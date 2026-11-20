"""Agent orchestration controller (ENGINEERED single-agent mode).

States:  INIT -> BASELINE_CAPTURED -> DEFECT_VERIFIED -> AGENT_TURN <-> (tool calls | PATCH_APPLIED) -> EVALUATING -> ACCEPTED
                                                                                       |-> REJECTED_CONTINUE -> AGENT_TURN
         any state -> STOPPED(reason)   (resumable: state.json + trace.jsonl)
Stop reasons: accepted | max_iterations | max_model_calls | max_tool_calls | wall_budget | cost_budget | repeated_identical_patch | no_progress | invalid_replies |
              provider_error | provider_usage_limit (resumable after the reset; the failed call is not charged) | defect_not_detected | baseline_unavailable | evaluation_blocked
The controller owns state; the model only returns one JSON action per turn. The model never touches files: its only effect on the design is `propose_patch`.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from ae.harness.checks import run_fast
from ae.harness.circuit import FE_AAF_001, CircuitProfile, resolve_callable
from ae.harness.contracts import ToolRegistry
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.designstate import canonical_state
from ae.harness.improvement import ACCEPT, PolicyError, edited_refs, load_policy
from ae.harness.providers import ModelProvider
from ae.harness.tasks import Task, create_defect
from ae.harness.tools import AgentContext, advisory_sanity, build_registry
from ae.harness.workspace import REPO_ROOT, Workspace, trusted_digest


class Mode(str, Enum):
    BASIC_SINGLE = "basic_single_agent_tools"
    ENGINEERED_SINGLE = "engineered_single_agent"
    COORDINATED_SPECIALISTS = "coordinated_specialists"


MODE_STATUS = {Mode.ENGINEERED_SINGLE: "implemented", Mode.BASIC_SINGLE: "NOT IMPLEMENTED (future)", Mode.COORDINATED_SPECIALISTS: "NOT IMPLEMENTED (future)"}


@dataclass
class Limits:
    max_iterations: int = 4              # evaluator submissions
    max_model_calls: int = 40
    max_tool_calls: int = 80
    model_timeout_s: float = 240.0
    wall_budget_s: float = 2400.0
    max_cost_usd: float | None = None    # enforced only when the provider reports cost; unknown cost cannot be enforced (recorded)
    identical_patch_limit: int = 2
    no_progress_limit: int = 2
    max_invalid_replies: int = 3         # restoration mode: CUMULATIVE. Improvement mode: CONSECUTIVE invalid replies (reset by any valid action); see max_invalid_replies_total
    max_invalid_replies_total: int = 12  # improvement mode: cumulative backstop (live runs of 30-45 calls emit an occasional prose reply; improve-live-7 ended on 3 scattered ones)
    max_refused_submits: int = 3         # improvement mode: zero-diff / duplicate submissions refused BEFORE evaluation; cumulative bound so a refuse-loop cannot run forever
    max_state_revisits: int = 6          # improvement mode: re-entering an already visited design state while exploring is legitimate; this bounds endless explore-revert cycling WITHOUT a submission (reset at every submission)
    progress_eps_db: float = 0.05        # improvement mode: a new best objective must beat the previous best by this much (10 % of the 0.5 dB target) to count as progress


_SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{12,}|(?i:api[_-]?key|token|secret)[\"'=: ]+[A-Za-z0-9._\-]{16,})")


def redact(text: str) -> str:
    text = _SECRET.sub("[REDACTED]", text)
    for k, v in os.environ.items():
        if v and len(v) >= 16 and any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            text = text.replace(v, "[REDACTED]")
    return text


SYSTEM_PROMPT = """You are the DESIGN AGENT in an electronics design harness. You can act ONLY by returning one JSON object per turn:
{"reasoning": "<short>", "action": {"tool": "<tool name>", "args": {...}}}
Return nothing but that JSON object. Tools (typed; invalid arguments are rejected):
{tools}
Rules: you cannot read or write files; the requirements, thresholds and evaluator are protected and cannot be changed. Your only design edit is `propose_patch` (scope: value and mpn_candidate of discrete R/C parts;
changing an MPN resets its verification status). Patch format: {{"summary": str, "base_sha256": <manifest_sha256 from get_design>, "ops": [{{"op":"set_field","part":"C1","field":"value","to":"..."}}]}}.
Use evidence (get_design, get_design_evidence, decode_mpn, run_check) to find the cause; make the SMALLEST justified change; re-run checks on your candidate; then call submit.
An independent evaluator runs the full pipeline against the protected requirements and may reject: if it does, you receive its findings. Some failures in the design may pre-date your task and are NOT yours to fix or hide.
Do not guess hidden answers; do not try to weaken checks. If unsure, gather evidence first."""


IMPROVEMENT_PROMPT = """You are the DESIGN AGENT in an electronics design harness. You can act ONLY by returning ONE JSON object per turn:
{"reasoning": "<short>", "action": {"tool": "<tool name>", "args": {...}}}
Return nothing but that single JSON object (two actions in one reply are rejected). Tools (typed; invalid arguments are rejected):
{tools}
Rules: you cannot read or write files; requirements, thresholds, the frozen policy and the evaluator are protected. Your only design edit is `propose_patch`
(scope: value and mpn_candidate of the parts named in the task's allowed_edits; changing an MPN resets its verification status; a value edit without a matching MPN edit fails the BOM check).
Patch format: {{"summary": str, "base_sha256": <manifest_sha256 from get_design>, "ops": [{{"op":"set_field","part":"R4","field":"value","to":"..."}}]}}.
The task has ONE objective with a minimum improvement and hard constraints; the evaluator re-runs the full pipeline over all tolerance corners and judges against the frozen policy. `ac_response` is nominal only. `run_check preview` is the recommended single check (ONE budget unit): it runs the corner sweep AND the nominal requirements and projects the policy rules (objective gain, corner limits, REQ-STAB, REQ-BIAS and the other guarded margins) as ok / WOULD_BE_REJECTED; this is an advisory projection from nominal and corner numbers, not a verdict, and it does not cover the BOM, status, series or row rules. `run_check edit_sanity` is a free, instant, advisory prediction (ideal op-amp model, no simulator, no budget) of your current design's corner gain window, flatness and stopband and of the R4/R5 stage-2 gain ratio; it can be off by a small margin (see the 'borderline' warnings) and cannot see REQ-STAB, bias or headroom. `run_check corner_preview` runs the evaluator's own worst-case sweep over ALL tolerance corners on your current design
(about a minute, advisory: the evaluator still decides and also applies further rules). `run_check requirements_preview` shows the nominal value of EVERY requirement, including REQ-STAB, bias, noise and settling,
which the non-regression rules guard and which corner_preview does not report (compare with the baseline values in the brief). Both previews are limited in number per task; use them before submitting. Pre-existing failures (e.g. REQ-STAB) are not yours to fix or hide and must not get worse. A rejection reports policy violations separately from failed circuit requirements.
Do not guess hidden answers; do not try to weaken checks. If you cannot find a justified improvement, call `declare_no_improvement` with your reasons: it ends the run, spends no evaluation and is recorded as its own outcome.
Submitting a design that is electrically identical to the baseline or to one already evaluated is refused before any evaluation (it costs you a turn, nothing else); exploring and reverting freely is fine.
If two evaluated designs in a row set neither a better worst-case objective nor fewer broken checks than before, a further submission ends the run instead of being evaluated."""


class _NonFinite(ValueError):
    pass


def _reject_constant(name: str):
    raise _NonFinite(name)


def _all_finite(obj) -> bool:
    if isinstance(obj, float):
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(_all_finite(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_all_finite(v) for v in obj)
    return True


def parse_action(text: str) -> tuple[dict | None, str | None]:
    """STRICT action parser. Exactly ONE JSON object with an `action` key may appear in a reply (prose around it is ignored, as seen in a live reply).
    Rejected with no side effects: no object, an object without `action`, two or more action objects (also inside an array), unknown keys, non-string tool, non-object args,
    NaN / Infinity / out-of-range floats. Nothing is dispatched on rejection."""
    decoder = json.JSONDecoder(parse_constant=_reject_constant)
    found, other, i = [], 0, 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, i)
        except _NonFinite as exc:
            return None, f"non-finite number ({exc}) is not allowed"
        except (ValueError, RecursionError):
            i += 1
            continue
        if isinstance(obj, dict) and "action" in obj:
            found.append(obj)
        else:
            other += 1
        i = end
    if len(found) > 1:
        return None, f"ambiguous: {len(found)} action objects in one reply (exactly one action per reply)"
    if not found:
        return None, "no JSON action object found" if not other else "JSON object has no 'action' key"
    obj = found[0]
    if not _all_finite(obj):
        return None, "non-finite number (inf/nan) is not allowed"
    act = obj["action"]
    if set(obj) - {"reasoning", "action"}:
        return None, f"unknown top-level key(s) {sorted(set(obj) - {'reasoning', 'action'})}"
    if not isinstance(act, dict) or set(act) - {"tool", "args"} or not isinstance(act.get("tool"), str) or not act["tool"] or not isinstance(act.get("args", {}), dict):
        return None, "action must be {tool: non-empty str, args: object}"
    return {"tool": act["tool"], "args": act.get("args", {}), "reasoning": str(obj.get("reasoning", ""))[:500]}, None


def guard_context(policy, baseline_record: dict) -> dict:
    """What the combined `preview` needs to project the frozen policy's rules (read-only data derived from the policy and the baseline record)."""
    from ae.harness.improvement import _by_id
    return {"policy_raw": policy.raw, "objective_check": policy.objective_check, "baseline_checks": _by_id(baseline_record),
            "rules": {"margin_fraction": policy.margin_fraction, "abs_tol": policy.abs_tol, "rel_tol": policy.rel_tol, "min_improvement": policy.min_improvement}}


def preview_spend(tool_call_event: dict) -> int:
    """Fresh preview budget units one recorded run_check tool_call spent (mirrors tools.run_check): a fresh (non cache-hit) corner_preview / requirements_preview that reached the
    sweep. 'error' outcomes were refunded; 'blocked' outcomes were not; tool errors (ok false, e.g. preview_budget_exhausted) never spent."""
    check = (tool_call_event.get("args") or {}).get("check")
    if check not in ("corner_preview", "requirements_preview", "preview"):
        return 0
    result = tool_call_event.get("result") or {}
    payload = result.get("result") or {}
    if check == "preview":                                    # combined: one unit, refunded only when the corner part never reached a sweep (tools._combined_preview)
        return int(bool(result.get("ok")) and payload.get("cache_hit") is False and (payload.get("corner_preview") or {}).get("outcome") != "error")
    return int(bool(result.get("ok")) and payload.get("cache_hit") is False and payload.get("outcome") != "error")


class Controller:
    def __init__(self, run_dir: Path, task: Task, provider: ModelProvider, evaluator: TrustedEvaluator, limits: Limits = Limits(), mode: Mode = Mode.ENGINEERED_SINGLE,
                 pristine_manifest: Path | None = None, baseline_record: dict | None = None, checks_fn: Callable = run_fast, evaluate_defect: bool = False, repo: Path = REPO_ROOT, baseline_dir: Path | None = None):
        if MODE_STATUS[mode] != "implemented":
            raise NotImplementedError(f"mode {mode.value} is {MODE_STATUS[mode]}; only {Mode.ENGINEERED_SINGLE.value} exists")
        self.run_dir, self.task, self.provider, self.evaluator, self.limits, self.mode = run_dir, task, provider, evaluator, limits, mode
        self.repo, self.pristine = repo, pristine_manifest or repo / FE_AAF_001.baseline_design
        self._pristine_given = pristine_manifest is not None
        self.profile: CircuitProfile = FE_AAF_001          # replaced by the policy's profile once an improvement policy is loaded (_adopt_profile)
        self.baseline_record, self.checks_fn, self.evaluate_defect = baseline_record, checks_fn, evaluate_defect
        self.baseline_dir = baseline_dir or run_dir / "baseline"
        self.policy = None                      # frozen ImprovementPolicy (improvement tasks only; loaded + verified in prepare)
        self.t0 = time.monotonic()
        self.seq = 0
        self.state = {"state": "INIT", "iteration": 0, "model_calls": 0, "tool_calls": 0, "evaluations": 0, "invalid_replies": 0, "identical_patch_hits": 0, "no_progress": 0, "refused_submits": 0, "pending_feedback": False, "best_objective": 0.0, "best_infeasible_checks": None, "evaluated_states": [], "visited_states": [], "state_revisits": 0, "baseline_state": None,
                      "stop_reason": None, "evaluated_manifest_sha256": [], "patch_fingerprints": [], "best_violation_count": None,
                      "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd_reported": 0.0, "cost_unknown_calls": 0, "model_seconds": 0.0, "tool_seconds": 0.0, "eval_seconds": 0.0},
                      "provider": provider.name, "fixture": provider.fixture, "mode": mode.value}
        self.messages: list[dict] = []

    def _adopt_profile(self) -> None:
        """Take the circuit profile from the loaded policy. FE keeps every default untouched; for another circuit the baseline design and the checks function default to the profile's
        (an explicitly passed pristine_manifest / checks_fn always wins), and the evaluator must have been built for the same circuit (its digest must cover the profile's trusted globs)."""
        self.profile = self.policy.profile
        if self.profile.id == FE_AAF_001.id:
            return
        if not self._pristine_given:
            self.pristine = self.repo / self.profile.baseline_design
        if self.checks_fn is run_fast:
            self.checks_fn = resolve_callable(self.profile.checks)

    # ---------------------------------------------------------------- recording
    def _event(self, type_: str, **data) -> None:
        self.seq += 1
        with (self.run_dir / "trace.jsonl").open("a") as fh:
            fh.write(redact(json.dumps({"seq": self.seq, "t": round(time.monotonic() - self.t0, 3), "type": type_, **data}, default=str)) + "\n")

    def _transition(self, new: str, **why) -> None:
        self._event("transition", **{"from": self.state["state"], "to": new}, **why)
        self.state["state"] = new
        self._save()

    def _save(self) -> None:
        if getattr(self, "ctx", None) is not None:
            self.state["corner_previews_used"] = self.ctx.corner_previews      # the preview budget is per task, so it must survive a resume (live-8 got 12 more after resuming)
        (self.run_dir / "state.json").write_text(json.dumps(self.state, indent=1, default=str))

    def _stop(self, reason: str, **extra) -> dict:
        """Every stop states whether the newest rejection feedback reached the agent. `pending_feedback` is True from the moment feedback is built until a model call is made with it in the transcript."""
        self.state["stop_reason"] = reason
        self.state["final_feedback_delivered"] = (not self.state.get("pending_feedback")) if self.state.get("had_feedback") else None
        self._transition("STOPPED", reason=reason, feedback_delivered=self.state["final_feedback_delivered"], **extra)
        return self._summary()

    def _summary(self) -> dict:
        u = self.state["usage"]
        s = {"run_dir": str(self.run_dir), "task": self.task.id, "split": self.task.split, "mode": self.mode.value, "provider": self.provider.name, "fixture": self.provider.fixture,
             "task_kind": self.task.kind, "final_feedback_delivered": self.state.get("final_feedback_delivered"), "refused_submits": self.state.get("refused_submits", 0), "final_state": self.state["state"], "stop_reason": self.state["stop_reason"], "iterations": self.state["iteration"], "model_calls": self.state["model_calls"],
             "tool_calls": self.state["tool_calls"], "evaluations": self.state["evaluations"], "elapsed_s": round(time.monotonic() - self.t0, 1),
             "usage": {**u, "cost_usd_reported": (u["cost_usd_reported"] if u["cost_unknown_calls"] == 0 else None),
                       "cost_note": "cost is a provider-reported list-basis estimate; unknown calls make the total unknown (None), never 0"},
             "live_agent_success": bool(self.state["stop_reason"] == "accepted" and not self.provider.fixture),
             "accepted_by_independent_evaluator": self.state["stop_reason"] == "accepted"}
        (self.run_dir / "summary.json").write_text(json.dumps(s, indent=1, default=str))
        return s

    # ---------------------------------------------------------------- phases
    def prepare(self) -> dict | None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.task.kind == "improvement":                  # the policy names the circuit, which decides the baseline design hashed into task.json (a bad policy is reported below)
            try:
                self.policy = load_policy(Path(self.task.policy_path), self.repo)
                self._adopt_profile()
            except PolicyError:
                self.policy = None
        (self.run_dir / "task.json").write_text(json.dumps({**asdict(self.task), "limits": asdict(self.limits), "mode_status": MODE_STATUS[self.mode], "trusted_digest": self.evaluator.digest_at_start["combined"],
                                                            "pristine_manifest_sha256": __import__("hashlib").sha256(self.pristine.read_bytes()).hexdigest()}, indent=1))
        if self.task.kind == "improvement":
            try:
                self.policy = load_policy(Path(self.task.policy_path), self.repo)
            except PolicyError as exc:
                return self._stop("policy_blocked", detail=str(exc))
            self._adopt_profile()
            missing_globs = [g for g in self.profile.extra_trusted_globs if g not in getattr(self.evaluator, "extra_globs", ())]
            if missing_globs:
                return self._stop("policy_blocked", detail=f"evaluator was not built for circuit {self.profile.id!r}: trusted globs {missing_globs} are not in its digest")
            (self.run_dir / "policy_frozen.yaml").write_text(Path(self.task.policy_path).read_text())
        if self.baseline_record is None:
            try:
                self.baseline_record = self.evaluator.run_pipeline(self.pristine, self.baseline_dir)
            except Exception as exc:
                return self._stop("baseline_unavailable", error=f"{type(exc).__name__}: {exc}")
        (self.run_dir / "baseline_record.json").write_text(json.dumps(self.baseline_record, indent=1))
        self._transition("BASELINE_CAPTURED", baseline_summary=self.baseline_record["summary"])
        if self.policy is not None:
            return self._prepare_improvement()
        self.ws = Workspace(self.run_dir / "workspace")
        defect_manifest = self.run_dir / "defect_manifest.yaml"
        defect = create_defect(self.task, self.pristine, defect_manifest)
        (self.run_dir / "defect.json").write_text(json.dumps(defect, indent=1))
        self.ws.init(defect_manifest)
        self.ctx = AgentContext(self.ws, self.run_dir / "scratch", failure_report={}, checks_fn=self.checks_fn, repo=self.repo, trusted_combined=self.evaluator.digest_at_start["combined"])
        fast = self.checks_fn(["bom_consistency", "ac_response"], self.ws.manifest, self.ctx.scratch / "defect_fast")
        self.ctx.failure_report = {"design_manifest_sha256": self.ws.sha256(), "fast_check_results": fast,
                                  "note": "measured on the design you were given by the real checks; some failures may pre-date your task"}
        (self.run_dir / "defect_failure_report.json").write_text(json.dumps(self.ctx.failure_report, indent=1, default=str))
        if fast.get("bom_consistency", {}).get("outcome") != "failed":
            return self._stop("defect_not_detected", detail=str(fast.get("bom_consistency")))
        if self.evaluate_defect:
            d = self.evaluator.evaluate(self.ws.manifest, self.run_dir / "defect_evaluation", self.baseline_record)
            (self.run_dir / "defect_evaluation.json").write_text(json.dumps(d, indent=1, default=str))
            self._event("defect_evaluation", verdict=d.get("verdict"), n_violations=len(d.get("violations", [])))
            if d.get("verdict") != "rejected":
                return self._stop("defect_not_detected", detail=f"full evaluation verdict {d.get('verdict')}")
        self._transition("DEFECT_VERIFIED", defect=defect, failing=fast["bom_consistency"]["outcome"])
        self.registry: ToolRegistry = build_registry(self.ctx)
        specs = "\n".join(json.dumps(s.schema()) for s in self.registry.specs())
        self.system = SYSTEM_PROMPT.replace("{tools}", specs)
        self.messages = [{"role": "user", "content": f"TASK ({self.task.id}): {self.task.description}\nMeasured failures of the design you were given:\n{json.dumps(self.ctx.failure_report, indent=1, default=str)}\n"
                                                    f"Begin. Reply with one JSON action."}]
        self._event("message", role="user", content=self.messages[0]["content"])
        return None

    def _prepare_improvement(self) -> dict | None:
        pol = self.policy
        if hashlib.sha256(self.pristine.read_bytes()).hexdigest() != pol.raw["baseline"]["components_sha256"]:
            return self._stop("policy_blocked", detail="pristine manifest differs from the frozen baseline")
        self.ws = Workspace(self.run_dir / "workspace", scope=pol.scope())
        self.ws.init(self.pristine)                                        # improvement starts FROM the adopted reference: there is no seeded defect
        self.ctx = AgentContext(self.ws, self.run_dir / "scratch", failure_report={}, checks_fn=self.checks_fn, repo=self.repo, trusted_combined=self.evaluator.digest_at_start["combined"], improvement=True, profile=self.profile)
        self.state["baseline_state"] = canonical_state(self.ws.text())
        self.state["visited_states"] = [self.state["baseline_state"]]
        self.ctx.guard = guard_context(pol, self.baseline_record)
        self.ctx.baseline_manifest_text = self.pristine.read_text()
        by = {c["id"]: c for c in self.baseline_record["checks"]}
        keys = [pol.objective_check, *self.profile.brief_keys]
        brief = {"design_manifest_sha256": self.ws.sha256(), "policy_id": pol.task_id, "policy_version": pol.version, "policy_sha256": pol.sha256,
                 "objective": pol.raw["objective"], "hard_constraints": pol.raw["hard_constraints"], "allowed_edits": pol.raw["allowed_edits"], "non_regression": pol.raw["nonregression"],
                 "claims_never_made": pol.raw["claims_never_made"],
                 "baseline_measurements": {k: {"outcome": by[k]["outcome"], "value": by[k].get("value"), "limit": by[k].get("limit"), "margin": by[k].get("margin")} for k in keys},
                 "fast_check_results": self.checks_fn(["bom_consistency", "ac_response"], self.ws.manifest, self.ctx.scratch / "baseline_fast"),
                 "note": "baseline measured by the trusted pipeline; the evaluator re-runs the FULL pipeline (all corners) on your design; ac_response is nominal only; run_check corner_preview / requirements_preview show the evaluator's own corner and per-requirement numbers (limited uses)"}
        self.ctx.failure_report = brief
        (self.run_dir / "task_brief.json").write_text(json.dumps(brief, indent=1, default=str))
        self._transition("TASK_READY", task=pol.task_id, policy_sha256=pol.sha256[:12])
        self.registry = build_registry(self.ctx)
        specs = "\n".join(json.dumps(sp.schema()) for sp in self.registry.specs())
        self.system = IMPROVEMENT_PROMPT.replace("{tools}", specs)
        self.messages = [{"role": "user", "content": f"TASK ({self.task.id}): {self.task.description}\nFrozen objective, constraints and baseline:\n{json.dumps(brief, indent=1, default=str)[:12000]}\nBegin. Reply with one JSON action."}]
        self._event("message", role="user", content=self.messages[0]["content"])
        return None

    def _transcript(self) -> str:
        return "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in self.messages)

    def _parse(self, text: str):
        return parse_action(text)

    def _budget_stop(self) -> dict | None:
        u, L = self.state["usage"], self.limits
        if time.monotonic() - self.t0 > L.wall_budget_s:
            return self._stop("wall_budget")
        if self.state["model_calls"] >= L.max_model_calls:
            return self._stop("max_model_calls")
        if self.state["tool_calls"] >= L.max_tool_calls:
            return self._stop("max_tool_calls")
        if L.max_cost_usd is not None and u["cost_unknown_calls"] == 0 and u["cost_usd_reported"] >= L.max_cost_usd:
            return self._stop("cost_budget")
        return None

    def run(self) -> dict:
        early = self.prepare()
        if early:
            return early
        return self._loop()

    def _loop(self) -> dict:
        self._transition("AGENT_TURN")
        while True:
            stop = self._budget_stop()
            if stop:
                return stop
            feedback_pending_before_call = self.state.get("pending_feedback", False)
            self.state["pending_feedback"] = False                                 # this call carries every message built so far, including the newest feedback
            reply = self.provider.complete(self.system, self._transcript(), self.limits.model_timeout_s)
            u = self.state["usage"]
            self.state["model_calls"] += 1
            u["model_seconds"] += reply.elapsed_s
            if reply.usage:
                u["input_tokens"] += (reply.usage.get("input_tokens") or 0)
                u["output_tokens"] += (reply.usage.get("output_tokens") or 0)
            if reply.usage_limit:
                self.state["model_calls"] -= 1                                     # a call refused by the usage limit consumed none of the run's own budget (model_seconds still counts)
                self.state["pending_feedback"] = feedback_pending_before_call      # the refused call never carried the feedback to the agent
            elif reply.cost_usd is None:
                u["cost_unknown_calls"] += 1
            else:
                u["cost_usd_reported"] += reply.cost_usd
            self._event("model_reply", text=reply.text, error=reply.error, usage=reply.usage, cost_usd=reply.cost_usd, cost_basis=reply.cost_basis, model=reply.model, elapsed_s=round(reply.elapsed_s, 2))
            if reply.usage_limit:
                self.state["usage_limit"] = reply.usage_limit
                self.state["usage_limit_hits"] = self.state.get("usage_limit_hits", 0) + 1
                self._event("provider_usage_limit", **reply.usage_limit)
                return self._stop("provider_usage_limit", reset_at_iso=reply.usage_limit.get("reset_at_iso"), reset_text=reply.usage_limit.get("reset_text"))
            if reply.error:
                return self._stop("provider_error", error=reply.error)
            self.messages.append({"role": "assistant", "content": reply.text})
            action, err = self._parse(reply.text)
            if action is None:
                self.state["invalid_replies"] += 1
                self.state["consecutive_invalid"] = self.state.get("consecutive_invalid", 0) + 1
                self._event("invalid_reply", error=err, consecutive=self.state["consecutive_invalid"])
                if self.policy is None:
                    if self.state["invalid_replies"] >= self.limits.max_invalid_replies:
                        return self._stop("invalid_replies")
                elif self.state["consecutive_invalid"] >= self.limits.max_invalid_replies or self.state["invalid_replies"] >= self.limits.max_invalid_replies_total:
                    return self._stop("invalid_replies", consecutive=self.state["consecutive_invalid"], total=self.state["invalid_replies"])
                self._feedback({"ok": False, "error": {"code": "invalid_reply", "message": f"could not parse your reply: {err}. Reply with ONE JSON object {{\"reasoning\":..,\"action\":{{\"tool\":..,\"args\":{{..}}}}}}"}})
                continue
            self.state["consecutive_invalid"] = 0
            if action["tool"] == "submit":
                result = self._submit(action["args"])
                if result is not None:
                    return result
                continue
            if action["tool"] == "declare_no_improvement" and self.policy is not None:
                summary = str(action["args"].get("summary", ""))[:1000]
                self._event("no_improvement_declared", summary=summary, evaluations_spent=self.state["evaluations"])
                return self._stop("no_improvement_declared", summary=summary)
            self.state["tool_calls"] += 1
            res = self.registry.call(action["tool"], action["args"])
            self.state["usage"]["tool_seconds"] += res.elapsed_s
            self._event("tool_call", tool=action["tool"], args=action["args"], result=res.to_json())
            if action["tool"] == "propose_patch" and res.ok:
                fp = res.result["patch"]["fingerprint"]
                if self.policy is None and fp in self.state["patch_fingerprints"]:         # restoration mode: operation-based rule, unchanged
                    self.state["identical_patch_hits"] += 1
                self.state["patch_fingerprints"].append(fp)
                self._transition("PATCH_APPLIED", patch_seq=res.result["patch"]["seq"], after_sha256=res.result["patch"]["after_sha256"][:12])
                self._transition("AGENT_TURN")
                if self.policy is None and self.state["identical_patch_hits"] >= self.limits.identical_patch_limit:
                    return self._stop("repeated_identical_patch")
                if self.policy is not None:                                                  # improvement mode: protection is by STATE, not by operations
                    cs = canonical_state(self.ws.text())
                    if cs in self.state["visited_states"]:
                        self.state["state_revisits"] += 1
                        self._event("state_revisit", revisits=self.state["state_revisits"], is_baseline=cs == self.state["baseline_state"])
                        if self.state["state_revisits"] >= self.limits.max_state_revisits:           # only a revisit can trip the cap (a novel design after a resume does not)
                            return self._stop("exploration_cycling", detail=f"{self.state['state_revisits']} returns to already visited design states without a submission")
                    else:
                        self.state["visited_states"].append(cs)
            elif action["tool"] == "propose_patch" and not res.ok:
                pass
            self._feedback(res.to_json())

    def _feedback(self, payload: dict) -> None:
        text = json.dumps(payload, default=str)
        self.state["pending_feedback"], self.state["had_feedback"] = True, True
        content = f"TOOL RESULT:\n{text[:6000]}"
        self.messages.append({"role": "user", "content": content})
        self._event("message", role="user", content=content)                 # the EXACT text the agent saw, so resume rebuilds an identical transcript
        self._save()

    def _refuse_submit(self, code: str, message: str, sha: str):
        """Improvement mode: a submission that would evaluate nothing new is refused BEFORE any evaluation. It costs the model call that produced it (already counted) and nothing else:
        no iteration, no evaluation, no effect on the evaluated-design progress counters. A separate cumulative counter bounds repeated refusals."""
        self.state["refused_submits"] += 1
        self._event("submit_refused", code=code, manifest_sha256=sha, refused_submits=self.state["refused_submits"])
        if self.state["refused_submits"] >= self.limits.max_refused_submits:
            return self._stop("repeated_refusals", detail=f"{self.state['refused_submits']} submissions refused before evaluation ({code})")
        self._feedback({"ok": False, "error": {"code": code, "message": message}, "refused_submits": f"{self.state['refused_submits']} of {self.limits.max_refused_submits} allowed before the run stops",
                        "evaluations_spent": self.state["evaluations"], "note": "nothing was evaluated and no evaluation was spent"})
        return None

    def _improvement_progress(self, decision: dict) -> bool:
        """Improvement-mode progress for one EVALUATED rejection (final semantics, see docs/agent-improvement-mode.md section 8).
        Two axes, tracked separately; a design is progress if it sets a new best on EITHER:
          objective movement: objective.improvement (dB, larger is better) beats the best so far by more than `progress_eps_db`;
          feasibility: the number of DISTINCT checks that break a non-objective rule is smaller than the fewest seen so far
                       (one gain-corner breach that surfaces as both H3 and H7 is ONE check, not two violations).
        The objective best starts at the unchanged baseline (improvement 0.0), so an evaluated no-op is never progress. The feasibility best starts empty: the first evaluated design only sets it.
        Raw violation counts are never compared, and refused submissions never reach this function."""
        obj = (decision.get("objective") or {}).get("improvement")
        broken = {v.get("check") for v in decision.get("policy_violations", []) if v.get("rule") != "objective_improvement"}
        better_objective = obj is not None and obj > self.state["best_objective"] + self.limits.progress_eps_db
        best_broken = self.state.get("best_infeasible_checks")
        better_feasibility = best_broken is not None and len(broken) < best_broken
        if obj is not None:
            self.state["best_objective"] = max(self.state["best_objective"], obj)
        self.state["best_infeasible_checks"] = len(broken) if best_broken is None else min(best_broken, len(broken))
        self._event("progress_assessment", objective_improvement=obj, broken_checks=sorted(c for c in broken if c), better_objective=better_objective, better_feasibility=better_feasibility,
                    best_objective=self.state["best_objective"], best_infeasible_checks=self.state["best_infeasible_checks"])
        return better_objective or better_feasibility

    def _submit(self, args: dict):
        sha = self.ws.sha256()
        if self.policy is not None:
            self.state["state_revisits"] = 0                                          # a submission attempt (even a refused one) ends an exploration streak
            cs = canonical_state(self.ws.text())
            if cs == self.state["baseline_state"]:
                return self._refuse_submit("no_change_from_baseline", "the design is electrically identical to the adopted baseline (same values and part numbers; verification-status bookkeeping is ignored); change it, or call declare_no_improvement", sha)
            if cs in self.state["evaluated_states"]:
                return self._refuse_submit("already_evaluated", "an electrically identical design (same values and part numbers) was already evaluated and rejected; change the design before submitting again", sha)
            if self.state["no_progress"] >= self.limits.no_progress_limit:           # checked HERE, after the agent has had the chance to read the last feedback
                return self._stop("no_progress", detail=f"{self.state['no_progress']} consecutive evaluated designs set no new best objective and no new best feasibility")
        if sha in self.state["evaluated_manifest_sha256"]:
            self.state["no_progress"] += 1
            self._event("submit_rejected_duplicate", manifest_sha256=sha)
            if self.state["no_progress"] >= self.limits.no_progress_limit:
                return self._stop("no_progress", detail="design identical to one already evaluated")
            self._feedback({"ok": False, "error": {"code": "already_evaluated", "message": "this exact design was already evaluated and rejected; change the design before submitting again"}})
            return None
        sanity = advisory_sanity(self.ctx) if self.policy is not None else None     # ADVISORY only: recorded, never gates the submission
        if sanity is not None:
            self._event("sanity_advisory", status=sanity["status"], codes=[w["code"] for w in sanity["warnings"]], warnings=sanity["warnings"])
        self.state["iteration"] += 1
        self._transition("EVALUATING", iteration=self.state["iteration"], manifest_sha256=sha[:12])
        ev_dir = self.run_dir / "evaluations" / f"iter_{self.state['iteration']:02d}"
        t = time.monotonic()
        if self.policy is not None:
            decision = self.evaluator.evaluate_improvement(self.ws.manifest, ev_dir, self.baseline_record, self.baseline_dir, self.policy, self.pristine.read_text())
        else:
            decision = self.evaluator.evaluate(self.ws.manifest, ev_dir, self.baseline_record)
        self.state["usage"]["eval_seconds"] += time.monotonic() - t
        self.ctx.last_evaluated_manifest_text = self.ws.text()
        self.state["evaluations"] += 1
        if decision["verdict"] != "blocked":                              # a blocked evaluation never judged the design: it must stay resubmittable after a resume
            self.state["evaluated_manifest_sha256"].append(sha)
            if self.policy is not None:
                self.state["evaluated_states"].append(canonical_state(self.ws.text()))
        ev_dir.parent.mkdir(parents=True, exist_ok=True)
        (ev_dir.parent / f"decision_{self.state['iteration']:02d}.json").write_text(json.dumps(decision, indent=1, default=str))
        self._event("evaluation", iteration=self.state["iteration"], verdict=decision["verdict"], violations=decision.get("violations"), reason=decision.get("reason"))
        if decision["verdict"] in ("accepted", ACCEPT):
            self._finalize(decision)
            self.state["stop_reason"] = "accepted"
            self._transition("ACCEPTED", iteration=self.state["iteration"])
            return self._summary()
        if decision["verdict"] == "blocked":
            return self._stop("evaluation_blocked", detail=decision.get("reason"))
        if self.policy is not None:
            progressed = self._improvement_progress(decision)
            self.state["no_progress"] = 0 if progressed else self.state["no_progress"] + 1
            n = len(decision["violations"])
        else:                                                                       # restoration mode: unchanged semantics (violation-count comparison)
            n = len(decision["violations"])
            best = self.state["best_violation_count"]
            self.state["no_progress"] = self.state["no_progress"] + 1 if (best is not None and n >= best) else 0
            self.state["best_violation_count"] = n if best is None else min(best, n)
        self._transition("REJECTED_CONTINUE", violations=n, no_progress=self.state["no_progress"])
        findings = [{k: v for k, v in x.items() if k not in ("candidate_detail",)} | ({"detail": x["candidate_detail"][:200]} if "candidate_detail" in x else {}) for x in decision["violations"]][:12]
        extra = {"objective": decision.get("objective"), "circuit_requirement_failures": decision.get("circuit_requirement_failures")} if self.policy is not None else {}
        payload = {"ok": False, "evaluator_verdict": "rejected", "findings": findings, **extra, "unresolved_baseline_failures_not_yours": decision["unresolved_baseline_failures"],
                   "note": "fresh evidence was produced by the protected pipeline; fix the cause of the findings, do not guess"}
        if sanity is not None and sanity["warnings"]:
            payload["advisory_sanity"] = sanity                                         # ideal-model view of the design just rejected; explains gain/flatness findings, changes nothing
        # FEEDBACK BEFORE STOP: the rejection's feedback is built and delivered first; stop conditions are checked afterwards. If the evaluation budget is spent there is no further
        # agent turn to deliver it to: it is recorded as built-but-undelivered (and the stop says so) instead of being silently dropped. No extra model call is ever made to deliver it.
        if self.state["iteration"] >= self.limits.max_iterations:
            self._event("feedback_undelivered", reason="max_iterations reached: no further agent turn", payload=json.dumps(payload, default=str)[:6000])
            self.state["had_feedback"], self.state["pending_feedback"] = True, True
            return self._stop("max_iterations")
        self._feedback(payload)
        if self.policy is None and self.state["no_progress"] >= self.limits.no_progress_limit:
            return self._stop("no_progress", detail="violation count did not improve")
        self._transition("AGENT_TURN")
        return None

    def _finalize(self, decision: dict) -> None:
        out = self.run_dir / "output"
        out.mkdir(exist_ok=True)
        if self.policy is not None:
            shutil.copy(self.ws.manifest, out / "components.yaml")
            (out / "candidate_vs_adopted_reference.diff").write_text("".join(difflib.unified_diff(self.pristine.read_text().splitlines(True), self.ws.text().splitlines(True),
                                                                                                 self.profile.labels["reference_path"], "candidate/components.yaml")))
            (out / "patches.json").write_text(json.dumps(self.ws.history, indent=1))
            (out / "evaluator_decision.json").write_text(json.dumps(decision, indent=1, default=str))
            (out / "README.txt").write_text(f"ACCEPTED FOR THE FROZEN OBJECTIVE ONLY ({self.profile.labels['improvement_readme_task']}, policy sha256 " + self.policy.sha256[:12] + "). Isolated output; the adopted reference was NOT modified; merging is a human decision. "
                                            + self.profile.labels["improvement_readme_caveats"]
                                            + "; ".join(c["claim"] for c in decision.get("blocked_claims", [])) + "\n")
            return
        shutil.copy(self.ws.manifest, out / "components.yaml")
        pristine, defective, final = self.pristine.read_text().splitlines(True), (self.run_dir / "defect_manifest.yaml").read_text().splitlines(True), self.ws.text().splitlines(True)
        (out / "agent_patch_vs_defect.diff").write_text("".join(difflib.unified_diff(defective, final, "defect/components.yaml", "candidate/components.yaml")))
        (out / "candidate_vs_adopted_reference.diff").write_text("".join(difflib.unified_diff(pristine, final, self.profile.labels["reference_path"], "candidate/components.yaml")))
        (out / "patches.json").write_text(json.dumps(self.ws.history, indent=1))
        (out / "evaluator_decision.json").write_text(json.dumps(decision, indent=1, default=str))
        (out / "README.txt").write_text(f"Integrated result in an ISOLATED output workspace. The adopted reference ({self.profile.labels['reference_path']}) was NOT modified. "
                                        "Review agent_patch_vs_defect.diff / patches.json; merging is a human decision. release_ready is false: unresolved baseline failures remain "
                                        f"({', '.join(decision['unresolved_baseline_failures'])}).\n")

    @classmethod
    def resume(cls, run_dir: Path, **kw) -> "Controller":
        """Reload a stopped run: state counters, workspace and transcript (from the trace) are restored; limits may be raised in kw['limits']."""
        c = cls(run_dir, **kw)
        c.state = json.loads((run_dir / "state.json").read_text())
        if c.state["state"] == "ACCEPTED":
            raise RuntimeError(f"run {run_dir} already ended in state ACCEPTED (stop_reason={c.state['stop_reason']!r}); a finished run cannot be resumed")
        previous_stop = c.state.get("stop_reason")
        c.state["stop_reason"] = None
        for key, default in (("refused_submits", 0), ("pending_feedback", False), ("best_objective", 0.0), ("best_infeasible_checks", None), ("evaluated_states", []), ("visited_states", []), ("state_revisits", 0), ("baseline_state", None)):
            c.state.setdefault(key, default)                                    # state files written before the feedback-loop fix lack these keys
        c.baseline_record = json.loads((run_dir / "baseline_record.json").read_text())
        improvement = c.task.kind == "improvement"
        if improvement:
            c.policy = load_policy(Path(c.task.policy_path), c.repo)
        if improvement:
            c._adopt_profile()
        c.ws = Workspace(run_dir / "workspace", scope=c.policy.scope() if improvement else None)
        c.ws.history = [json.loads(p.read_text()) for p in sorted((run_dir / "workspace" / "patches").glob("*.json"))]
        report = json.loads((run_dir / ("task_brief.json" if improvement else "defect_failure_report.json")).read_text())
        c.ctx = AgentContext(c.ws, run_dir / "scratch", failure_report=report, checks_fn=c.checks_fn, repo=c.repo, trusted_combined=c.evaluator.digest_at_start["combined"], improvement=improvement, profile=c.profile)
        if improvement and c.state["baseline_state"] is None:                       # state files written before the state-based protection
            c.state["baseline_state"] = canonical_state(c.pristine.read_text())
            c.state["visited_states"] = c.state["visited_states"] or [c.state["baseline_state"]]
        if improvement:
            c.ctx.guard = guard_context(c.policy, c.baseline_record)
            c.ctx.baseline_manifest_text = c.pristine.read_text()
        c.registry = build_registry(c.ctx)
        c.system = (IMPROVEMENT_PROMPT if improvement else SYSTEM_PROMPT).replace("{tools}", "\n".join(json.dumps(sp.schema()) for sp in c.registry.specs()))
        c.messages, last, previews_spent = [], 0, 0
        for line in (run_dir / "trace.jsonl").read_text().splitlines():
            e = json.loads(line)
            last = e["seq"]
            if e["type"] == "tool_call" and e.get("tool") == "run_check":
                previews_spent += preview_spend(e)
            if e["type"] == "message":
                c.messages.append({"role": e["role"], "content": e["content"]})
            elif e["type"] == "feedback_undelivered":                      # built after the evaluation budget ended; a resumed run (with raised limits) delivers it now
                c.messages.append({"role": "user", "content": "TOOL RESULT:\n" + e["payload"]})
            elif e["type"] == "model_reply" and not e.get("error"):
                c.messages.append({"role": "assistant", "content": e["text"]})
        c.seq = last
        if "corner_previews_used" not in c.state:                                  # state files written before this key: rebuild the spend from the trace (key present wins)
            c.state["corner_previews_used"] = previews_spent
        c.ctx.corner_previews = int(c.state["corner_previews_used"])
        c.messages.append({"role": "user", "content": "(run resumed by the controller with the limits shown in task.json; continue from where you stopped)"})
        if c.state["state"] == "EVALUATING" and not (run_dir / "evaluations" / f"decision_{c.state['iteration']:02d}.json").exists():
            c.state["interrupted_evaluations"] = c.state.get("interrupted_evaluations", 0) + 1       # process died mid-evaluation: the iteration stays charged, no verdict exists
            c._event("evaluation_interrupted", iteration=c.state["iteration"], note="no decision file; partial evidence directory is never reused")
        c._event("resumed", from_state=c.state["state"], **({"resumed_after": previous_stop} if previous_stop == "provider_usage_limit" else {}))
        return c

    def continue_run(self) -> dict:
        return self._loop()

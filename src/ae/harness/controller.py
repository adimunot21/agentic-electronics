"""Agent orchestration controller (ENGINEERED single-agent mode).

States:  INIT -> BASELINE_CAPTURED -> DEFECT_VERIFIED -> AGENT_TURN <-> (tool calls | PATCH_APPLIED) -> EVALUATING -> ACCEPTED
                                                                                       |-> REJECTED_CONTINUE -> AGENT_TURN
         any state -> STOPPED(reason)   (resumable: state.json + trace.jsonl)
Stop reasons: accepted | max_iterations | max_model_calls | max_tool_calls | wall_budget | cost_budget | repeated_identical_patch | no_progress | invalid_replies |
              provider_error | defect_not_detected | baseline_unavailable | evaluation_blocked
The controller owns state; the model only returns one JSON action per turn. The model never touches files: its only effect on the design is `propose_patch`.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from ae.harness.checks import run_fast
from ae.harness.contracts import ToolRegistry
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ModelProvider
from ae.harness.tasks import Task, create_defect
from ae.harness.tools import AgentContext, build_registry
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
    max_invalid_replies: int = 3


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


class Controller:
    def __init__(self, run_dir: Path, task: Task, provider: ModelProvider, evaluator: TrustedEvaluator, limits: Limits = Limits(), mode: Mode = Mode.ENGINEERED_SINGLE,
                 pristine_manifest: Path | None = None, baseline_record: dict | None = None, checks_fn: Callable = run_fast, evaluate_defect: bool = False, repo: Path = REPO_ROOT):
        if MODE_STATUS[mode] != "implemented":
            raise NotImplementedError(f"mode {mode.value} is {MODE_STATUS[mode]}; only {Mode.ENGINEERED_SINGLE.value} exists")
        self.run_dir, self.task, self.provider, self.evaluator, self.limits, self.mode = run_dir, task, provider, evaluator, limits, mode
        self.repo, self.pristine = repo, pristine_manifest or repo / "designs/fe-aaf-001/components.yaml"
        self.baseline_record, self.checks_fn, self.evaluate_defect = baseline_record, checks_fn, evaluate_defect
        self.t0 = time.monotonic()
        self.seq = 0
        self.state = {"state": "INIT", "iteration": 0, "model_calls": 0, "tool_calls": 0, "evaluations": 0, "invalid_replies": 0, "identical_patch_hits": 0, "no_progress": 0,
                      "stop_reason": None, "evaluated_manifest_sha256": [], "patch_fingerprints": [], "best_violation_count": None,
                      "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd_reported": 0.0, "cost_unknown_calls": 0, "model_seconds": 0.0, "tool_seconds": 0.0, "eval_seconds": 0.0},
                      "provider": provider.name, "fixture": provider.fixture, "mode": mode.value}
        self.messages: list[dict] = []

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
        (self.run_dir / "state.json").write_text(json.dumps(self.state, indent=1, default=str))

    def _stop(self, reason: str, **extra) -> dict:
        self.state["stop_reason"] = reason
        self._transition("STOPPED", reason=reason, **extra)
        return self._summary()

    def _summary(self) -> dict:
        u = self.state["usage"]
        s = {"run_dir": str(self.run_dir), "task": self.task.id, "split": self.task.split, "mode": self.mode.value, "provider": self.provider.name, "fixture": self.provider.fixture,
             "final_state": self.state["state"], "stop_reason": self.state["stop_reason"], "iterations": self.state["iteration"], "model_calls": self.state["model_calls"],
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
        (self.run_dir / "task.json").write_text(json.dumps({**asdict(self.task), "limits": asdict(self.limits), "mode_status": MODE_STATUS[self.mode], "trusted_digest": self.evaluator.digest_at_start["combined"],
                                                            "pristine_manifest_sha256": __import__("hashlib").sha256(self.pristine.read_bytes()).hexdigest()}, indent=1))
        if self.baseline_record is None:
            try:
                self.baseline_record = self.evaluator.run_pipeline(self.pristine, self.run_dir / "baseline")
            except Exception as exc:
                return self._stop("baseline_unavailable", error=f"{type(exc).__name__}: {exc}")
        (self.run_dir / "baseline_record.json").write_text(json.dumps(self.baseline_record, indent=1))
        self._transition("BASELINE_CAPTURED", baseline_summary=self.baseline_record["summary"])
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

    def _transcript(self) -> str:
        return "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in self.messages)

    def _parse(self, text: str):
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None, "no JSON object found"
        try:
            obj = json.loads(m.group(0))
            act = obj["action"]
            assert isinstance(act["tool"], str) and isinstance(act.get("args", {}), dict)
            return {"tool": act["tool"], "args": act.get("args", {}), "reasoning": str(obj.get("reasoning", ""))[:500]}, None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

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
            reply = self.provider.complete(self.system, self._transcript(), self.limits.model_timeout_s)
            u = self.state["usage"]
            self.state["model_calls"] += 1
            u["model_seconds"] += reply.elapsed_s
            if reply.usage:
                u["input_tokens"] += (reply.usage.get("input_tokens") or 0)
                u["output_tokens"] += (reply.usage.get("output_tokens") or 0)
            if reply.cost_usd is None:
                u["cost_unknown_calls"] += 1
            else:
                u["cost_usd_reported"] += reply.cost_usd
            self._event("model_reply", text=reply.text, error=reply.error, usage=reply.usage, cost_usd=reply.cost_usd, cost_basis=reply.cost_basis, model=reply.model, elapsed_s=round(reply.elapsed_s, 2))
            if reply.error:
                return self._stop("provider_error", error=reply.error)
            self.messages.append({"role": "assistant", "content": reply.text})
            action, err = self._parse(reply.text)
            if action is None:
                self.state["invalid_replies"] += 1
                self._event("invalid_reply", error=err)
                if self.state["invalid_replies"] >= self.limits.max_invalid_replies:
                    return self._stop("invalid_replies")
                self._feedback({"ok": False, "error": {"code": "invalid_reply", "message": f"could not parse your reply: {err}. Reply with ONE JSON object {{\"reasoning\":..,\"action\":{{\"tool\":..,\"args\":{{..}}}}}}"}})
                continue
            if action["tool"] == "submit":
                result = self._submit(action["args"])
                if result is not None:
                    return result
                continue
            self.state["tool_calls"] += 1
            res = self.registry.call(action["tool"], action["args"])
            self.state["usage"]["tool_seconds"] += res.elapsed_s
            self._event("tool_call", tool=action["tool"], args=action["args"], result=res.to_json())
            if action["tool"] == "propose_patch" and res.ok:
                fp = res.result["patch"]["fingerprint"]
                if fp in self.state["patch_fingerprints"]:
                    self.state["identical_patch_hits"] += 1
                self.state["patch_fingerprints"].append(fp)
                self._transition("PATCH_APPLIED", patch_seq=res.result["patch"]["seq"], after_sha256=res.result["patch"]["after_sha256"][:12])
                self._transition("AGENT_TURN")
                if self.state["identical_patch_hits"] >= self.limits.identical_patch_limit:
                    return self._stop("repeated_identical_patch")
            elif action["tool"] == "propose_patch" and not res.ok:
                pass
            self._feedback(res.to_json())

    def _feedback(self, payload: dict) -> None:
        text = json.dumps(payload, default=str)
        self.messages.append({"role": "user", "content": f"TOOL RESULT:\n{text[:6000]}"})
        self._event("message", role="user", content=text[:6000])
        self._save()

    def _submit(self, args: dict):
        sha = self.ws.sha256()
        if sha in self.state["evaluated_manifest_sha256"]:
            self.state["no_progress"] += 1
            self._event("submit_rejected_duplicate", manifest_sha256=sha)
            if self.state["no_progress"] >= self.limits.no_progress_limit:
                return self._stop("no_progress", detail="design identical to one already evaluated")
            self._feedback({"ok": False, "error": {"code": "already_evaluated", "message": "this exact design was already evaluated and rejected; change the design before submitting again"}})
            return None
        self.state["iteration"] += 1
        self._transition("EVALUATING", iteration=self.state["iteration"], manifest_sha256=sha[:12])
        ev_dir = self.run_dir / "evaluations" / f"iter_{self.state['iteration']:02d}"
        t = time.monotonic()
        decision = self.evaluator.evaluate(self.ws.manifest, ev_dir, self.baseline_record)
        self.state["usage"]["eval_seconds"] += time.monotonic() - t
        self.state["evaluations"] += 1
        self.state["evaluated_manifest_sha256"].append(sha)
        ev_dir.parent.mkdir(parents=True, exist_ok=True)
        (ev_dir.parent / f"decision_{self.state['iteration']:02d}.json").write_text(json.dumps(decision, indent=1, default=str))
        self._event("evaluation", iteration=self.state["iteration"], verdict=decision["verdict"], violations=decision.get("violations"), reason=decision.get("reason"))
        if decision["verdict"] == "accepted":
            self._finalize(decision)
            self.state["stop_reason"] = "accepted"
            self._transition("ACCEPTED", iteration=self.state["iteration"])
            return self._summary()
        if decision["verdict"] == "blocked":
            return self._stop("evaluation_blocked", detail=decision.get("reason"))
        n = len(decision["violations"])
        best = self.state["best_violation_count"]
        self.state["no_progress"] = self.state["no_progress"] + 1 if (best is not None and n >= best) else 0
        self.state["best_violation_count"] = n if best is None else min(best, n)
        self._transition("REJECTED_CONTINUE", violations=n, no_progress=self.state["no_progress"])
        if self.state["iteration"] >= self.limits.max_iterations:
            return self._stop("max_iterations")
        if self.state["no_progress"] >= self.limits.no_progress_limit:
            return self._stop("no_progress", detail="violation count did not improve")
        findings = [{k: v for k, v in x.items() if k != "candidate_detail"} | ({"detail": x["candidate_detail"][:200]} if "candidate_detail" in x else {}) for x in decision["violations"]][:12]
        self._feedback({"ok": False, "evaluator_verdict": "rejected", "findings": findings, "unresolved_baseline_failures_not_yours": decision["unresolved_baseline_failures"],
                        "note": "fresh evidence was produced by the protected pipeline; fix the cause of the findings, do not guess"})
        self._transition("AGENT_TURN")
        return None

    def _finalize(self, decision: dict) -> None:
        out = self.run_dir / "output"
        out.mkdir(exist_ok=True)
        shutil.copy(self.ws.manifest, out / "components.yaml")
        pristine, defective, final = self.pristine.read_text().splitlines(True), (self.run_dir / "defect_manifest.yaml").read_text().splitlines(True), self.ws.text().splitlines(True)
        (out / "agent_patch_vs_defect.diff").write_text("".join(difflib.unified_diff(defective, final, "defect/components.yaml", "candidate/components.yaml")))
        (out / "candidate_vs_adopted_reference.diff").write_text("".join(difflib.unified_diff(pristine, final, "designs/fe-aaf-001/components.yaml", "candidate/components.yaml")))
        (out / "patches.json").write_text(json.dumps(self.ws.history, indent=1))
        (out / "evaluator_decision.json").write_text(json.dumps(decision, indent=1, default=str))
        (out / "README.txt").write_text("Integrated result in an ISOLATED output workspace. The adopted reference (designs/fe-aaf-001/components.yaml) was NOT modified. "
                                        "Review agent_patch_vs_defect.diff / patches.json; merging is a human decision. release_ready is false: unresolved baseline failures remain "
                                        f"({', '.join(decision['unresolved_baseline_failures'])}).\n")

    @classmethod
    def resume(cls, run_dir: Path, **kw) -> "Controller":
        """Reload a stopped run: state counters, workspace and transcript (from the trace) are restored; limits may be raised in kw['limits']."""
        c = cls(run_dir, **kw)
        c.state = json.loads((run_dir / "state.json").read_text())
        c.state["stop_reason"] = None
        c.baseline_record = json.loads((run_dir / "baseline_record.json").read_text())
        c.ws = Workspace(run_dir / "workspace")
        c.ws.history = [json.loads(p.read_text()) for p in sorted((run_dir / "workspace" / "patches").glob("*.json"))]
        c.ctx = AgentContext(c.ws, run_dir / "scratch", failure_report=json.loads((run_dir / "defect_failure_report.json").read_text()), checks_fn=c.checks_fn, repo=c.repo,
                             trusted_combined=c.evaluator.digest_at_start["combined"])
        c.registry = build_registry(c.ctx)
        c.system = SYSTEM_PROMPT.replace("{tools}", "\n".join(json.dumps(s.schema()) for s in c.registry.specs()))
        c.messages, last = [], 0
        for line in (run_dir / "trace.jsonl").read_text().splitlines():
            e = json.loads(line)
            last = e["seq"]
            if e["type"] == "message":
                c.messages.append({"role": e["role"], "content": e["content"]})
            elif e["type"] == "model_reply" and not e.get("error"):
                c.messages.append({"role": "assistant", "content": e["text"]})
        c.seq = last
        c.messages.append({"role": "user", "content": "(run resumed by the controller with the limits shown in task.json; continue from where you stopped)"})
        c._event("resumed", from_state=c.state["state"])
        return c

    def continue_run(self) -> dict:
        return self._loop()

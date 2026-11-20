"""The agent's typed tool set for the first task. Every tool is read-only except `propose_patch`, which only edits the isolated manifest through validated patches."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from ae import bomcheck
from ae.harness.checks import CHECKS, PREVIEW_CHECKS, assess_guards, run_fast
from ae.harness.contracts import Arg, ToolError, ToolRegistry, ToolSpec
from ae.harness.workspace import REPO_ROOT, Workspace, trusted_digest


@dataclass
class AgentContext:
    workspace: Workspace
    scratch: Path
    failure_report: dict
    checks_fn: Callable = run_fast
    repo: Path = REPO_ROOT
    cache: dict = field(default_factory=dict)
    trusted_combined: str = ""
    patch_log: list = field(default_factory=list)
    improvement: bool = False          # improvement tasks also get `declare_no_improvement` and the worst-case `corner_preview` check
    corner_previews: int = 0           # uses so far (the sweep is ~1 min of simulation); bounded by max_corner_previews
    baseline_manifest_text: str | None = None       # improvement tasks: adopted baseline manifest text (input of the advisory edit_sanity)
    last_evaluated_manifest_text: str | None = None  # the design most recently sent to the evaluator (edit_sanity's "previous")
    guard: dict | None = None          # improvement tasks: {"policy_raw", "rules", "baseline_checks", "objective_check"} used by the combined `preview` to project the policy rules
    max_corner_previews: int = 12      # fresh sweeps of EITHER preview check (corner ~50 s, requirements ~40 s); cache hits are free


def advisory_sanity(ctx: AgentContext, manifest_text: str | None = None, full: bool = False) -> dict | None:
    """ADVISORY ideal-op-amp edit check (ae.harness.sanity) of the current (or given) design. Never raises, never changes acceptance or refusal; None when not applicable
    (restoration task, no baseline text). Compact form = status + warnings (code, severity, message); full adds the predicted numbers."""
    if not ctx.improvement or ctx.baseline_manifest_text is None:
        return None
    try:
        from ae.harness.sanity import edit_sanity
        res = edit_sanity(manifest_text if manifest_text is not None else ctx.workspace.text(), ctx.baseline_manifest_text, previous_manifest_text=ctx.last_evaluated_manifest_text)
        out = {"advisory": True, "status": res.get("status"), "not_acceptance": res.get("not_acceptance", "advisory only; the independent evaluator decides"),
               "warnings": [{k: w.get(k) for k in ("code", "severity", "message")} for w in res.get("warnings", [])]}
        if res.get("status") == "skipped":
            out["reason"] = res.get("reason")
        if full:
            out["predicted"], out["baseline_predicted"] = res.get("predicted"), res.get("baseline_predicted")
        return out
    except Exception as exc:                                  # advisory: must never break a run
        return {"advisory": True, "status": "skipped", "reason": f"{type(exc).__name__}: {str(exc)[:120]}", "warnings": []}


def _contract(repo: Path) -> dict:
    return yaml.safe_load((repo / "requirements/fe-aaf-001.yaml").read_text())


def build_registry(ctx: AgentContext) -> ToolRegistry:
    reg = ToolRegistry()

    def get_requirements(ids: list | None = None) -> dict:
        c = _contract(ctx.repo)
        reqs = [{"id": r["id"], "text": r["text"], "check": r.get("check"), "verify": r.get("verify")} for r in c["requirements"] if not ids or r["id"] in ids]
        return {"requirements": reqs, "conditions": c["conditions"], "signal_source": c["signal_source"], "note": "read-only; the contract is protected and cannot be edited by any tool"}

    def get_design(parts: list | None = None) -> dict:
        data = yaml.safe_load(ctx.workspace.text())["parts"]
        rows = []
        for ref, p in data.items():
            if parts and ref not in parts:
                continue
            rows.append({"ref": ref, "role": p.get("role"), "value": p.get("value"), "tol": p.get("tol"), "dielectric": p.get("dielectric"), "voltage": p.get("voltage"),
                         "package": p.get("package"), "mpn": p.get("mpn") or p.get("mpn_candidate"), "mpn_status": p.get("mpn_status"), "footprint": p.get("footprint")})
        return {"manifest_sha256": ctx.workspace.sha256(), "parts": rows}

    def get_design_evidence(ref: str) -> dict:
        ledger = yaml.safe_load((ctx.repo / "evidence/bom-verification.yaml").read_text())["lines"]
        entry = next((l for l in ledger if ref in l["refs"]), None)
        derivation = json.loads((ctx.repo / "analysis/filter_design_0002.json").read_text())["params"]
        key = {"C1": "C1", "C2": "C2", "C3": "C3", "R1": "R1", "R2": "R2", "R3": "R3", "R4": "R4", "R5": "R5"}.get(ref)
        return {"ref": ref, "bom_verification_ledger": ({"level": entry["level"], "mpn": entry["mpn"], "design_value_at_verification": entry["design_value"], "checked_against_design": entry["checked_against_design"],
                                                         "sources": [s.get("what") for s in entry["sources"]]} if entry else None),
                "design_calculation_record": ({"value_si": derivation[key], "source": "analysis/filter_design_0002.json (design-time derivation)"} if key in derivation else None)}

    def decode_mpn(mpn: str) -> dict:
        d = bomcheck.decode(mpn)
        return {"mpn": mpn, "supported": d is not None, "decoded": d}

    def get_failure_report() -> dict:
        return ctx.failure_report

    def run_check(check: str) -> dict:
        if check not in CHECKS:
            raise ToolError("invalid_arguments", f"unknown check {check!r}", {"available": list(CHECKS)})
        if check in PREVIEW_CHECKS and not ctx.improvement:
            raise ToolError("invalid_arguments", f"{check} is available only in improvement tasks", {"available": [c for c in CHECKS if c not in PREVIEW_CHECKS]})
        if check == "edit_sanity":                                 # free (~0.1 s, ideal model, no simulator), advisory, no budget
            res = advisory_sanity(ctx, full=True)
            if res is None:
                raise ToolError("invalid_arguments", "edit_sanity is available only in improvement tasks", {"available": [c for c in CHECKS if c not in PREVIEW_CHECKS]})
            return {**res, "cache_hit": False}
        key = (check, ctx.workspace.sha256(), ctx.trusted_combined)
        if check == "preview":
            return _combined_preview(key)
        if key in ctx.cache:
            return {**ctx.cache[key], "cache_hit": True, "evidence_key": [check, key[1][:12], key[2][:12]]}
        if check in PREVIEW_CHECKS:
            if ctx.corner_previews >= ctx.max_corner_previews:
                raise ToolError("preview_budget_exhausted", f"previews (corner_preview + requirements_preview + preview) are limited to {ctx.max_corner_previews} fresh runs per task; submit or declare instead", {"used": ctx.corner_previews})
            ctx.corner_previews += 1
        out = ctx.checks_fn([check], ctx.workspace.manifest, ctx.scratch / f"fast_{key[1][:10]}")
        res = out.get(check)
        if not isinstance(res, dict) or "outcome" not in res:
            raise ToolError("unreadable_result", f"check {check} produced no readable result", {"got": str(out)[:200]})
        if check in PREVIEW_CHECKS and res["outcome"] in ("error", "blocked"):
            if res["outcome"] == "error":
                ctx.corner_previews -= 1                                   # never reached the sweep (e.g. netlist export failed): no budget spent
            return {**res, "cache_hit": False, "evidence_key": [check, key[1][:12], key[2][:12]]}      # transient failures are not cached, so the same design can be retried
        ctx.cache[key] = res                                           # keyed by design hash + trusted-code digest: ANY edit makes the old evidence unreachable
        return {**res, "cache_hit": False, "evidence_key": [check, key[1][:12], key[2][:12]]}

    def _combined_preview(key: tuple) -> dict:
        """ONE budget unit for corners + nominal requirements + the policy-rule projection. Parts already cached for this design (from the single previews) are reused and free;
        if both are cached the whole call is free. A part that errors or blocks is returned as such, is not cached, and refunds the unit only when nothing reached a simulation."""
        design_key = (key[1], key[2])
        parts = {name: ctx.cache.get((name, *design_key)) for name in ("corner_preview", "requirements_preview")}
        missing = [n for n, v in parts.items() if v is None]
        fresh = bool(missing)
        if fresh:
            if ctx.corner_previews >= ctx.max_corner_previews:
                raise ToolError("preview_budget_exhausted", f"previews (corner_preview + requirements_preview + preview) are limited to {ctx.max_corner_previews} fresh runs per task; submit or declare instead", {"used": ctx.corner_previews})
            ctx.corner_previews += 1
            out = ctx.checks_fn(missing, ctx.workspace.manifest, ctx.scratch / f"fast_{key[1][:10]}")
            for name in missing:
                res = out.get(name)
                if not isinstance(res, dict) or "outcome" not in res:
                    raise ToolError("unreadable_result", f"check {name} produced no readable result", {"got": str(out)[:200]})
                parts[name] = res
                if res["outcome"] == "error" and len(missing) == 2 and name == missing[0]:
                    ctx.corner_previews -= 1                               # same refund rule as the single previews: it never reached a sweep
                if res["outcome"] not in ("error", "blocked"):
                    ctx.cache[(name, *design_key)] = res
        corner, reqs = parts["corner_preview"], parts["requirements_preview"]
        ok_corner = corner["outcome"] in ("within_contract_limits", "outside_contract_limits")
        ok_reqs = reqs["outcome"] == "info"
        result = {"outcome": "info" if (ok_corner and ok_reqs) else "partial", "not_acceptance": "advisory projection; only the independent evaluator accepts or rejects (it also applies BOM, status, series and row rules)",
                  "corner_preview": corner, "requirements_preview": reqs, "cache_hit": not fresh, "evidence_key": ["preview", key[1][:12], key[2][:12]]}
        if ctx.guard is not None:
            g = ctx.guard
            result["policy_projection"] = assess_guards(g["policy_raw"], g["rules"], g["baseline_checks"], g["objective_check"], corner if ok_corner else None, reqs if ok_reqs else None)
        return result

    def propose_patch(patch: dict) -> dict:
        rec = ctx.workspace.propose(patch)
        ctx.patch_log.append(rec)
        return {"applied": True, "patch": rec, "new_base_sha256": rec["after_sha256"], "note": "evidence for the previous design hash is now stale; rerun checks"}

    reg.register(ToolSpec("get_requirements", "Read the protected requirements/conditions (read-only).", {"ids": Arg("list", "requirement ids to return (default: all)", False)}), get_requirements)
    reg.register(ToolSpec("get_design", "Read the current design manifest (parts, values, MPNs, evidence status) and its base hash.", {"parts": Arg("list", "refs to return (default: all)", False)}), get_design)
    reg.register(ToolSpec("get_design_evidence", "Read evidence recorded for one part: BOM verification ledger entry and design-time calculation record.", {"ref": Arg("str", "part reference, e.g. 'C1'")}), get_design_evidence)
    reg.register(ToolSpec("decode_mpn", "Decode a manufacturer part number (Kemet MLCC, Yageo RC/RT) into value/package/tolerance/rating.", {"mpn": Arg("str", "ordering code")}), decode_mpn)
    reg.register(ToolSpec("get_failure_report", "Measured failures of the design you were given (fast checks run on it before you started).", {}), get_failure_report)
    reg.register(ToolSpec("run_check", "Run a REAL check on the CURRENT design: bom_consistency (MPN vs schematic value/package/tolerance/rating), ac_response (nominal AC gain/flatness/alias vs the contract), preview (improvement tasks only, RECOMMENDED: ONE budget unit that runs the corner sweep AND the nominal requirements and projects every policy rule - objective gain, corner hard limits, REQ-STAB, REQ-BIAS and other guarded margins - as ok / WOULD_BE_REJECTED; about 100 s), corner_preview (improvement tasks only: the evaluator\'s own worst-case sweep over ALL tolerance corners, about a minute) or requirements_preview (improvement tasks only: the evaluator\'s nominal sweep of every requirement incl. REQ-STAB, bias, noise, settling; about 40 s). Both are limited in number per task and advisory.",
                          {"check": Arg("str", "bom_consistency | ac_response | edit_sanity (improvement, free, advisory ideal-model prediction of gain/flatness/alias limits) | preview | corner_preview | requirements_preview (improvement tasks)")}, timeout_s=150.0), run_check)
    reg.register(ToolSpec("propose_patch", "Propose AND apply (in the isolated workspace) a constrained patch: {summary, base_sha256, ops:[{op:'set_field', part, field:'value'|'mpn_candidate', to}]}. Rejected if stale or out of scope.",
                          {"patch": Arg("dict", "the patch object")}, writes_workspace=True), propose_patch)
    reg.register(ToolSpec("submit", "Finish: ask the independent evaluator to judge the current design. It runs the full trusted pipeline and may reject.", {"summary": Arg("str", "what you changed and why")}, timeout_s=5.0), lambda summary: {"submitted": True})
    if ctx.improvement:
        reg.register(ToolSpec("declare_no_improvement", "End the run WITHOUT submitting: you found no justified improvement. Spends no evaluation and is recorded as its own stop reason; use it instead of submitting an unchanged design (which is refused).",
                              {"summary": Arg("str", "why no improvement is justified")}, timeout_s=5.0), lambda summary: {"declared": True})
    return reg

"""The agent's typed tool set for the first task. Every tool is read-only except `propose_patch`, which only edits the isolated manifest through validated patches."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from ae import bomcheck
from ae.harness.checks import CHECKS, run_fast
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
        key = (check, ctx.workspace.sha256(), ctx.trusted_combined)
        if key in ctx.cache:
            return {**ctx.cache[key], "cache_hit": True, "evidence_key": [check, key[1][:12], key[2][:12]]}
        out = ctx.checks_fn([check], ctx.workspace.manifest, ctx.scratch / f"fast_{key[1][:10]}")
        res = out.get(check)
        if not isinstance(res, dict) or "outcome" not in res:
            raise ToolError("unreadable_result", f"check {check} produced no readable result", {"got": str(out)[:200]})
        ctx.cache[key] = res                                           # keyed by design hash + trusted-code digest: ANY edit makes the old evidence unreachable
        return {**res, "cache_hit": False, "evidence_key": [check, key[1][:12], key[2][:12]]}

    def propose_patch(patch: dict) -> dict:
        rec = ctx.workspace.propose(patch)
        ctx.patch_log.append(rec)
        return {"applied": True, "patch": rec, "new_base_sha256": rec["after_sha256"], "note": "evidence for the previous design hash is now stale; rerun checks"}

    reg.register(ToolSpec("get_requirements", "Read the protected requirements/conditions (read-only).", {"ids": Arg("list", "requirement ids to return (default: all)", False)}), get_requirements)
    reg.register(ToolSpec("get_design", "Read the current design manifest (parts, values, MPNs, evidence status) and its base hash.", {"parts": Arg("list", "refs to return (default: all)", False)}), get_design)
    reg.register(ToolSpec("get_design_evidence", "Read evidence recorded for one part: BOM verification ledger entry and design-time calculation record.", {"ref": Arg("str", "part reference, e.g. 'C1'")}), get_design_evidence)
    reg.register(ToolSpec("decode_mpn", "Decode a manufacturer part number (Kemet MLCC, Yageo RC/RT) into value/package/tolerance/rating.", {"mpn": Arg("str", "ordering code")}), decode_mpn)
    reg.register(ToolSpec("get_failure_report", "Measured failures of the design you were given (fast checks run on it before you started).", {}), get_failure_report)
    reg.register(ToolSpec("run_check", "Run a REAL check on the CURRENT design: bom_consistency (MPN vs schematic value/package/tolerance/rating) or ac_response (nominal AC gain/flatness/alias vs the contract).",
                          {"check": Arg("str", "bom_consistency | ac_response")}, timeout_s=150.0), run_check)
    reg.register(ToolSpec("propose_patch", "Propose AND apply (in the isolated workspace) a constrained patch: {summary, base_sha256, ops:[{op:'set_field', part, field:'value'|'mpn_candidate', to}]}. Rejected if stale or out of scope.",
                          {"patch": Arg("dict", "the patch object")}, writes_workspace=True), propose_patch)
    reg.register(ToolSpec("submit", "Finish: ask the independent evaluator to judge the current design. It runs the full trusted pipeline and may reject.", {"summary": Arg("str", "what you changed and why")}, timeout_s=5.0), lambda summary: {"submitted": True})
    return reg

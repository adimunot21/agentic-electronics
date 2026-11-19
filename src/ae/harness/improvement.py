"""IMPROVEMENT mode: typed frozen policy + pure decision function (separate from the restoration policy in evaluator.decide, which is untouched).

Restoration demands numeric equality with the baseline. Improvement must allow a CHANGED design, so equality is replaced by explicit, pre-registered rules:
  objective improvement >= min_improvement (beyond jitter) + hard constraints with reserves + margin non-regression for everything else + status rules for failed/blocked/not_tested baseline checks.
The policy is a frozen dataclass built from a trusted YAML (in the trusted digest); candidates are data and cannot address it. `decide_improvement` is pure: records in, decision out.
Decisions keep POLICY VIOLATIONS (rules of this policy that were broken) apart from CIRCUIT REQUIREMENT FAILURES (candidate checks whose own outcome is failed).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ae.harness.evaluator import SEVERITY
from ae.harness.patch import ScopePolicy
from ae.harness.workspace import REPO_ROOT

ACCEPT = "accepted_for_objective"


class PolicyError(Exception):
    pass


@dataclass(frozen=True)
class ImprovementPolicy:
    task_id: str
    version: int
    sha256: str                         # hash of the policy file text (recorded in every decision)
    raw: dict                           # the parsed YAML (read-only by convention; never handed to the agent as writable)
    objective_check: str
    min_improvement: float
    rel_tol: float
    abs_tol: float
    margin_fraction: float
    traded: frozenset
    allowed_parts: tuple
    allowed_fields: tuple
    max_ops: int

    def scope(self) -> ScopePolicy:
        return ScopePolicy(part_pattern="^(?:" + "|".join(re.escape(p) for p in self.allowed_parts) + ")$", fields=frozenset(self.allowed_fields), max_ops=self.max_ops)


def load_policy(path: Path, repo: Path = REPO_ROOT) -> ImprovementPolicy:
    """Load and VERIFY: baseline design/requirements hashes must equal the files on disk, and contract limits quoted in the policy must equal the protected requirements."""
    text = path.read_text()
    y = yaml.safe_load(text)
    if y.get("kind") != "improvement":
        raise PolicyError(f"{path.name}: not an improvement policy")
    b = y["baseline"]
    for key, rel in (("components_sha256", b["design"]), ("requirements_sha256", "requirements/fe-aaf-001.yaml")):
        actual = hashlib.sha256((repo / rel).read_bytes()).hexdigest()
        if actual != b[key]:
            raise PolicyError(f"baseline_changed: {rel} sha256 {actual[:12]} != frozen {b[key][:12]}")
    req = {r["id"]: r.get("check") or {} for r in yaml.safe_load((repo / "requirements/fe-aaf-001.yaml").read_text())["requirements"]}
    for h in y["hard_constraints"]:
        if "contract_limit" not in h:
            continue
        chk = req[h["check"].replace("-CORNERS", "")]
        lim = h["contract_limit"]
        if isinstance(lim, list):
            ok = [chk.get("min"), chk.get("max")] == list(lim) and abs(h["min"] - lim[0] - h["reserve"]) < 1e-12 and abs(lim[1] - h["max"] - h["reserve"]) < 1e-12
        elif "min" in h:
            ok = chk.get("min") == lim and abs(h["min"] - lim - h["reserve"]) < 1e-12
        else:
            ok = chk.get("max") == lim and abs(lim - h["max"] - h["reserve"]) < 1e-12
        if not ok:
            raise PolicyError(f"{h['id']}: contract_limit/reserve in the policy do not match the protected requirements ({chk})")
    ae = y["allowed_edits"]
    return ImprovementPolicy(y["id"], y["policy_version"], hashlib.sha256(text.encode()).hexdigest(), y, y["objective"]["check"], float(y["objective"]["min_improvement"]),
                             float(y["jitter"]["rel"]), float(y["jitter"]["abs"]), float(y["nonregression"]["generic_margin_fraction"]), frozenset(y["traded_metrics"]),
                             tuple(ae["parts"]), tuple(ae["fields"]), int(ae["max_ops"]))


def _by_id(record: dict) -> dict:
    out: dict[str, dict] = {}
    for c in record["checks"]:
        cur = out.setdefault(c["id"], dict(c))
        if SEVERITY.get(c["outcome"], 3) > SEVERITY.get(cur["outcome"], 3):
            out[c["id"]] = dict(c)
    return out


def _margin(c: dict) -> float | None:
    if c.get("margin") is not None:
        return float(c["margin"])
    m = re.fullmatch(r"\s*(<=|>=)\s*([-+\d.eE]+)\s*", c.get("limit") or "")
    if m and c.get("value") is not None:
        lim = float(m.group(2))
        return lim - c["value"] if m.group(1) == "<=" else c["value"] - lim
    return None


def _within(a: float, b: float, pol: ImprovementPolicy) -> bool:
    return abs(a - b) <= pol.abs_tol + pol.rel_tol * abs(b)


def _bom_rows(path: Path) -> dict:
    return {r["Refs"]: r for r in csv.DictReader(path.open())} if path.exists() else {}


def edited_refs(baseline_manifest: str, candidate_manifest: str) -> list[str]:
    a, b = yaml.safe_load(baseline_manifest)["parts"], yaml.safe_load(candidate_manifest)["parts"]
    return sorted(ref for ref in set(a) | set(b) if a.get(ref) != b.get(ref))


def decide_improvement(baseline: dict, candidate: dict, policy: ImprovementPolicy, baseline_dir: Path, candidate_dir: Path, baseline_manifest: str, candidate_manifest: str) -> dict:
    """Pure policy decision. `baseline`/`candidate` are trusted pipeline records; *_dir hold their raw outputs (corners_summary.json, release/bom.csv)."""
    base, cand = _by_id(baseline), _by_id(candidate)
    raw = policy.raw
    viol: list[dict] = []
    blocked_reasons: list[str] = []

    def v(rule: str, check: str | None, **kw) -> None:
        viol.append({"rule": rule, "check": check, **kw})

    # ---- evidence completeness: every baseline check must exist; errors/blocked evidence for evaluated sim checks block the verdict
    for cid in base:
        if cid not in cand:
            blocked_reasons.append(f"missing_check:{cid}")
    for cid, c in cand.items():
        if c["outcome"] == "error":
            blocked_reasons.append(f"error:{cid}")
    for cid in ("REQ-ALIAS-CORNERS", "REQ-FLAT-CORNERS", "REQ-GAIN-CORNERS"):
        if cid in cand and cand[cid]["outcome"] == "blocked":
            blocked_reasons.append(f"corners_incomplete:{cid}")
    if blocked_reasons:
        return _finish("blocked", viol, base, cand, policy, [], {"reason": blocked_reasons}, [], baseline_manifest, candidate_manifest)

    # ---- baseline sanity: fresh baseline must reproduce the frozen objective value (guards against a drifted toolchain)
    obj_b, obj_c = base[policy.objective_check], cand[policy.objective_check]
    expected = raw["objective"]["baseline_value_expected_dB"]
    if obj_b.get("value") is None or not _within(obj_b["value"], expected, policy):
        return _finish("blocked", viol, base, cand, policy, [], {"reason": [f"baseline_not_reproduced: objective {obj_b.get('value')} != frozen {expected}"]}, [], baseline_manifest, candidate_manifest)

    # ---- objective
    improvement = obj_b["value"] - obj_c["value"]            # direction: minimize, positive = more rejection
    objective = {"check": policy.objective_check, "unit": raw["objective"]["unit"], "direction": "minimize", "baseline": obj_b["value"], "candidate": obj_c["value"],
                 "improvement": improvement, "min_improvement": policy.min_improvement, "met": improvement >= policy.min_improvement,
                 "jitter_tolerance_dB": policy.abs_tol + policy.rel_tol * abs(obj_b["value"])}
    if not objective["met"]:
        kind = "regression" if improvement < -objective["jitter_tolerance_dB"] else ("no_change_within_jitter" if abs(improvement) <= objective["jitter_tolerance_dB"] else "improvement_below_threshold")
        v("objective_improvement", policy.objective_check, kind=kind, improvement=improvement, required=policy.min_improvement)

    # ---- hard constraints
    def val(cid: str):
        return cand[cid].get("value")
    cs_b = json.loads((baseline_dir / "corners_summary.json").read_text())
    cs_c = json.loads((candidate_dir / "corners_summary.json").read_text())
    for h in raw["hard_constraints"]:
        hid = h["id"]
        if hid == "H7":
            for cid, b in base.items():
                if b["outcome"] == "passed" and cand[cid]["outcome"] != "passed":
                    v("H7_passed_stays_passed", cid, baseline="passed", candidate=cand[cid]["outcome"], detail=cand[cid].get("detail", "")[:200])
        elif hid in ("H1", "H4"):
            x = val(h["check"])
            if x is None or not math.isfinite(x) or x > h["max"]:
                v(f"{hid}_hard_limit", h["check"], value=x, max=h["max"], unit="dB")
        elif hid == "H2":
            if not math.isfinite(cs_c["gain_min"]) or cs_c["gain_min"] < h["min"]:
                v("H2_gain_min_corners", h["check"], value=cs_c["gain_min"], min=h["min"], baseline_value=cs_b["gain_min"], unit="V/V")
        elif hid == "H3":
            if not math.isfinite(cs_c["gain_max"]) or cs_c["gain_max"] > h["max"]:
                v("H3_gain_max_corners", h["check"], value=cs_c["gain_max"], max=h["max"], baseline_value=cs_b["gain_max"], unit="V/V")
        elif hid == "H5":
            x = val("REQ-GAIN")
            if x is None or not (h["min"] <= x <= h["max"]):
                v("H5_gain_nominal", "REQ-GAIN", value=x, min=h["min"], max=h["max"], unit="V/V")
        elif hid == "H6":
            xb, xc = base["REQ-ALIAS"]["value"], val("REQ-ALIAS")
            if xc is None or xc > h["max"] or xc > xb + policy.abs_tol + policy.rel_tol * abs(xb):
                v("H6_alias_nominal_nonregression", "REQ-ALIAS", baseline=xb, candidate=xc, unit="dB")

    # ---- non-regression of everything else (traded metrics are governed by H1-H5 only)
    nr = raw["nonregression"]
    handled = set(policy.traded) | {policy.objective_check, "REQ-ALIAS"}
    for cid, b in base.items():
        c = cand[cid]
        if cid in handled:
            continue
        sb, sc = SEVERITY.get(b["outcome"], 3), SEVERITY.get(c["outcome"], 3)
        if cid == "REQ-STAB":
            if c["outcome"] == "passed":
                v("REQ-STAB_status_change_needs_review", cid, baseline=b["outcome"], candidate=c["outcome"])
            if c["value"] is None or c["value"] > b["value"] + policy.abs_tol + policy.rel_tol * abs(b["value"]):
                v("REQ-STAB_nonregression", cid, baseline=b["value"], candidate=c["value"], delta=None if c["value"] is None else c["value"] - b["value"], unit="dB")
            continue
        if cid == "REQ-BOM":
            if c["outcome"] != "failed":
                v("REQ-BOM_status", cid, baseline=b["outcome"], candidate=c["outcome"])
            continue                                             # row-level BOM rule below
        if cid in nr["not_tested_in_baseline"]:
            if c["outcome"] != "not_tested":
                v("MODEL_not_tested_stays", cid, baseline=b["outcome"], candidate=c["outcome"])
            continue
        if cid == "BOM_CONSISTENCY":
            if c["outcome"] != "blocked" or not re.search(r"\b0 MISMATCH\b", c.get("detail", "")):
                v("BOM_CONSISTENCY_blocked_with_0_mismatch", cid, baseline=b["outcome"], candidate=c["outcome"], detail=c.get("detail", "")[:200])
            continue
        if cid in nr["unchanged_status_required"]:
            if c["outcome"] != b["outcome"]:
                v("unchanged_status_required", cid, baseline=b["outcome"], candidate=c["outcome"])
            continue
        if sc > sb:
            v("status_regression", cid, baseline=b["outcome"], candidate=c["outcome"])
            continue
        if b["outcome"] != "passed":
            if sc < sb:
                v("unreviewed_status_improvement", cid, baseline=b["outcome"], candidate=c["outcome"])
            continue
        mb, mc = _margin(b), _margin(c)
        if mb is None or mc is None:
            if (b.get("value") is not None) != (c.get("value") is not None):
                v("margin_unavailable", cid)
            continue
        allowed_loss = policy.margin_fraction * mb if mb > 0 else 0.0
        if mc < mb - allowed_loss - (policy.abs_tol + policy.rel_tol * abs(mb)):
            v("margin_nonregression", cid, baseline_margin=mb, candidate_margin=mc, allowed_loss=allowed_loss, baseline_value=b.get("value"), candidate_value=c.get("value"))

    # ---- BOM row rule: only edited refs may differ, and only to unverified_candidate
    edited = edited_refs(baseline_manifest, candidate_manifest)
    rb, rc = _bom_rows(baseline_dir / "release/bom.csv"), _bom_rows(candidate_dir / "release/bom.csv")
    changed_rows = [k for k in set(rb) | set(rc) if rb.get(k) != rc.get(k)]
    for k in changed_rows:
        refs = [r.strip() for r in k.split(",")]
        row = rc.get(k)
        if not any(r in edited for r in refs):
            v("bom_row_changed_outside_edit", "REQ-BOM", row=k)
        elif row is not None and row["MPN_Status"] != "unverified_candidate" and rb.get(k, {}).get("MPN") != row["MPN"]:
            v("bom_edited_mpn_not_marked_unverified", "REQ-BOM", row=k, status=row["MPN_Status"])

    blocked_claims = _blocked_claims(raw, edited, rc)
    verdict = ACCEPT if not viol else "rejected"
    return _finish(verdict, viol, base, cand, policy, edited, None, blocked_claims, baseline_manifest, candidate_manifest, objective=objective, cs=(cs_b, cs_c))


def _blocked_claims(raw: dict, edited: list[str], cand_bom: dict) -> list[dict]:
    claims = []
    for row, r in cand_bom.items():
        if r["MPN_Status"] == "unverified_candidate":
            claims.append({"claim": f"BOM_VERIFICATION:{row}", "why": f"MPN {r['MPN']} is unverified_candidate (value-consistent by decode only; existence/orderability/datasheet not checked)"})
    claims.append({"claim": "MODEL_STARTUP/SATURATION/OVERLOAD_RECOVERY", "why": "checks stay not_tested; behaviour of the modified stage-2 time constant under startup/saturation/overload is unvalidated"})
    claims.append({"claim": "REQ-STAB", "why": "rev 2 stays FAILED, rev 3 stays PROPOSED/NOT APPROVED; this task never resolves it"})
    return claims


def _finish(verdict: str, viol: list, base: dict, cand: dict, policy: ImprovementPolicy, edited: list, blocked: dict | None, claims: list, bm: str, cm: str, objective: dict | None = None, cs=None) -> dict:
    circuit_failures = [{"id": cid, "outcome": c["outcome"], "value": c.get("value"), "pre_existing": base.get(cid, {}).get("outcome") == "failed", "baseline_value": base.get(cid, {}).get("value")}
                        for cid, c in sorted(cand.items()) if c["outcome"] == "failed"]
    return {"mode": "improvement", "verdict": verdict, "task": policy.task_id, "policy_version": policy.version, "policy_sha256": policy.sha256,
            "scope": "accepted for THIS objective only; NOT a released or validated board" if verdict == ACCEPT else None,
            "objective": objective, "policy_violations": viol, "violations": viol, "circuit_requirement_failures": circuit_failures,
            "blocked": blocked, "blocked_claims": claims, "edited_refs": edited,
            "unresolved_baseline_failures": sorted(cid for cid, b in base.items() if b["outcome"] != "passed"), "release_ready": False,
            "release_note": "a scoped improvement is not a release pass; unresolved baseline failures remain and are listed",
            "corner_gain": ({"baseline": [cs[0]["gain_min"], cs[0]["gain_max"]], "candidate": [cs[1]["gain_min"], cs[1]["gain_max"]]} if cs else None),
            "baseline_manifest_sha256": hashlib.sha256(bm.encode()).hexdigest(), "candidate_manifest_sha256": hashlib.sha256(cm.encode()).hexdigest()}

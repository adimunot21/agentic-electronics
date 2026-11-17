"""Controlled defects: apply each to a separate run copy, run the SAME pipeline, compare with predictions made beforehand."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from ae.designs import fe_aaf_001 as design
from ae.reference_run import run_reference
from ae.sexpr import Q, dumps, find, find_all, parse
from ae.toolchain import REPO_ROOT


def swap_c1_c2_values(text: str) -> str:
    root = parse(text)
    inst = {str(find(s, "property")[2]): s for s in find_all(root, "symbol") if find(s, "property") and str(find(s, "property")[1]) == "Reference"}
    props = {ref: next(p for p in find_all(inst[ref], "property") if p[1] == "Value") for ref in ("C1", "C2")}
    props["C1"][2], props["C2"][2] = props["C2"][2], props["C1"][2]
    return dumps(root) + "\n"


def swap_symbol_pin_numbers(text: str) -> str:
    root = parse(text)
    lib = next(s for s in find_all(find(root, "lib_symbols"), "symbol") if s[1] == "Amplifier_Operational:LM358")
    sub = next(s for s in find_all(lib, "symbol") if s[1] == "LM358_1_1")
    nums = [find(p, "number") for p in find_all(sub, "pin")]
    two = next(n for n in nums if n[1] == "2")
    three = next(n for n in nums if n[1] == "3")
    two[1], three[1] = Q("3"), Q("2")
    return dumps(root) + "\n"


def swapped_inputs_intent() -> dict:
    nets = copy.deepcopy(design.INTENDED_NETS)
    nets["N_INV"] = [("U1", "3") if p == ("U1", "2") else p for p in nets["N_INV"]]
    nets["VMID"] = [("U1", "2") if p == ("U1", "3") else p for p in nets["VMID"]]
    return nets


CASES = {
    "D1_swap_C1_C2_values": dict(mutate_schematic=swap_c1_c2_values),
    "D2_opamp_inputs_swapped_in_intent": dict(nets=swapped_inputs_intent()),
    "D3_symbol_pin_numbers_swapped": dict(mutate_schematic=swap_symbol_pin_numbers),
    "D4_missing_routed_track": dict(mutate_pcb_net="/N_INV"),
}


def observed(checks: list[dict], check_id: str) -> str:
    outcomes = [c["outcome"] for c in checks if c["id"] == check_id]
    if not outcomes:
        return "absent"
    return "failed" if "failed" in outcomes else ("passed" if all(o == "passed" for o in outcomes) else outcomes[0])


def matches(pred: str, obs: str) -> bool:
    return {"pass": obs == "passed", "fail": obs == "failed", "not_pass": obs in ("failed", "blocked", "error", "not_tested"), "any": True}[pred]


def run_defect(defect_id: str, out_root: Path, baseline: dict) -> dict:
    spec = next(d for d in yaml.safe_load((REPO_ROOT / "designs/fe-aaf-001/defects.yaml").read_text())["defects"] if d["id"] == defect_id)
    rec = run_reference(out_root / defect_id, label=defect_id, **CASES[defect_id])
    checks = json.loads((out_root / defect_id / "record.json").read_text())["checks"]
    rows = []
    for cid, pred in spec["expect"].items():
        obs = observed(checks, cid)
        rows.append({"check": cid, "predicted": pred, "observed": obs, "match": matches(pred, obs)})
    base_obs = {c["id"]: observed(baseline["checks"], c["id"]) for c in baseline["checks"]}
    unpredicted = [{"check": cid, "baseline": base_obs[cid], "defect_run": observed(checks, cid)} for cid in base_obs
                   if cid not in spec["expect"] and observed(checks, cid) != base_obs[cid]]
    return {"defect": defect_id, "what": spec["what"], "rows": rows, "all_predictions_matched": all(r["match"] for r in rows), "unpredicted_changes": unpredicted}


if __name__ == "__main__":
    import sys
    out = REPO_ROOT / "runs/ref/defects"
    baseline = json.loads((REPO_ROOT / "runs/ref/baseline/record.json").read_text())
    ids = sys.argv[1:] or list(CASES)
    results = [run_defect(d, out, baseline) for d in ids]
    (out / "summary.json").write_text(json.dumps(results, indent=2))
    for r in results:
        print(f"\n== {r['defect']}: {'ALL PREDICTIONS MATCHED' if r['all_predictions_matched'] else 'PREDICTION MISMATCH'}")
        for row in r["rows"]:
            print(f"  {row['check']:<16} predicted {row['predicted']:<9} observed {row['observed']:<8} {'ok' if row['match'] else '<-- MISMATCH'}")
        for u in r["unpredicted_changes"]:
            print(f"  (unpredicted change) {u['check']}: baseline {u['baseline']} -> {u['defect_run']}")

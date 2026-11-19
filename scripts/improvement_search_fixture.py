"""DETERMINISTIC bounded local search for the frozen improvement task `improve-stage2-alias`, driven through the REAL controller + REAL EDA pipeline.

THESE ARE SCRIPTED FIXTURES: every candidate patch below is written down in the frozen policy file BEFORE any evaluation. No model proposes anything here, so none of the
results is evidence that an agent designed an improvement. What is exercised: patch validation, the improvement evaluator and its policy on real full-pipeline evidence.

  uv run python scripts/improvement_search_fixture.py                # baseline + search family (<= max_full_evaluations) + labelled controls
  uv run python scripts/improvement_search_fixture.py --labels A_r45_16k9 X_noop_c3_5n60
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.improvement import load_policy
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task
from ae.harness.workspace import REPO_ROOT

TASK_ID = "improve-stage2-alias"
OUT = REPO_ROOT / "runs/agent/improve-stage2-alias-fixture"


def scripted_agent(ops: list) -> ScriptedProvider:
    """FIXTURE agent: read the design, apply the pre-registered ops, run the nominal check, submit. (An out-of-scope patch is simply rejected by the validator; the script then ends.)"""
    def patch(transcript: str) -> str:
        sha = re.findall(r'"manifest_sha256": "([0-9a-f]{64})"', transcript)[-1]
        return json.dumps({"reasoning": "fixture", "action": {"tool": "propose_patch", "args": {"patch": {"summary": "fixture: pre-registered candidate", "base_sha256": sha,
                           "ops": [{"op": "set_field", "part": p, "field": f, "to": t} for p, f, t in ops]}}}})
    return ScriptedProvider([{"reasoning": "fixture", "action": {"tool": "get_design", "args": {}}}, patch,
                             {"reasoning": "fixture", "action": {"tool": "run_check", "args": {"check": "ac_response"}}},
                             {"reasoning": "fixture", "action": {"tool": "submit", "args": {"summary": "fixture candidate"}}}])


def snapshot_evidence(run_dir: Path) -> None:
    """Copy the small raw-evidence files of the evaluated candidate (record, corner summary, BOM) next to the decision so they stay traceable (the big evaluation dirs are git-ignored)."""
    src = run_dir / "evaluations/iter_01"
    if not src.exists():
        return
    dst = run_dir / "evidence"
    (dst / "release").mkdir(parents=True, exist_ok=True)
    for rel in ("record.json", "corners_summary.json", "release/bom.csv"):
        if (src / rel).exists():
            shutil.copy(src / rel, dst / rel)


def summarize(label: str, run_dir: Path, summary: dict, t: float) -> dict:
    row = {"label": label, "run_dir": str(run_dir.relative_to(REPO_ROOT)), "stop_reason": summary["stop_reason"], "final_state": summary["final_state"], "wall_s": round(t, 1)}
    dec_path = run_dir / "evaluations/decision_01.json"
    if dec_path.exists():
        d = json.loads(dec_path.read_text())
        rec = json.loads(Path(d["record_path"]).read_text()) if d.get("record_path") and Path(d["record_path"]).exists() else {"checks": []}
        by = {c["id"]: c for c in rec["checks"]}
        row.update({"verdict": d["verdict"], "objective": d.get("objective"), "policy_violation_rules": [v["rule"] for v in d.get("policy_violations", [])],
                    "circuit_failures": [f"{c['id']}{'(pre-existing)' if c['pre_existing'] else '(NEW)'}" for c in d.get("circuit_requirement_failures", [])],
                    "candidate_manifest_sha256": d.get("candidate_manifest_sha256"), "decision": str(dec_path.relative_to(REPO_ROOT)), "eval_s": d.get("elapsed_s"),
                    "metrics": {k: by[k].get("value") for k in ("REQ-ALIAS-CORNERS", "REQ-FLAT-CORNERS", "REQ-GAIN-CORNERS", "REQ-FLAT", "REQ-GAIN", "REQ-ALIAS", "REQ-STAB", "REQ-SETTLE", "REQ-NOISE") if k in by},
                    "corner_gain_min_max": d.get("corner_gain"), "blocked_claims": [c["claim"] for c in d.get("blocked_claims", [])]})
    else:
        tr = [json.loads(l) for l in (run_dir / "trace.jsonl").read_text().splitlines()]
        rej = [e for e in tr if e["type"] == "tool_call" and e["tool"] == "propose_patch" and not e["result"]["ok"]]
        row["verdict"] = "no_evaluation"
        row["patch_rejected_by_validator"] = rej[0]["result"]["error"] if rej else None
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", nargs="*", default=None, help="restrict to these candidate labels (default: frozen search order + controls)")
    ap.add_argument("--extension", default=None, help="YAML with extra candidate definitions (policy is NOT changed); labelled search_extension")
    ap.add_argument("--prior-full-evals", type=int, default=0, help="full evaluations already spent against the frozen cap")
    ap.add_argument("--reuse-baseline", action="store_true", help="reuse OUT/baseline_record.json if it was captured under the same trusted digest")
    ap.add_argument("--snapshot-existing", action="store_true", help="only snapshot raw evidence of already-evaluated candidate runs, then exit (no evaluation)")
    a = ap.parse_args()
    if a.snapshot_existing:
        for d in sorted(p for p in OUT.iterdir() if (p / "evaluations/iter_01").exists()):
            snapshot_evidence(d)
        (OUT / "baseline_evidence").mkdir(exist_ok=True)
        for rel in ("corners_summary.json",):
            shutil.copy(OUT / "baseline" / rel, OUT / "baseline_evidence" / rel)
        (OUT / "baseline_evidence/release").mkdir(exist_ok=True)
        shutil.copy(OUT / "baseline/release/bom.csv", OUT / "baseline_evidence/release/bom.csv")
        return 0
    policy = load_policy(REPO_ROOT / "src/ae/harness/tasks/improve_stage2_alias.yaml")
    search, cands = policy.raw["search"], policy.raw["search"]["candidates"]
    import yaml
    ext = yaml.safe_load(Path(a.extension).read_text())["candidates"] if a.extension else {}
    cands = {**cands, **ext}
    plan = a.labels or (list(search["order"]) + [c["id"] for c in search["controls"]])
    evaluator = TrustedEvaluator()
    OUT.mkdir(parents=True, exist_ok=True)
    pristine = REPO_ROOT / "designs/fe-aaf-001/components.yaml"
    base_dir = OUT / "baseline"
    t0 = time.monotonic()
    meta = OUT / "baseline_meta.json"
    if a.reuse_baseline and meta.exists() and json.loads(meta.read_text())["trusted_digest"] == evaluator.digest_at_start["combined"]:
        baseline = json.loads((OUT / "baseline_record.json").read_text())
        print("baseline REUSED (same trusted digest)", flush=True)
    else:
        baseline = evaluator.run_pipeline(pristine, base_dir)
        (OUT / "baseline_record.json").write_text(json.dumps(baseline, indent=1))
        meta.write_text(json.dumps({"trusted_digest": evaluator.digest_at_start["combined"], "policy_sha256": policy.sha256}))
    t_base = time.monotonic() - t0
    print(f"baseline captured in {t_base:.1f}s trusted_digest={evaluator.digest_at_start['combined'][:12]} policy={policy.sha256[:12]}", flush=True)
    rows, full_evals, prev_accepted = [], a.prior_full_evals, False
    for label in plan:
        is_search = label in search["order"] or label in ext
        if is_search and full_evals >= search["max_full_evaluations"]:
            print(f"STOP: max_full_evaluations={search['max_full_evaluations']} reached", flush=True)
            break
        run_dir = OUT / label
        ctl = Controller(run_dir, load_task(TASK_ID), scripted_agent(cands[label]["ops"]), evaluator, Limits(max_iterations=1, max_model_calls=8), Mode.ENGINEERED_SINGLE,
                         pristine_manifest=pristine, baseline_record=baseline, baseline_dir=base_dir)
        t = time.monotonic()
        summary = ctl.run()
        snapshot_evidence(run_dir)
        row = summarize(label, run_dir, summary, time.monotonic() - t)
        row["role"] = ("search_extension" if label in ext else "search") if is_search else "control"
        rows.append(row)
        if row["verdict"] != "no_evaluation":
            full_evals += 1 if is_search else 0
        print(json.dumps({k: row.get(k) for k in ("label", "role", "verdict", "wall_s", "policy_violation_rules", "circuit_failures")}), flush=True)
        hard = {"H1_hard_limit", "H2_gain_min_corners", "H3_gain_max_corners", "H4_hard_limit", "H5_gain_nominal"}
        rejected_on_hard = row["verdict"] == "rejected" and bool(hard & set(row.get("policy_violation_rules", [])))
        if is_search and prev_accepted and rejected_on_hard:
            print("STOP: accepted candidate followed by a hard-constraint rejection (frozen stop rule 1)", flush=True)
            break
        if is_search:
            prev_accepted = row["verdict"] == "accepted_for_objective"
    out = {"task": TASK_ID, "policy_sha256": policy.sha256, "trusted_digest": evaluator.digest_at_start["combined"], "baseline_wall_s": round(t_base, 1),
           "baseline_inputs_sha256": baseline["inputs_sha256"], "baseline_summary": baseline["summary"], "fixture": True,
           "note": "scripted FIXTURE candidates, not agent output", "rows": rows}
    (OUT / ("search_summary_extension.json" if ext else "search_summary.json")).write_text(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

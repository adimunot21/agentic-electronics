"""PLUMBING CONTROLS ONLY for the frozen task improve-wide-retune-alias (policy v2). Reads the labelled controls from the frozen policy's `plumbing_controls`; there is NO search family and
no solving candidate for this task. Real controller + real EDA pipeline; scripted FIXTURE agent; no model.   uv run python scripts/improvement_controls_v2.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from improvement_search_fixture import scripted_agent, snapshot_evidence, summarize  # noqa: E402

from ae.harness.controller import Controller, Limits, Mode  # noqa: E402
from ae.harness.evaluator import TrustedEvaluator  # noqa: E402
from ae.harness.improvement import load_policy  # noqa: E402
from ae.harness.tasks import load_task  # noqa: E402
from ae.harness.workspace import REPO_ROOT  # noqa: E402

TASK_ID = "improve-wide-retune-alias"
OUT = REPO_ROOT / "runs/agent/improve-wide-retune-controls"


def main() -> int:
    policy = load_policy(REPO_ROOT / "src/ae/harness/tasks/improve_wide_retune_alias.yaml")
    controls = policy.raw["plumbing_controls"]
    evaluator = TrustedEvaluator()
    pristine = REPO_ROOT / "designs/fe-aaf-001/components.yaml"
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    baseline = evaluator.run_pipeline(pristine, OUT / "baseline")
    (OUT / "baseline_record.json").write_text(json.dumps(baseline, indent=1))
    print(f"baseline {time.monotonic() - t0:.1f}s digest={evaluator.digest_at_start['combined'][:12]} policy={policy.sha256[:12]}", flush=True)
    rows = []
    for label, c in controls.items():
        run_dir = OUT / label
        ctl = Controller(run_dir, load_task(TASK_ID), scripted_agent(c["ops"]), evaluator, Limits(max_iterations=1, max_model_calls=8), Mode.ENGINEERED_SINGLE,
                         pristine_manifest=pristine, baseline_record=baseline, baseline_dir=OUT / "baseline")
        t = time.monotonic()
        summary = ctl.run()
        snapshot_evidence(run_dir)
        row = summarize(label, run_dir, summary, time.monotonic() - t)
        row["expected_verdict"] = c["expected_verdict"]
        row["as_expected"] = row["verdict"] == c["expected_verdict"]
        rows.append(row)
        print(json.dumps({k: row.get(k) for k in ("label", "verdict", "expected_verdict", "as_expected", "wall_s", "policy_violation_rules", "patch_rejected_by_validator")}, default=str), flush=True)
    (OUT / "controls_summary.json").write_text(json.dumps({"task": TASK_ID, "policy_sha256": policy.sha256, "trusted_digest": evaluator.digest_at_start["combined"],
                                                         "fixture": True, "note": "plumbing controls, not solving candidates, not agent output", "rows": rows}, indent=1, default=str))
    return 0 if all(r["as_expected"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

"""CLI: run one harness task.

  uv run python -m ae.harness.run --task dev-c1-value --provider claude-cli            # LIVE model-driven run (needs the logged-in `claude` CLI)
  uv run python -m ae.harness.run --task dev-c1-value --provider scripted-fixture      # deterministic FIXTURE run of the machinery (labelled; not agent success)
  uv run python -m ae.harness.run --probe                                              # check that live model access works (one tiny call)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ClaudeCliProvider, ScriptedProvider
from ae.harness.tasks import list_tasks, load_task
from ae.harness.workspace import REPO_ROOT


def scripted_reference_agent() -> ScriptedProvider:
    """FIXTURE that applies the known answer. It tests the machinery only; it is NOT an agent repair."""
    def patch(transcript: str) -> str:
        import re
        sha = re.findall(r'"manifest_sha256": "([0-9a-f]{64})"', transcript)[-1]
        return json.dumps({"reasoning": "fixture", "action": {"tool": "propose_patch", "args": {"patch": {"summary": "fixture: restore C1", "base_sha256": sha,
                           "ops": [{"op": "set_field", "part": "C1", "field": "value", "to": "18n"}]}}}})
    return ScriptedProvider([{"reasoning": "fixture", "action": {"tool": "get_design", "args": {}}}, patch, {"reasoning": "fixture", "action": {"tool": "run_check", "args": {"check": "bom_consistency"}}},
                             {"reasoning": "fixture", "action": {"tool": "submit", "args": {"summary": "fixture restore"}}}])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ae.harness.run")
    ap.add_argument("--task", default="dev-c1-value")
    ap.add_argument("--provider", choices=["claude-cli", "scripted-fixture"], default="claude-cli")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--max-iterations", type=int, default=4)
    ap.add_argument("--max-model-calls", type=int, default=30)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-budget-usd", type=float, default=0.5, help="per-call cap passed to the CLI")
    ap.add_argument("--allow-heldout", action="store_true")
    ap.add_argument("--evaluate-defect", action="store_true", help="also run the full trusted pipeline on the defective design (evidence of the original failure)")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--list-tasks", action="store_true")
    a = ap.parse_args(argv)
    if a.list_tasks:
        print(json.dumps(list_tasks(), indent=1)); return 0
    if a.probe:
        r = ClaudeCliProvider(a.model, a.max_budget_usd).probe()
        print(json.dumps({"ok": r.error is None and r.text.strip() == "OK", "error": r.error, "text": r.text[:40], "model": r.model, "cost_usd_reported": r.cost_usd, "cost_basis": r.cost_basis, "elapsed_s": round(r.elapsed_s, 2)}))
        return 0 if r.error is None else 2
    task = load_task(a.task, a.allow_heldout)
    if task.kind == "improvement" and a.provider == "scripted-fixture":
        print("improvement tasks have no built-in scripted agent; deterministic candidates run via scripts/improvement_search_fixture.py", file=sys.stderr)
        return 2
    provider = ClaudeCliProvider(a.model, a.max_budget_usd) if a.provider == "claude-cli" else scripted_reference_agent()
    run_id = a.run_id or f"{a.task}-{a.provider}-{time.strftime('%Y%m%d-%H%M%S')}"
    ctl = Controller(REPO_ROOT / "runs/agent" / run_id, task, provider, TrustedEvaluator(), Limits(max_iterations=a.max_iterations, max_model_calls=a.max_model_calls),
                     Mode.ENGINEERED_SINGLE, evaluate_defect=a.evaluate_defect)
    summary = ctl.run()
    print(json.dumps(summary, indent=1, default=str))
    return 0 if summary["stop_reason"] == "accepted" else 1


if __name__ == "__main__":
    sys.exit(main())

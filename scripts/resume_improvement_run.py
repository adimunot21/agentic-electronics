"""Resume a STOPPED improvement run from its run directory (state, workspace, transcript restored by Controller.resume) with the same launch settings as ae.harness.run.
Used when a run ended on a provider-side interruption (e.g. the subscription's 5-hour usage limit), never to extend caps: the call/iteration counters continue where they stopped.
  env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 uv run python scripts/resume_improvement_run.py RUN_ID TASK_ID MODEL MAX_ITERATIONS MAX_MODEL_CALLS"""
from __future__ import annotations

import json
import sys

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ClaudeCliProvider
from ae.harness.tasks import load_task
from ae.harness.workspace import REPO_ROOT

run_id, task_id, model, max_iter, max_calls = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
ctl = Controller.resume(REPO_ROOT / "runs/agent" / run_id, task=load_task(task_id), provider=ClaudeCliProvider(model, 1000000.0), evaluator=TrustedEvaluator(),
                        limits=Limits(max_iterations=max_iter, max_model_calls=max_calls), mode=Mode.ENGINEERED_SINGLE)
summary = ctl.continue_run()
print(json.dumps(summary, indent=1, default=str))
sys.exit(0 if summary["stop_reason"] == "accepted" else 1)

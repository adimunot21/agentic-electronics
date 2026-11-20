"""Supervise an improvement run across the subscription's usage limits: while the run's stop reason is `provider_usage_limit`, wait (sleep only, NO API calls) until the recorded
reset time + margin, resume it, and repeat, up to --max-resumes. Any other stop reason (accepted, max_iterations, no_improvement_declared, ...) ends the supervision and is reported.
Subscription route only: it uses the same `claude` CLI provider as scripts/resume_improvement_run.py; no API key is read or needed. It never raises caps (counters continue).
  env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 uv run python scripts/supervise_improvement_run.py RUN_ID TASK_ID MODEL MAX_ITERATIONS MAX_MODEL_CALLS [--max-resumes 3] [--dry-run]
Exit codes: 0 accepted, 1 run ended on another stop reason, 2 refused (nothing resumable / reset unknown / wait too long / resume bound reached while still limited)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ae.harness.providers import usage_limit_wait_seconds, wait_for
from ae.harness.workspace import REPO_ROOT

EXIT_ACCEPTED, EXIT_ENDED, EXIT_REFUSED = 0, 1, 2


def _read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text())


def limited_wait(run_dir: Path, state: dict, now: datetime, margin_s: float) -> tuple[float | None, dict | None, bool]:
    """(seconds to wait, usage-limit info, is_usage_limit_stop). A stop counts as a usage limit when stop_reason is provider_usage_limit, or (runs recorded before that stop
    reason existed, e.g. improve-live-7/-8) provider_error whose last model_reply parses as a usage limit and whose state has no usage_limit of its own. A stale
    state['usage_limit'] left over from an earlier, already-resumed limit never makes a different stop wait."""
    stop = state.get("stop_reason")
    if stop != "provider_usage_limit" and not (stop == "provider_error" and "usage_limit" not in state):
        return None, None, False
    wait_s, info = usage_limit_wait_seconds(run_dir, now=now, margin_s=margin_s)
    if stop == "provider_error" and info is None:
        return None, None, False
    return wait_s, info, True


def supervise(run_dir: Path, resume_once: Callable[[], dict], *, max_resumes: int = 3, margin_s: float = 120.0, max_wait_s: float = 21600.0,
              sleep: Callable[[float], None] = time.sleep, now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc), log: Callable[[str], None] = print) -> tuple[int, dict]:
    """Core loop (clock, sleep and resume are injected so tests never wait or call a model). Returns (exit code, report)."""
    report: dict = {"resumes": 0, "waits_s": [], "final_stop_reason": None}
    while True:
        state = _read_state(run_dir)
        wait_s, info, is_limit = limited_wait(run_dir, state, now_fn(), margin_s)
        stop = state.get("stop_reason")
        if report["resumes"] == 0 and not is_limit:
            report["final_stop_reason"] = stop
            log(f"run is not stopped on a usage limit (stop_reason={stop!r}); nothing to supervise")
            return EXIT_REFUSED, report
        if is_limit:
            if report["resumes"] >= max_resumes:
                report["final_stop_reason"] = stop
                log(f"still usage-limited after {report['resumes']} resume(s); --max-resumes reached")
                return EXIT_REFUSED, report
            if wait_s is None:
                report["final_stop_reason"] = stop
                log("usage limit hit but no reset time is recorded/parseable; not sleeping (resume by hand after the reset)")
                return EXIT_REFUSED, report
            if wait_s > max_wait_s:
                report["final_stop_reason"] = stop
                log(f"reset is {wait_s:.0f}s away, longer than --max-wait-s {max_wait_s:.0f}; refusing to wait")
                return EXIT_REFUSED, report
            report["waits_s"].append(round(wait_s, 1))
            wait_for(wait_s, sleep=sleep, log=log)
            report["resumes"] += 1
            summary = resume_once()
            report["last_summary"] = summary
            continue
        report["final_stop_reason"] = stop
        return (EXIT_ACCEPTED if stop == "accepted" else EXIT_ENDED), report


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_id")
    ap.add_argument("task_id")
    ap.add_argument("model")
    ap.add_argument("max_iterations", type=int)
    ap.add_argument("max_model_calls", type=int)
    ap.add_argument("--max-resumes", type=int, default=3, help="bound on resume attempts across repeated limits")
    ap.add_argument("--margin-s", type=float, default=120.0)
    ap.add_argument("--max-wait-s", type=float, default=21600.0, help="refuse a single wait longer than this (default 6 h)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit 0; constructs no provider, calls nothing")
    ap.add_argument("--runs-dir", type=Path, default=REPO_ROOT / "runs/agent")
    return ap


def main(argv: list[str] | None = None, *, resume_once: Callable[[], dict] | None = None, sleep: Callable[[float], None] = time.sleep,
         now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.runs_dir / args.run_id
    try:
        state = _read_state(run_dir)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {run_dir / 'state.json'}: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if args.dry_run:
        wait_s, info, is_limit = limited_wait(run_dir, state, now_fn(), args.margin_s)
        print(json.dumps({"run_dir": str(run_dir), "stop_reason": state.get("stop_reason"), "usage_limit_stop": is_limit, "usage_limit": info, "seconds_to_wait": wait_s,
                          "max_resumes": args.max_resumes, "model_calls": state.get("model_calls"), "iteration": state.get("iteration")}, indent=1, default=str))
        return 0
    if resume_once is None:
        def resume_once() -> dict:                                   # lazy imports: dry runs and tests need no provider / evaluator
            from ae.harness.controller import Controller, Limits, Mode
            from ae.harness.evaluator import TrustedEvaluator
            from ae.harness.providers import ClaudeCliProvider
            from ae.harness.tasks import load_task
            ctl = Controller.resume(run_dir, task=load_task(args.task_id), provider=ClaudeCliProvider(args.model, 1000000.0), evaluator=TrustedEvaluator(),
                                    limits=Limits(max_iterations=args.max_iterations, max_model_calls=args.max_model_calls), mode=Mode.ENGINEERED_SINGLE)
            return ctl.continue_run()
    code, report = supervise(run_dir, resume_once, max_resumes=args.max_resumes, margin_s=args.margin_s, max_wait_s=args.max_wait_s, sleep=sleep, now_fn=now_fn)
    print(json.dumps(report, indent=1, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())

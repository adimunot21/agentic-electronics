"""Resume a STOPPED improvement run from its run directory (state, workspace, transcript restored by Controller.resume) with the same launch settings as ae.harness.run.
Used when a run ended on a provider-side interruption (e.g. the subscription's 5-hour usage limit), never to extend caps: the call/iteration counters continue where they stopped.
  env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 uv run python scripts/resume_improvement_run.py [--dry-run] [--wait-until-reset] RUN_ID TASK_ID MODEL MAX_ITERATIONS MAX_MODEL_CALLS
--dry-run prints the stop reason, parsed reset time, wait and preview budget used, and exits 0 without constructing a provider or evaluator.
--wait-until-reset sleeps (no API calls) until the recorded usage-limit reset plus --margin-s; exit 2 if the reset is unknown or further away than --max-wait-s."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ae.harness.controller import preview_spend
from ae.harness.providers import usage_limit_wait_seconds, wait_for
from ae.harness.workspace import REPO_ROOT

RESUMABLE_STOPS = {"provider_usage_limit", "provider_error"}
EXIT_REFUSED = 2


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_id")
    ap.add_argument("task_id")
    ap.add_argument("model")
    ap.add_argument("max_iterations", type=int)
    ap.add_argument("max_model_calls", type=int)
    ap.add_argument("--wait-until-reset", action="store_true", help="sleep until the recorded usage-limit reset (+ margin) before resuming; no API calls while waiting")
    ap.add_argument("--margin-s", type=float, default=120.0, help="seconds added after the reset time (clock skew, provider lag)")
    ap.add_argument("--max-wait-s", type=float, default=21600.0, help="refuse to wait longer than this (default 6 h)")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen and exit 0; constructs nothing, calls nothing")
    ap.add_argument("--runs-dir", type=Path, default=REPO_ROOT / "runs/agent", help="directory holding run directories")
    return ap


def _previews_used(run_dir: Path, state: dict) -> int:
    if "corner_previews_used" in state:
        return int(state["corner_previews_used"])
    trace = run_dir / "trace.jsonl"
    events = (json.loads(line) for line in trace.read_text().splitlines()) if trace.exists() else ()
    return sum(preview_spend(e) for e in events if e.get("type") == "tool_call" and e.get("tool") == "run_check")


def main(argv: list[str] | None = None, sleep=None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.runs_dir / args.run_id
    try:
        state = json.loads((run_dir / "state.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {run_dir / 'state.json'}: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    stop_reason = state.get("stop_reason")
    if stop_reason not in RESUMABLE_STOPS:
        print(f"WARNING: stop_reason is {stop_reason!r}, not a provider interruption ({sorted(RESUMABLE_STOPS)}); resuming does not extend caps", file=sys.stderr)
    wait_s, info = usage_limit_wait_seconds(run_dir, margin_s=args.margin_s)
    if args.dry_run:
        print(json.dumps({"run_dir": str(run_dir), "stop_reason": stop_reason, "usage_limit": info, "reset_at_iso": (info or {}).get("reset_at_iso"), "seconds_to_wait": wait_s,
                          "would_wait": bool(args.wait_until_reset and wait_s), "corner_previews_used": _previews_used(run_dir, state),
                          "model_calls": state.get("model_calls"), "iteration": state.get("iteration")}, indent=1, default=str))
        return 0
    if args.wait_until_reset:
        if wait_s is None:
            print("no usage-limit reset time is recorded for this run; not sleeping (resume without --wait-until-reset once the limit has reset)", file=sys.stderr)
            return EXIT_REFUSED
        if wait_s > args.max_wait_s:
            print(f"reset is {wait_s:.0f}s away, longer than --max-wait-s {args.max_wait_s:.0f}; refusing to wait", file=sys.stderr)
            return EXIT_REFUSED
        wait_for(wait_s, **({"sleep": sleep} if sleep else {}))
    from ae.harness.controller import Controller, Limits, Mode            # lazy: the dry run must not need the provider / evaluator
    from ae.harness.evaluator import TrustedEvaluator
    from ae.harness.providers import ClaudeCliProvider
    from ae.harness.tasks import load_task
    ctl = Controller.resume(run_dir, task=load_task(args.task_id), provider=ClaudeCliProvider(args.model, 1000000.0), evaluator=TrustedEvaluator(),
                            limits=Limits(max_iterations=args.max_iterations, max_model_calls=args.max_model_calls), mode=Mode.ENGINEERED_SINGLE)
    summary = ctl.continue_run()
    print(json.dumps(summary, indent=1, default=str))
    return 0 if summary["stop_reason"] == "accepted" else 1


if __name__ == "__main__":
    sys.exit(main())

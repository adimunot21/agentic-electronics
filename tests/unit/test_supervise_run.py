"""Usage-limit supervisor: fake clock, fake sleep, fake/scripted resume. No model, no real waiting, no launching of live runs."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task

from tests.unit.test_improvement import A
from tests.unit.test_improvement_feedback_loop import Rig, TASK_ID
from tests.unit.test_provider_limit import limit_reply

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("supervise_improvement_run", REPO / "scripts/supervise_improvement_run.py")
sup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sup)

NOW = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)


def write_state(run_dir: Path, stop: str | None, reset_in_s: float | None = 600, **extra) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"stop_reason": stop, "model_calls": 3, "iteration": 0, **extra}
    if reset_in_s is not None and stop == "provider_usage_limit":
        state["usage_limit"] = {"kind": "5-hour limit", "reset_at_iso": (NOW + timedelta(seconds=reset_in_s)).isoformat()}
    (run_dir / "state.json").write_text(json.dumps(state))


class Clock:
    def __init__(self):
        self.now, self.sleeps = NOW, []

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += timedelta(seconds=s)


def test_waits_until_reset_plus_margin_then_resumes_until_accepted(tmp_path):
    run = tmp_path / "r"
    write_state(run, "provider_usage_limit", 600)
    clock, calls = Clock(), []

    def resume_once():
        calls.append(clock.now)
        write_state(run, "accepted")
        return {"stop_reason": "accepted"}
    code, rep = sup.supervise(run, resume_once, margin_s=120, sleep=clock.sleep, now_fn=lambda: clock.now, log=lambda m: None)
    assert code == sup.EXIT_ACCEPTED and rep["resumes"] == 1 and rep["waits_s"] == [720.0]
    assert sum(clock.sleeps) == pytest.approx(720.0) and calls == [NOW + timedelta(seconds=720)]


def test_repeated_limits_loop_up_to_the_bound(tmp_path):
    run = tmp_path / "r"
    write_state(run, "provider_usage_limit", 60)
    clock, n = Clock(), {"n": 0}

    def resume_once():
        n["n"] += 1
        reset_in = 60 + (clock.now - NOW).total_seconds()
        (run / "state.json").write_text(json.dumps({"stop_reason": "provider_usage_limit", "usage_limit": {"reset_at_iso": (clock.now + timedelta(seconds=60)).isoformat()}}))
        return {}
    code, rep = sup.supervise(run, resume_once, max_resumes=2, margin_s=0, sleep=clock.sleep, now_fn=lambda: clock.now, log=lambda m: None)
    assert code == sup.EXIT_REFUSED and n["n"] == 2 and rep["resumes"] == 2 and rep["final_stop_reason"] == "provider_usage_limit"


def test_limit_then_other_stop_reason_ends_supervision_without_further_waiting(tmp_path):
    run = tmp_path / "r"
    write_state(run, "provider_usage_limit", 30)
    clock = Clock()

    def resume_once():
        write_state(run, "max_iterations", usage_limit={"reset_at_iso": NOW.isoformat()})          # stale usage_limit history must not cause a wait
        return {}
    code, rep = sup.supervise(run, resume_once, margin_s=0, sleep=clock.sleep, now_fn=lambda: clock.now, log=lambda m: None)
    assert code == sup.EXIT_ENDED and rep["final_stop_reason"] == "max_iterations" and rep["resumes"] == 1


@pytest.mark.parametrize("stop,reset_in,kw", [("accepted", None, {}), ("max_iterations", None, {}), ("provider_usage_limit", None, {}), ("provider_usage_limit", 99999, {})])
def test_refusals_never_resume_or_sleep(tmp_path, stop, reset_in, kw):
    run = tmp_path / "r"
    write_state(run, stop, reset_in)
    clock = Clock()
    code, rep = sup.supervise(run, lambda: pytest.fail("must not resume"), max_wait_s=21600, sleep=clock.sleep, now_fn=lambda: clock.now, log=lambda m: None)
    assert code == sup.EXIT_REFUSED and clock.sleeps == [] and rep["resumes"] == 0


def test_old_style_provider_error_run_with_a_usage_limit_trace_is_supervised(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "state.json").write_text(json.dumps({"stop_reason": "provider_error"}))
    line = {"seq": 1, "type": "model_reply", "text": "You've hit your 5-hour limit · resets 9:10am (Europe/London)", "error": "provider_error: You've hit your 5-hour limit · resets 9:10am (Europe/London) (api_error_status=429)"}
    (run / "trace.jsonl").write_text(json.dumps(line) + "\n")
    wait_s, info, is_limit = sup.limited_wait(run, {"stop_reason": "provider_error"}, datetime.now(timezone.utc), 0)
    assert is_limit and info is not None
    plain = tmp_path / "p"
    plain.mkdir()
    (plain / "state.json").write_text("{}")
    assert sup.limited_wait(plain, {"stop_reason": "provider_error"}, NOW, 0) == (None, None, False)          # a plain provider error is never waited on


def test_end_to_end_with_real_controller_and_scripted_provider_hitting_the_limit_twice(tmp_path):
    rig = Rig(tmp_path)
    task_limits = Limits(max_iterations=3, max_model_calls=30)
    rig.controller([A("get_design"), limit_reply()], task_limits).run()
    run_dir, clock, scripts = tmp_path / "run", Clock(), [[limit_reply()], [A("get_design"), A("declare_no_improvement", summary="done")]]
    clock.now = datetime.now(timezone.utc)                                                         # the parsed reset is real wall-clock based

    def resume_once():
        ev = TrustedEvaluator()
        ev.run_pipeline = rig._pipe
        ctl = Controller.resume(run_dir, task=load_task(TASK_ID), provider=ScriptedProvider(scripts.pop(0)), evaluator=ev, limits=task_limits, mode=Mode.ENGINEERED_SINGLE,
                                baseline_record=rig.base, baseline_dir=rig.base_dir, checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names})
        return ctl.continue_run()
    code, rep = sup.supervise(run_dir, resume_once, max_resumes=3, margin_s=60, max_wait_s=10**6, sleep=clock.sleep, now_fn=lambda: clock.now, log=lambda m: None)
    assert code == sup.EXIT_ENDED and rep["final_stop_reason"] == "no_improvement_declared" and rep["resumes"] == 2 and len(clock.sleeps) >= 2
    assert json.loads((run_dir / "state.json").read_text())["usage_limit_hits"] == 2


def test_dry_run_and_cli_construct_nothing(tmp_path, capsys):
    write_state(tmp_path / "r1", "provider_usage_limit", 120)
    assert sup.main(["r1", "t", "m", "3", "10", "--runs-dir", str(tmp_path), "--dry-run"], now_fn=lambda: NOW) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["usage_limit_stop"] is True and out["seconds_to_wait"] == pytest.approx(240.0)
    assert sup.main(["missing", "t", "m", "3", "10", "--runs-dir", str(tmp_path)]) == sup.EXIT_REFUSED
    clock = Clock()
    assert sup.main(["r1", "t", "m", "3", "10", "--runs-dir", str(tmp_path)], resume_once=lambda: (write_state(tmp_path / "r1", "accepted"), {})[1], sleep=clock.sleep, now_fn=lambda: clock.now) == sup.EXIT_ACCEPTED

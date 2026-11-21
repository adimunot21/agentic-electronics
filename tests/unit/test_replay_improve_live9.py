"""Deterministic replay of the real improve-live-9 replies (45 model calls, 21 propose_patch, 17 edit_sanity, 1 preview, 0 submits, stop max_model_calls) through the hardened controller.
The model's exact replies come from tests/unit/data/improve_live9_replies.json (extracted from the run's trace.jsonl); tools run for real (patches, ideal-model edit_sanity), the corner/requirements
sweeps are stubbed. No model, no docker. The replay reproduces the stop, and shows the new budget display and advisory reminders that the live run did not have."""
from __future__ import annotations

import json
from pathlib import Path

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task

from tests.unit.test_improvement import synth_pipeline, PRISTINE

DATA = json.loads((Path(__file__).parent / "data" / "improve_live9_replies.json").read_text())
TASK_ID = "improve-wide-retune-alias-v4"


def stub_checks(names, manifest, workdir):
    if "bom_consistency" in names:
        return {n: {"outcome": "passed"} for n in names}
    res = {"corner_preview": {"outcome": "outside_contract_limits", "measurements": {"alias_corners_worst_dB": -59.2, "flatness_corners_worst_dB": 0.40, "gain_corners_min_V_per_V": 1.24, "gain_corners_max_V_per_V": 1.37}},
           "requirements_preview": {"outcome": "info", "requirements": {}}}
    return {n: res[n] for n in names}


def replay(tmp_path, limits=None, replies=None):
    base_dir = tmp_path / "base"
    base = synth_pipeline(PRISTINE, base_dir)
    ev = TrustedEvaluator()
    ev.run_pipeline = lambda c, o: synth_pipeline(c, o)
    provider = ScriptedProvider(replies if replies is not None else DATA["replies"])
    ctl = Controller(tmp_path / "run", load_task(TASK_ID), provider, ev, limits or Limits(**DATA["limits"]), Mode.ENGINEERED_SINGLE, baseline_record=base, baseline_dir=base_dir, checks_fn=stub_checks)
    return ctl, ctl.run()


def events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "run/trace.jsonl").read_text().splitlines()]


def test_replay_reproduces_the_live9_stop(tmp_path):
    ctl, summary = replay(tmp_path)
    assert summary["stop_reason"] == "max_model_calls" and summary["model_calls"] == 45 and summary["evaluations"] == 0 and summary["iterations"] == 0
    tools = [e["tool"] for e in events(tmp_path) if e["type"] == "tool_call"]
    assert tools.count("propose_patch") == 21 and tools.count("run_check") == 18          # 17 edit_sanity + 1 preview, as counted in the live trace
    assert ctl.state["free_check_calls"] == 17


def test_hardened_controller_shows_the_budget_and_sends_the_reminders_once_each(tmp_path):
    ctl, _ = replay(tmp_path)
    ev = events(tmp_path)
    first = next(e for e in ev if e["type"] == "message" and e["role"] == "user")["content"]
    assert "BUDGET: 45 model calls" in first and "3 evaluations" in first and "12 preview units" in first
    feedback = [json.loads(e["content"].split("TOOL RESULT:\n", 1)[1]) for e in ev if e["type"] == "message" and e["role"] == "user" and "TOOL RESULT:" in e["content"]]
    tool_results = [f for f in feedback if "budget" in f]
    assert tool_results and all({"model_calls_left", "evaluations_left", "previews_left", "edit_sanity_calls"} <= set(f["budget"]) for f in tool_results)
    reminders = [e for e in ev if e["type"] == "budget_reminder"]
    assert [r["level"] for r in reminders] == [1, 2]                                         # at 40 % (18 calls) and 70 % (32 calls), once each
    assert reminders[0]["model_calls"] >= 18 and reminders[1]["model_calls"] >= 32 and reminders[0]["model_calls_left"] > 20
    texts = [f["reminder"] for f in feedback if "reminder" in f]
    assert len(texts) == 2 and "facts only" in texts[0] and "declare_no_improvement" in texts[0]


def test_reminders_change_nothing_but_the_feedback(tmp_path):
    """Same replies, reminders disabled: identical tool sequence, counters and stop (the reminder is feedback text only; no refusal, cap or acceptance logic moved)."""
    a, sa = replay(tmp_path / "a")
    b, sb = replay(tmp_path / "b", limits=Limits(**DATA["limits"], nudge_fractions=()))
    assert [x for x in sa if x not in ("run_dir", "elapsed_s", "usage")] == [x for x in sb if x not in ("run_dir", "elapsed_s", "usage")]
    keys = ("stop_reason", "model_calls", "tool_calls", "evaluations", "iterations")
    assert {k: sa[k] for k in keys} == {k: sb[k] for k in keys}
    assert "nudges_sent" not in b.state and a.state["nudges_sent"] == 2


def test_no_reminder_once_something_was_submitted_or_in_restoration_mode(tmp_path):
    from tests.unit.test_harness import make
    ctl = make(tmp_path / "r", [])
    ctl.run()
    assert not any(e["type"] == "budget_reminder" for e in events(tmp_path / "r"))
    ctl2, _ = replay(tmp_path / "x")
    ctl2.state["evaluations"] = 1
    assert ctl2._nudge() is None


def test_reminder_is_factual_and_does_not_steer(tmp_path):
    ctl, _ = replay(tmp_path)
    ev = events(tmp_path)
    texts = [json.loads(e["content"].split("TOOL RESULT:\n", 1)[1])["reminder"] for e in ev if e["type"] == "message" and e["role"] == "user" and '"reminder"' in e["content"]]
    assert len(texts) == 2
    for t in texts:
        assert "model calls used" in t and "evaluations used: 0 of 3" in t and "Only `submit` produces an evaluator verdict" in t and "BOM, status, series or row rules" in t and "declare_no_improvement" in t
        assert "Do not submit" not in t and "WOULD_BE_REJECTED" not in t and "if none is justified" not in t and "you decide" not in t


def test_budget_is_in_every_improvement_feedback_including_invalid_replies_and_refused_submits(tmp_path):
    from tests.unit.test_improvement import A
    ctl, _ = replay(tmp_path, replies=["not json", json.dumps(A("get_design")), json.dumps(A("submit", summary="x")), json.dumps(A("submit", summary="x"))], limits=Limits(max_iterations=3, max_model_calls=10))
    fbs = [e["content"] for e in events(tmp_path) if e["type"] == "message" and e["role"] == "user" and "TOOL RESULT:" in e["content"]]
    assert len(fbs) >= 3 and all('"budget"' in f and f.index('"budget"') < 20 for f in fbs)
    assert any("invalid_reply" in f for f in fbs) and any("no_change_from_baseline" in f for f in fbs)


def test_resume_of_a_pre_guard_state_does_not_fire_passed_levels(tmp_path):
    ctl, _ = replay(tmp_path, replies=DATA["replies"][:20])
    st = json.loads((tmp_path / "run/state.json").read_text())
    st.pop("nudges_sent", None)
    st["stop_reason"] = "provider_usage_limit"
    st["model_calls"] = 34                                                                  # past both levels (18 and 32 of 45), state written before the guard existed
    (tmp_path / "run/state.json").write_text(json.dumps(st))
    ev = TrustedEvaluator()
    ev.run_pipeline = lambda c, o: synth_pipeline(c, o)
    resumed = Controller.resume(tmp_path / "run", task=load_task(TASK_ID), provider=ScriptedProvider([]), evaluator=ev, limits=Limits(**DATA["limits"]), mode=Mode.ENGINEERED_SINGLE,
                                baseline_record=json.loads((tmp_path / "run/baseline_record.json").read_text()), baseline_dir=tmp_path / "base", checks_fn=stub_checks)
    assert resumed.state["nudges_sent"] == 2 and resumed._nudge() is None


def test_sanity_band_wording_is_neutral():
    from ae.harness.sanity import _limit_check
    w = _limit_check("GAIN_CORNER_MAX", "corner gain max", 1.3650, 1.3645, 0.001, True, " V/V", "lower the gain")
    assert w["severity"] == "warn" and "may or may not be a real breach; the evaluator decides" in w["message"] and "false alarm" not in w["message"]

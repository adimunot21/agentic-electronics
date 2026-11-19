"""Feedback-loop semantics (improvement mode): feedback before stop, zero-diff/duplicate refusal before evaluation, progress = objective movement + feasibility.
Stub pipelines, no model, no docker. The 'gain breach' pipeline REPLAYS the shape of improve-live-2 iteration 2 (objective met, one gain-corner breach reported as H3 and H7)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task
from ae.harness.patch import sha256_text

from tests.unit.test_improvement import A, PRISTINE, patch_ops, synth_pipeline

TASK_ID = "improve-wide-retune-alias-v3"
NOOP = patch_ops([("C2", "mpn_candidate", "C0805C102J5GACTA")])
NOOP2 = patch_ops([("C2", "mpn_candidate", "C0805C102J5GACTB")])
NOOP3 = patch_ops([("C2", "mpn_candidate", "C0805C102J5GACTC")])
BREACH = patch_ops([("C1", "value", "20n")])


def breach_pipeline(components_path, out_dir):
    """FIXTURE shaped like improve-live-2 iteration 2: objective +0.916 dB but corner gain max 1.373 > 1.3645 (REQ-GAIN-CORNERS failed => H3 AND H7)."""
    rec = synth_pipeline(components_path, out_dir)
    if "20n" in Path(components_path).read_text() and 'C1: {role: "MFB node-X capacitor (to GND)", value: "20n"' in Path(components_path).read_text():
        for c in rec["checks"]:
            if c["id"] == "REQ-ALIAS-CORNERS":
                c["value"] = -59.651
            if c["id"] == "REQ-GAIN-CORNERS":
                c["outcome"], c["value"] = "failed", 1.373
        (out_dir / "corners_summary.json").write_text(json.dumps({"gain_min": 1.2505, "gain_max": 1.3730}))
        (out_dir / "record.json").write_text(json.dumps(rec))
    return rec


class Rig:
    def __init__(self, tmp_path, pipeline=None):
        self.calls, self.root = [], tmp_path
        self.base_dir = tmp_path / "base"
        self.base = synth_pipeline(PRISTINE, self.base_dir)
        self.pipeline = pipeline or synth_pipeline

    def _pipe(self, components_path, out_dir):
        self.calls.append(Path(out_dir).name)
        return self.pipeline(components_path, out_dir)

    def controller(self, script, limits):
        ev = TrustedEvaluator()
        ev.run_pipeline = self._pipe
        self.provider = ScriptedProvider(script)
        self.ctl = Controller(self.root / "run", load_task(TASK_ID), self.provider, ev, limits, Mode.ENGINEERED_SINGLE, baseline_record=self.base, baseline_dir=self.base_dir,
                              checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names})
        return self.ctl

    def events(self):
        return [json.loads(l) for l in (self.root / "run/trace.jsonl").read_text().splitlines()]


def last_result(transcript: str) -> dict:
    return json.loads(transcript.rsplit("TOOL RESULT:\n", 1)[1].split("\n\n[", 1)[0])


# ---------------------------------------------------------------- 2. zero-diff and duplicate designs are refused BEFORE evaluation
def test_unchanged_design_is_refused_without_evaluation_or_progress_effects(tmp_path):
    rig = Rig(tmp_path)
    seen = {}
    ctl = rig.controller([A("get_design"), A("submit", summary="nothing"), lambda t: (seen.update(t=t) or json.dumps(A("get_design")))], Limits(max_iterations=3, max_model_calls=3))
    ctl.run()
    s = ctl.state
    assert rig.calls == [] and (s["iteration"], s["evaluations"], s["no_progress"], s["refused_submits"]) == (0, 0, 0, 1)
    fb = last_result(seen["t"])
    assert fb["error"]["code"] == "no_change_from_baseline" and "nothing was evaluated" in fb["note"]
    assert not (tmp_path / "run/evaluations").exists()


def test_duplicate_design_is_refused_and_does_not_touch_the_evaluated_design_counters(tmp_path):
    rig = Rig(tmp_path)
    ctl = rig.controller([A("get_design"), NOOP, A("submit", summary="a"), A("submit", summary="b"), A("get_design")], Limits(max_iterations=5, max_model_calls=5))
    ctl.run()
    s = ctl.state
    assert len(rig.calls) == 1 and (s["iteration"], s["evaluations"], s["refused_submits"]) == (1, 1, 1)
    assert s["no_progress"] == 1                                         # from the one evaluated no-op only; the refusal added nothing
    assert len(s["evaluated_manifest_sha256"]) == 1


def test_repeated_refusals_are_bounded_and_stop_the_run(tmp_path):
    rig = Rig(tmp_path)
    ctl = rig.controller([A("get_design")] + [A("submit", summary="again")] * 6, Limits(max_iterations=3, max_model_calls=20))
    s = ctl.run()
    assert s["stop_reason"] == "repeated_refusals" and rig.calls == [] and ctl.state["refused_submits"] == 3 and s["model_calls"] == 4 and s["evaluations"] == 0


# ---------------------------------------------------------------- 3. progress semantics (improve-live-2 replay)
def test_live2_replay_objective_met_with_one_gain_breach_is_progress_and_the_agent_gets_the_feedback_before_any_stop(tmp_path):
    rig = Rig(tmp_path, breach_pipeline)
    seen = {}

    def capture(t):
        seen["t"], seen["pending_when_called"] = t, rig.ctl.state["pending_feedback"]
        return json.dumps(A("get_design"))
    ctl = rig.controller([A("get_design"), NOOP, A("submit", summary="noop"), A("get_design"), BREACH, A("submit", summary="c1"), capture], Limits(max_iterations=3, max_model_calls=10))
    s = ctl.run()
    assert s["stop_reason"] != "no_progress"                                          # the live-2 run stopped HERE; progress now recognises the objective movement
    d2 = json.loads((tmp_path / "run/evaluations/decision_02.json").read_text())
    assert d2["verdict"] == "rejected" and {v["rule"] for v in d2["policy_violations"]} == {"H3_gain_max_corners", "H7_passed_stays_passed"}
    prog = [e for e in rig.events() if e["type"] == "progress_assessment"]
    assert prog[1]["broken_checks"] == ["REQ-GAIN-CORNERS"] and prog[1]["better_objective"] is True      # H3 + H7 are ONE broken check
    assert prog[0]["better_objective"] is False and prog[0]["better_feasibility"] is False
    fb = last_result(seen["t"])                                                         # delivered to the model call that follows the rejection
    assert seen["pending_when_called"] is False
    h3 = next(f for f in fb["findings"] if f["rule"] == "H3_gain_max_corners")
    assert h3["value"] == 1.373 and h3["max"] == 1.3645 and fb["objective"]["improvement"] == pytest.approx(0.9158, abs=1e-3) and fb["objective"]["met"] is True
    assert ctl.state["no_progress"] == 0


def test_no_progress_stops_a_new_submission_only_after_the_agent_has_read_the_feedback(tmp_path):
    rig = Rig(tmp_path)
    script = [A("get_design"), NOOP, A("submit", summary="1"), A("get_design"), NOOP2, A("submit", summary="2"), A("get_design"), NOOP3, A("submit", summary="3")]
    ctl = rig.controller(script, Limits(max_iterations=5, max_model_calls=20))
    s = ctl.run()
    assert s["stop_reason"] == "no_progress" and len(rig.calls) == 2 and ctl.state["evaluations"] == 2        # the 3rd submission was gated, not evaluated
    assert s["final_feedback_delivered"] is True                                                          # the agent had read the 2nd rejection before the gate fired


# ---------------------------------------------------------------- 1. feedback before stop
def test_when_the_evaluation_budget_is_spent_the_feedback_is_built_recorded_and_flagged_undelivered(tmp_path):
    rig = Rig(tmp_path, breach_pipeline)
    ctl = rig.controller([A("get_design"), BREACH, A("submit", summary="c1")], Limits(max_iterations=1, max_model_calls=10))
    s = ctl.run()
    assert s["stop_reason"] == "max_iterations" and s["final_feedback_delivered"] is False and s["model_calls"] == 3      # no extra model call to deliver it
    undelivered = [e for e in rig.events() if e["type"] == "feedback_undelivered"]
    assert len(undelivered) == 1 and "H3_gain_max_corners" in undelivered[0]["payload"] and "1.373" in undelivered[0]["payload"]
    stopped = [e for e in rig.events() if e["type"] == "transition" and e["to"] == "STOPPED"][0]
    assert stopped["feedback_delivered"] is False


def test_model_call_cap_after_a_rejection_does_not_exceed_the_cap_and_records_undelivered(tmp_path):
    rig = Rig(tmp_path, breach_pipeline)
    ctl = rig.controller([A("get_design"), BREACH, A("submit", summary="c1"), A("get_design")], Limits(max_iterations=3, max_model_calls=3))
    s = ctl.run()
    assert s["stop_reason"] == "max_model_calls" and s["model_calls"] == 3 and rig.provider.i == 3
    assert s["final_feedback_delivered"] is False and "H3_gain_max_corners" in ctl.messages[-1]["content"]      # built and in the transcript, but no call left to carry it


def test_a_stop_after_feedback_was_consumed_reports_delivered(tmp_path):
    rig = Rig(tmp_path, breach_pipeline)
    ctl = rig.controller([A("get_design"), BREACH, A("submit", summary="c1"), A("get_design")], Limits(max_iterations=3, max_model_calls=4, max_tool_calls=2))
    s = ctl.run()
    assert s["stop_reason"] in ("max_model_calls", "max_tool_calls") and s["final_feedback_delivered"] is not None

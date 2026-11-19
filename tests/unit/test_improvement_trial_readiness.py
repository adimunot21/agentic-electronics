"""Pre-trial hardening tests for improvement mode (NO model, NO docker, NO network; all pipelines are stubs).

Four questions a live trial depends on, each answered by a deterministic test:
  1. resume after an interrupted run restores counters / policy / patches / evaluation de-duplication without re-running or double-counting finished evaluations;
  2. exactly what the agent is shown after each submit (accepted / rejected / no-op / duplicate / last-iteration);
  3. how every kind of agent action is charged against iterations, model calls, tool calls and evaluations (two separate classes: pre-dispatch rejections vs evaluated submissions);
  4. what a process death or a killed pipeline subprocess during a full evaluation leaves behind, and what resume does with it.
Fixtures: ScriptedProvider replies and synth_pipeline numbers are FIXTURES, not agent results.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ModelProvider, ScriptedProvider
from ae.harness.tasks import load_task
from ae.harness.patch import sha256_text
from ae.harness.workspace import REPO_ROOT

from tests.unit.test_improvement import A, PRISTINE, patch_ops, synth_pipeline

GOOD = [("R4", "value", "17.4k"), ("R4", "mpn_candidate", "RC0603FR-0717K4L"), ("R5", "value", "17.4k"), ("R5", "mpn_candidate", "RC0603FR-0717K4L")]
REGRESSION = [("C3", "value", "5.1n"), ("C3", "mpn_candidate", "C0805C512J5GACTU")]


class Crash(BaseException):
    """Simulates sudden process death: BaseException, so no `except Exception` in the harness can absorb it."""


class CrashingProvider(ModelProvider):
    """Scripted replies; raises Crash INSTEAD of answering call number `crash_at` (1-based)."""
    name, fixture = "scripted-fixture", True

    def __init__(self, script, crash_at):
        self.inner, self.crash_at, self.n = ScriptedProvider(script), crash_at, 0

    def complete(self, system, transcript, timeout_s):
        self.n += 1
        if self.n == self.crash_at:
            raise Crash("process died while waiting for the model")
        return self.inner.complete(system, transcript, timeout_s)


class Harness:
    """Builds controllers over one run directory with a counting stub pipeline."""

    def __init__(self, tmp_path: Path, pipeline=None):
        self.root, self.calls, self.pipeline_override = tmp_path, [], pipeline
        self.base_dir = tmp_path / "base"
        self.base = synth_pipeline(PRISTINE, self.base_dir)

    def _pipeline(self, components_path, out_dir):
        self.calls.append(Path(out_dir).name)
        if self.pipeline_override:
            return self.pipeline_override(components_path, out_dir)
        return synth_pipeline(components_path, out_dir)

    def kwargs(self, provider, limits):
        ev = TrustedEvaluator()
        ev.run_pipeline = self._pipeline
        return dict(task=load_task("improve-stage2-alias"), provider=provider, evaluator=ev, limits=limits, mode=Mode.ENGINEERED_SINGLE, baseline_record=self.base,
                    baseline_dir=self.base_dir, checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names})

    def new(self, script, limits=Limits(max_iterations=3), provider=None):
        return Controller(self.root / "run", provider=provider or ScriptedProvider(script), **{k: v for k, v in self.kwargs(None, limits).items() if k != "provider"})

    def resume(self, script, limits=Limits(max_iterations=3)):
        kw = self.kwargs(ScriptedProvider(script), limits)
        return Controller.resume(self.root / "run", **kw)

    @property
    def run(self) -> Path:
        return self.root / "run"

    def state(self) -> dict:
        return json.loads((self.run / "state.json").read_text())

    def events(self) -> list[dict]:
        return [json.loads(l) for l in (self.run / "trace.jsonl").read_text().splitlines()]


def counters(state: dict) -> dict:
    return {k: state[k] for k in ("iteration", "evaluations", "model_calls", "tool_calls", "invalid_replies", "no_progress")}


def last_tool_result(transcript: str) -> dict:
    text = transcript.rsplit("TOOL RESULT:\n", 1)[1]
    return json.loads(text.split("\n\n[", 1)[0])


# =================================================================== TASK 1: resume after an interrupted improvement run
def test_resume_after_crash_between_iterations_restores_everything_and_does_not_respend(tmp_path):
    h = Harness(tmp_path)
    # iteration 1: submit the unchanged design (a no-op => genuinely evaluated, rejected); the process then dies while waiting for the next model reply
    ctl = h.new(None, provider=CrashingProvider([A("get_design"), A("submit", summary="no change"), A("get_design")], crash_at=3))
    with pytest.raises(Crash):
        ctl.run()
    before = h.state()
    assert counters(before) == {"iteration": 1, "evaluations": 1, "model_calls": 2, "tool_calls": 1, "invalid_replies": 0, "no_progress": 0}
    assert before["evaluated_manifest_sha256"] == [sha256_text(PRISTINE.read_text())] and len(h.calls) == 1
    messages_before = list(ctl.messages)
    spent_before = dict(before["usage"])

    r = h.resume([A("get_design"), patch_ops(GOOD), A("submit", summary="good")])
    # restored without any evaluation or model call
    assert counters(r.state) == counters(before) and r.state["usage"] == spent_before and len(h.calls) == 1
    assert r.policy is not None and r.policy.sha256 == "d278109a50e8a9f161a41f07bbdd9d5157f0d757ab5f37aea62e7599f88aabdc"
    assert r.messages[:len(messages_before)] == messages_before                                  # transcript rebuilt from the trace == what the agent had seen
    assert r.ws.sha256() == sha256_text(PRISTINE.read_text()) and r.ws.history == []
    summary = r.continue_run()
    assert summary["stop_reason"] == "accepted" and summary["final_state"] == "ACCEPTED"
    after = h.state()
    # counters CONTINUE (not restart, not double): 1 old evaluation + 1 new; model calls 2 old + 3 new; tool calls 1 old + 2 new (get_design, propose_patch)
    assert counters(after) == {"iteration": 2, "evaluations": 2, "model_calls": 5, "tool_calls": 3, "invalid_replies": 0, "no_progress": r.state["no_progress"]}
    assert h.calls == ["iter_01", "iter_02"]                                                      # the finished evaluation was NOT run again
    assert len(after["evaluated_manifest_sha256"]) == 2 and len(set(after["evaluated_manifest_sha256"])) == 2
    assert sorted(p.name for p in (h.run / "evaluations").glob("decision_*.json")) == ["decision_01.json", "decision_02.json"]
    assert [p["seq"] for p in r.ws.history] == [1] and (h.run / "workspace/patches/001.json").exists()
    assert (h.run / "output/evaluator_decision.json").exists() and json.loads((h.run / "summary.json").read_text())["fixture"] is True
    assert any(e["type"] == "resumed" for e in h.events())


def test_resumed_run_does_not_reevaluate_an_already_evaluated_design(tmp_path):
    h = Harness(tmp_path)
    ctl = h.new(None, provider=CrashingProvider([A("get_design"), A("submit", summary="no change"), A("get_design")], crash_at=3))
    with pytest.raises(Crash):
        ctl.run()
    r = h.resume([A("submit", summary="same design again"), A("get_design")], Limits(max_iterations=3, max_model_calls=4))
    r.continue_run()
    s = h.state()
    assert len(h.calls) == 1 and s["iteration"] == 1 and s["evaluations"] == 1           # de-duplication survived the restart: no 2nd pipeline run, no iteration charged
    assert s["no_progress"] == 1 and s["model_calls"] == 4                                # the model call that produced the duplicate IS charged
    assert any(e["type"] == "submit_rejected_duplicate" for e in h.events())


def test_resume_keeps_policy_scope_so_out_of_scope_edits_still_fail(tmp_path):
    h = Harness(tmp_path)
    ctl = h.new(None, provider=CrashingProvider([A("get_design"), A("get_design")], crash_at=2))
    with pytest.raises(Crash):
        ctl.run()
    r = h.resume([A("get_design"), patch_ops([("R1", "value", "60.4k")]), A("get_design")], Limits(max_iterations=3, max_model_calls=5))
    r.continue_run()
    codes = [e["result"]["error"]["code"] for e in h.events() if e["type"] == "tool_call" and not e["result"]["ok"]]
    assert codes == ["out_of_scope"] and r.ws.history == [] and r.ws.sha256() == sha256_text(PRISTINE.read_text())


# =================================================================== TASK 2: what the agent sees after submit
def run_to_next_call(h: Harness, edit_ops, limits=Limits(max_iterations=2)):
    """Run [get_design, (patch), submit, <capture>] and return (controller, the TOOL RESULT the agent saw after submit)."""
    seen = {}

    def capture(transcript):
        seen["transcript"] = transcript
        return json.dumps(A("get_design"))
    script = [A("get_design")] + ([patch_ops(edit_ops)] if edit_ops else []) + [A("submit", summary="s"), capture]
    ctl = h.new(script, limits)
    ctl.run()
    return ctl, seen


def test_feedback_after_rejected_regression_has_verdict_reasons_and_summary_metrics_but_no_per_corner_data(tmp_path):
    h = Harness(tmp_path)
    ctl, seen = run_to_next_call(h, REGRESSION)
    fb = last_tool_result(seen["transcript"])
    assert set(fb) == {"ok", "evaluator_verdict", "findings", "objective", "circuit_requirement_failures", "unresolved_baseline_failures_not_yours", "note"}
    assert fb["ok"] is False and fb["evaluator_verdict"] == "rejected"
    assert fb["objective"]["check"] == "REQ-ALIAS-CORNERS" and fb["objective"]["candidate"] == -57.9 and fb["objective"]["improvement"] < 0     # the corner-based summary VALUE is disclosed
    assert {f["rule"] for f in fb["findings"]} >= {"objective_improvement"}
    text = json.dumps(fb)
    for hidden in ("corners_summary", "per_corner", "corner_index", "release_ready", "record_path", "trusted_digest", "policy_sha256", "elapsed_s"):
        assert hidden not in text                                                                # no per-corner table, no paths, no hashes, no evaluator internals
    assert len(text) < 6000                                                                      # not truncated by _feedback


def test_feedback_after_noop_says_no_change_with_zero_improvement(tmp_path):
    h = Harness(tmp_path)
    ctl, seen = run_to_next_call(h, [("C3", "value", "5.60n")])
    fb = last_tool_result(seen["transcript"])
    assert fb["evaluator_verdict"] == "rejected" and fb["objective"]["improvement"] == 0 and fb["objective"]["met"] is False
    assert [f["kind"] for f in fb["findings"] if f["rule"] == "objective_improvement"] == ["no_change_within_jitter"]


def test_feedback_after_unchanged_design_then_duplicate_submit(tmp_path):
    h = Harness(tmp_path)
    seen = {}

    def capture(transcript):
        seen["t"] = transcript
        return json.dumps(A("get_design"))
    ctl = h.new([A("get_design"), A("submit", summary="a"), A("submit", summary="b"), capture], Limits(max_iterations=3, no_progress_limit=3))
    ctl.run()
    fb = last_tool_result(seen["t"])
    assert fb == {"ok": False, "error": {"code": "already_evaluated", "message": "this exact design was already evaluated and rejected; change the design before submitting again"}}


def test_accepted_submission_sends_nothing_back_to_the_agent_run_just_ends(tmp_path):
    h = Harness(tmp_path)
    ctl = h.new([A("get_design"), patch_ops(GOOD), A("submit", summary="s")], Limits(max_iterations=2))
    s = ctl.run()
    assert s["stop_reason"] == "accepted"
    assert ctl.messages[-1]["role"] == "assistant" and '"submit"' in ctl.messages[-1]["content"]          # last thing in the transcript is the agent's own submit
    ev = h.events()
    idx = max(i for i, e in enumerate(ev) if e["type"] == "evaluation")
    assert not [e for e in ev[idx + 1:] if e["type"] == "message"]                                       # no feedback message after the evaluation
    assert (h.run / "output/evaluator_decision.json").exists()                                           # the human-facing record exists


def test_rejection_on_the_last_allowed_iteration_is_not_reported_to_the_agent(tmp_path):
    h = Harness(tmp_path)
    ctl = h.new([A("get_design"), patch_ops(REGRESSION), A("submit", summary="s")], Limits(max_iterations=1))
    s = ctl.run()
    assert s["stop_reason"] == "max_iterations"
    assert ctl.messages[-1]["role"] == "assistant"                                                       # the agent never learns why
    assert json.loads((h.run / "evaluations/decision_01.json").read_text())["verdict"] == "rejected"      # recorded for humans


# =================================================================== TASK 3: attempt accounting. CLASS 1 = rejected BEFORE any evaluation, CLASS 2 = evaluated
BAD_REPLIES = {
    "malformed_json": "{this is not json",
    "no_json_at_all": "I will just edit the file",
    "multiple_actions": '{"action": {"tool": "get_design"}} {"action": {"tool": "submit", "args": {"summary": "s"}}}',
    "array_of_actions": '[{"action": {"tool": "get_design"}}, {"action": {"tool": "get_design"}}]',
    "nonfinite_nan": '{"action": {"tool": "run_check", "args": {"check": NaN}}}',
    "nonfinite_1e999": '{"action": {"tool": "get_design", "args": {"x": 1e999}}}',
    "unknown_action_key": '{"action": {"tool": "get_design", "shell": "rm -rf /"}}',
    "unknown_top_level_key": '{"action": {"tool": "get_design"}, "extra": 1}',
    "missing_action_key": '{"tool": "get_design"}',
}


@pytest.mark.parametrize("name", sorted(BAD_REPLIES))
def test_class1_unparseable_reply_charges_one_model_call_and_one_invalid_reply_and_nothing_else(tmp_path, name):
    h = Harness(tmp_path)
    ctl = h.new([BAD_REPLIES[name], A("get_design")], Limits(max_iterations=3, max_model_calls=2))
    sha = ctl.ws.sha256() if hasattr(ctl, "ws") else None
    ctl.run()
    ev = h.events()
    first = next(i for i, e in enumerate(ev) if e["type"] == "invalid_reply")
    assert ev[first - 1]["type"] == "model_reply" and ev[first - 1]["text"] == BAD_REPLIES[name]
    assert not [e for e in ev[:first] if e["type"] in ("tool_call", "evaluation", "submit_rejected_duplicate")]       # nothing dispatched before the rejection
    s = h.state()
    assert s["invalid_replies"] == 1 and s["iteration"] == 0 and s["evaluations"] == 0
    # model_calls: the bad reply (1) + the following valid get_design (2); tool_calls counts only that valid get_design
    assert (s["model_calls"], s["tool_calls"]) == (2, 1)
    assert h.calls == [] and not (h.run / "evaluations").exists() and not list((h.run / "workspace/patches").glob("*.json"))
    assert ctl.ws.sha256() == sha256_text(PRISTINE.read_text()) and ctl.ws.history == []


def test_class1_invalid_reply_limit_is_cumulative_not_consecutive(tmp_path):
    h = Harness(tmp_path)
    ctl = h.new([BAD_REPLIES["malformed_json"], A("get_design"), BAD_REPLIES["no_json_at_all"], A("get_design"), BAD_REPLIES["missing_action_key"], A("get_design")], Limits(max_iterations=3))
    s = ctl.run()
    assert s["stop_reason"] == "invalid_replies" and h.state()["invalid_replies"] == 3 and h.state()["tool_calls"] == 2        # valid actions in between did NOT reset the counter


REGISTRY_REJECTIONS = {
    "unknown_tool": lambda t: A("format_disk"),
    "invalid_check_name": lambda t: A("run_check", check="rm -rf"),
    "missing_required_arg": lambda t: A("decode_mpn"),
    "out_of_scope_patch": patch_ops([("R1", "value", "60.4k")]),
    "unknown_op": lambda t: A("propose_patch", patch={"summary": "s", "base_sha256": re.findall(r'"manifest_sha256": "([0-9a-f]{64})"', t)[-1], "ops": [{"op": "delete_part", "part": "R4", "field": "value", "to": "x"}]}),
    "stale_base_hash": lambda t: A("propose_patch", patch={"summary": "s", "base_sha256": "0" * 64, "ops": [{"op": "set_field", "part": "R4", "field": "value", "to": "17.4k"}]}),
}


def _as_text(item):
    return (lambda t: json.dumps(item(t))) if getattr(item, "__name__", "") == "<lambda>" else item


@pytest.mark.parametrize("name", sorted(REGISTRY_REJECTIONS))
def test_class1b_parsed_but_rejected_by_the_tool_layer_is_charged_as_a_tool_call_but_changes_nothing(tmp_path, name):
    h = Harness(tmp_path)
    ctl = h.new([A("get_design"), _as_text(REGISTRY_REJECTIONS[name])], Limits(max_iterations=3, max_model_calls=2))
    ctl.run()
    s = h.state()
    failed = [e for e in h.events() if e["type"] == "tool_call" and not e["result"]["ok"]]
    assert len(failed) == 1                                                                              # it WAS dispatched to the registry, and refused there
    assert (s["model_calls"], s["tool_calls"], s["invalid_replies"], s["iteration"], s["evaluations"]) == (2, 2, 0, 0, 0)
    assert h.calls == [] and not (h.run / "evaluations").exists() and not list((h.run / "workspace/patches").glob("*.json"))
    assert ctl.ws.sha256() == sha256_text(PRISTINE.read_text()) and ctl.ws.history == []


def test_class2_evaluated_submissions_charge_iteration_evaluation_and_one_model_call_but_no_tool_call(tmp_path):
    rows = {}
    for label, script in {"full_evaluation_rejected_regression": [A("get_design"), patch_ops(REGRESSION), A("submit", summary="s")],
                          "noop_unchanged_design": [A("get_design"), A("submit", summary="s")],
                          "noop_textual_change": [A("get_design"), patch_ops([("C3", "value", "5.60n")]), A("submit", summary="s")]}.items():
        sub = tmp_path / label
        sub.mkdir()
        h = Harness(sub)
        ctl = h.new(script, Limits(max_iterations=1))
        ctl.run()
        rows[label] = (counters(h.state()), len(h.calls), h.state()["stop_reason"])
    for label, (c, pipeline_runs, stop) in rows.items():
        assert c["iteration"] == 1 and c["evaluations"] == 1 and pipeline_runs == 1 and stop == "max_iterations", label
    assert rows["full_evaluation_rejected_regression"][0]["tool_calls"] == 2 and rows["noop_unchanged_design"][0]["tool_calls"] == 1     # get_design (+ propose_patch); submit is not a tool call
    assert rows["full_evaluation_rejected_regression"][0]["model_calls"] == 3 and rows["noop_unchanged_design"][0]["model_calls"] == 2


def test_class2_duplicate_submit_costs_a_model_call_but_no_iteration_and_no_evaluation(tmp_path):
    h = Harness(tmp_path)
    ctl = h.new([A("get_design"), A("submit", summary="a"), A("submit", summary="b"), A("get_design")], Limits(max_iterations=5, no_progress_limit=5, max_model_calls=4))
    ctl.run()
    s = h.state()
    assert (s["iteration"], s["evaluations"], len(h.calls), s["model_calls"], s["tool_calls"], s["no_progress"]) == (1, 1, 1, 4, 2, 1)


def test_class2_blocked_evaluation_still_consumes_an_iteration_and_an_evaluation_and_stops_the_run(tmp_path):
    def broken(components_path, out_dir):
        raise RuntimeError("docker exited 125")
    h = Harness(tmp_path)
    ctl = h.new([A("get_design"), A("submit", summary="s")], Limits(max_iterations=3))
    ctl.evaluator.run_pipeline = broken
    s = ctl.run()
    d = json.loads((h.run / "evaluations/decision_01.json").read_text())
    assert s["stop_reason"] == "evaluation_blocked" and d["verdict"] == "blocked" and d["reason"].startswith("pipeline_error")
    assert (s["iterations"], s["evaluations"]) == (1, 1)


# =================================================================== TASK 4: crash during a full evaluation
def test_process_death_mid_evaluation_leaves_iteration_charged_no_evaluation_recorded_and_partial_files(tmp_path):
    def dies_midway(components_path, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "ngspice_partial.raw").write_text("half written")
        raise Crash("SIGKILL")
    h = Harness(tmp_path, pipeline=dies_midway)
    ctl = h.new([A("get_design"), A("submit", summary="s")], Limits(max_iterations=2))
    with pytest.raises(Crash):
        ctl.run()
    s = h.state()
    assert s["state"] == "EVALUATING"                                                                    # last durable state
    assert (s["iteration"], s["evaluations"], s["model_calls"], s["tool_calls"]) == (1, 0, 2, 1)       # iteration + model calls are charged; no evaluation recorded
    assert s["evaluated_manifest_sha256"] == [] and s["usage"]["eval_seconds"] == 0
    assert not list((h.run / "evaluations").glob("decision_*.json")) and (h.run / "evaluations/iter_01/ngspice_partial.raw").exists()
    assert not (h.run / "summary.json").exists()                                                         # no summary: an unfinished run is distinguishable


def test_resume_after_mid_evaluation_death_records_the_interruption_and_reevaluates_in_a_fresh_directory(tmp_path):
    state = {"die": True}

    def maybe_die(components_path, out_dir):
        if state["die"]:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "ngspice_partial.raw").write_text("half written")
            raise Crash("SIGKILL")
        return synth_pipeline(components_path, out_dir)
    h = Harness(tmp_path, pipeline=maybe_die)
    ctl = h.new([A("get_design"), A("submit", summary="s")], Limits(max_iterations=3))
    with pytest.raises(Crash):
        ctl.run()
    state["die"] = False
    r = h.resume([A("submit", summary="resubmit after crash")], Limits(max_iterations=3))
    r.continue_run()
    s = h.state()
    ev = h.events()
    assert any(e["type"] == "evaluation_interrupted" and e["iteration"] == 1 for e in ev)               # the lost evaluation is on the record
    assert s["interrupted_evaluations"] == 1
    # ACTUAL accounting: the crashed attempt keeps its iteration (conservative), the re-run is iteration 2 in a fresh directory; the half-written directory is never reused
    assert (s["iteration"], s["evaluations"]) == (2, 1)
    assert (h.run / "evaluations/iter_02/record.json").exists() and not (h.run / "evaluations/iter_02/ngspice_partial.raw").exists()
    assert (h.run / "evaluations/iter_01/ngspice_partial.raw").exists()


def test_killed_pipeline_subprocess_is_blocked_not_judged_and_the_design_may_be_resubmitted_after_resume(tmp_path):
    """A REAL subprocess that SIGKILLs itself, run through subprocess.run(check=True) like the real pipeline's tool calls."""
    state = {"kill": True}

    def pipeline(components_path, out_dir):
        if state["kill"]:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "partial.txt").write_text("x")
            subprocess.run([sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"], check=True)
        return synth_pipeline(components_path, out_dir)
    h = Harness(tmp_path, pipeline=pipeline)
    ctl = h.new([A("get_design"), A("submit", summary="s")], Limits(max_iterations=3))
    s = ctl.run()
    d = json.loads((h.run / "evaluations/decision_01.json").read_text())
    assert s["stop_reason"] == "evaluation_blocked" and d["verdict"] == "blocked" and "CalledProcessError" in d["reason"] and "SIGKILL" in d["reason"]
    assert (s["iterations"], s["evaluations"]) == (1, 1)                                                  # a blocked evaluation is charged
    state["kill"] = False
    r = h.resume([A("submit", summary="retry")], Limits(max_iterations=3))
    r.continue_run()
    after = h.state()
    assert not any(e["type"] == "submit_rejected_duplicate" for e in h.events())                           # a never-judged design must not be reported as "already evaluated and rejected"
    assert (after["iteration"], after["evaluations"]) == (2, 2) and len(h.calls) == 2


# =================================================================== real recorded decisions -> the exact feedback payload for improve-stage2-alias
REAL_RUNS = REPO_ROOT / "runs/agent/improve-stage2-alias-fixture"


@pytest.mark.skipif(not REAL_RUNS.exists(), reason="recorded improvement-search artifacts not present")
@pytest.mark.parametrize("label", ["A_r45_16k9", "B_r45_17k4", "C_c3_6n2_r45_15k4", "D_c3_6n2", "X_regression_c3_5n1", "X_noop_c3_5n60"])
def test_feedback_payload_for_real_recorded_decisions_fits_and_has_the_documented_shape(tmp_path, label):
    """The controller is fed the REAL decision recorded from the real-EDA run (stub evaluator returns it); we inspect what the agent would be sent."""
    decision = json.loads((REAL_RUNS / label / "evaluations/decision_01.json").read_text())
    h = Harness(tmp_path)
    seen = {}

    def capture(transcript):
        seen["t"] = transcript
        return json.dumps(A("get_design"))
    ctl = h.new([A("get_design"), A("submit", summary="s"), capture], Limits(max_iterations=2))
    ctl.evaluator.evaluate_improvement = lambda *a, **k: decision
    ctl.run()
    fb = last_tool_result(seen["t"])
    raw = seen["t"].rsplit("TOOL RESULT:\n", 1)[1].split("\n\n[", 1)[0]
    assert len(raw) < 6000, f"{label}: payload {len(raw)} chars would be truncated into invalid JSON"
    assert set(fb) == {"ok", "evaluator_verdict", "findings", "objective", "circuit_requirement_failures", "unresolved_baseline_failures_not_yours", "note"}
    assert set(fb["objective"]) == {"check", "unit", "direction", "baseline", "candidate", "improvement", "min_improvement", "met", "jitter_tolerance_dB"}
    print(f"\n[{label}] payload {len(raw)} chars; findings rules: {[f['rule'] for f in fb['findings']]}")

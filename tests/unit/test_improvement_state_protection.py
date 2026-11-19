"""State-based protection (improvement mode): reverts are legitimate, loops are bounded by STATE, explicit no-improvement declaration.
Stub pipelines; no model, no docker."""
from __future__ import annotations

import json
import re

from ae.harness.controller import Limits
from ae.harness.patch import sha256_text

from tests.unit.test_improvement import A, PRISTINE
from tests.unit.test_improvement_feedback_loop import NOOP, NOOP2, Rig


def edit(part, field, to):
    def f(t):
        sha = re.findall(r'"(?:manifest_sha256|new_base_sha256)": "([0-9a-f]{64})"', t)[-1]
        return json.dumps(A("propose_patch", patch={"summary": "x", "base_sha256": sha, "ops": [{"op": "set_field", "part": part, "field": field, "to": to}]}))
    return f


def test_reverting_to_the_baseline_state_is_legitimate_and_a_revert_submission_is_refused_without_evaluation(tmp_path):
    rig = Rig(tmp_path)
    # edit C1 value then REVERT it: the MPN was never touched, so the text is identical; also revert after an MPN edit (status stays unverified_candidate = byte-different manifest)
    ctl = rig.controller([A("get_design"), edit("C2", "mpn_candidate", "C0805C102J5GACTA"), edit("C2", "mpn_candidate", "C0805C102J5GACTU"), A("submit", summary="reverted"), A("get_design")],
                         Limits(max_iterations=3, max_model_calls=6))
    ctl.run()
    s = ctl.state
    assert sha256_text(ctl.ws.text()) != sha256_text(PRISTINE.read_text())                 # byte-different (status bookkeeping) ...
    assert rig.calls == [] and s["refused_submits"] == 1 and s["evaluations"] == 0 and s["iteration"] == 0      # ... but refused: no evaluation spent
    assert s["state_revisits"] == 0                                                          # reset by the submission attempt


def test_a_genuinely_different_part_number_is_evaluated_not_discarded_as_a_duplicate(tmp_path):
    rig = Rig(tmp_path)
    ctl = rig.controller([A("get_design"), edit("C2", "mpn_candidate", "C0805C102J5GACTA"), A("submit", summary="alt MPN"), A("get_design")], Limits(max_iterations=3, max_model_calls=4))
    ctl.run()
    assert len(rig.calls) == 1 and ctl.state["evaluations"] == 1 and ctl.state["refused_submits"] == 0


def test_resubmitting_an_electrically_identical_evaluated_state_is_refused(tmp_path):
    rig = Rig(tmp_path)
    A_, B_ = "C0805C102J5GACTA", "C0805C102J5GACTB"
    ctl = rig.controller([A("get_design"), edit("C2", "mpn_candidate", A_), A("submit", summary="state A"),
                          A("get_design"), edit("C2", "mpn_candidate", B_), edit("C2", "mpn_candidate", A_), A("submit", summary="back to state A by another route"), A("get_design")],
                         Limits(max_iterations=3, max_model_calls=12, no_progress_limit=5))
    ctl.run()
    s = ctl.state
    assert len(rig.calls) == 1 and s["evaluations"] == 1 and s["refused_submits"] == 1                  # state A was evaluated once; the revisit was refused, not re-evaluated
    refused = [json.loads(l) for l in (tmp_path / "run/trace.jsonl").read_text().splitlines() if '"submit_refused"' in l]
    assert refused[0]["code"] == "already_evaluated"


def test_exploration_cycling_without_submission_is_bounded(tmp_path):
    rig = Rig(tmp_path)
    script = [A("get_design")] + [edit("C2", "mpn_candidate", "C0805C102J5GACTA"), edit("C2", "mpn_candidate", "C0805C102J5GACTU")] * 8
    ctl = rig.controller(script, Limits(max_iterations=3, max_model_calls=30, max_state_revisits=3))
    s = ctl.run()
    assert s["stop_reason"] == "exploration_cycling" and rig.calls == [] and ctl.state["state_revisits"] == 3


def test_a_submission_attempt_resets_the_revisit_streak(tmp_path):
    rig = Rig(tmp_path)
    script = [A("get_design"), edit("C2", "mpn_candidate", "C0805C102J5GACTA"), edit("C2", "mpn_candidate", "C0805C102J5GACTU"), edit("C2", "mpn_candidate", "C0805C102J5GACTA"),
              A("submit", summary="x"), A("get_design")]
    ctl = rig.controller(script, Limits(max_iterations=3, max_model_calls=8, max_state_revisits=3))
    ctl.run()
    assert ctl.state["stop_reason"] != "exploration_cycling"


def test_declare_no_improvement_ends_the_run_without_an_evaluation_and_has_its_own_stop_reason(tmp_path):
    rig = Rig(tmp_path)
    ctl = rig.controller([A("get_design"), A("declare_no_improvement", summary="nominal checks show every retune breaks a worst-case limit")], Limits(max_iterations=3, max_model_calls=5))
    s = ctl.run()
    assert s["stop_reason"] == "no_improvement_declared" and rig.calls == [] and s["evaluations"] == 0 and s["iterations"] == 0
    assert s["live_agent_success"] is False and s["accepted_by_independent_evaluator"] is False
    ev = [json.loads(l) for l in (tmp_path / "run/trace.jsonl").read_text().splitlines()]
    assert any(e["type"] == "no_improvement_declared" and "worst-case" in e["summary"] for e in ev)


def test_declare_no_improvement_is_not_a_tool_in_restoration_mode():
    from ae.harness.tasks import load_task
    assert load_task("dev-c1-value").kind == "restoration"
    import ae.harness.tools as tools
    from pathlib import Path
    from ae.harness.workspace import Workspace
    ctx = tools.AgentContext(Workspace(Path("/tmp/claude-1000/_ws_unused")), Path("/tmp/claude-1000/_scratch_unused"), failure_report={})
    assert "declare_no_improvement" not in {sp.name for sp in tools.build_registry(ctx).specs()}
    ctx.improvement = True
    assert "declare_no_improvement" in {sp.name for sp in tools.build_registry(ctx).specs()}


def test_resume_after_cycling_stop_is_not_instantly_re_stopped_by_a_novel_design(tmp_path):
    from tests.unit.test_improvement_feedback_loop import Rig as _Rig
    from ae.harness.controller import Controller
    rig = _Rig(tmp_path)
    script = [A("get_design")] + [edit("C2", "mpn_candidate", "C0805C102J5GACTA"), edit("C2", "mpn_candidate", "C0805C102J5GACTU")] * 4
    ctl = rig.controller(script, Limits(max_iterations=3, max_model_calls=30, max_state_revisits=3))
    assert ctl.run()["stop_reason"] == "exploration_cycling"
    kw = dict(task=ctl.task, provider=__import__("ae.harness.providers", fromlist=["x"]).ScriptedProvider([A("get_design"), edit("C1", "value", "20n"), A("get_design")]), evaluator=ctl.evaluator,
              limits=Limits(max_iterations=3, max_model_calls=30, max_state_revisits=3), mode=ctl.mode, baseline_record=rig.base, baseline_dir=rig.base_dir, checks_fn=ctl.checks_fn)
    r = Controller.resume(tmp_path / "run", **kw)
    r.continue_run()
    assert r.state["stop_reason"] != "exploration_cycling"                                # a novel state after the resume is not a revisit

"""Plumbing tests for the frozen task improve-wide-retune-alias (policy v2). NO model, NO docker; stub pipelines. No solving candidate is written or evaluated here."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.improvement import load_policy
from ae.harness.patch import sha256_text, validate_patch
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import list_tasks, load_task
from ae.harness.workspace import REPO_ROOT, trusted_digest

from tests.unit.test_improvement import A, PRISTINE, patch_ops, synth_pipeline

NOOP = patch_ops([("C2", "mpn_candidate", "C0805C102J5GACTA")])   # evaluated no-op; an UNCHANGED design is refused before evaluation (feedback-loop fix)

TASKS = REPO_ROOT / "src/ae/harness/tasks"
V1, V2 = TASKS / "improve_stage2_alias.yaml", TASKS / "improve_wide_retune_alias.yaml"
TASK_ID = "improve-wide-retune-alias"


def test_unchanged_loader_accepts_the_v2_file_as_is_and_v1_is_untouched():
    p2 = load_policy(V2)
    assert (p2.task_id, p2.version, p2.min_improvement, p2.max_ops) == (TASK_ID, 2, 0.5, 16) and p2.allowed_parts == ("R1", "R2", "R3", "C1", "C2", "R4", "R5", "C3")
    assert hashlib.sha256(V1.read_bytes()).hexdigest() == "d278109a50e8a9f161a41f07bbdd9d5157f0d757ab5f37aea62e7599f88aabdc"          # v1 never edited in place
    assert load_policy(V1).version == 1
    assert hashlib.sha256((REPO_ROOT / "requirements/fe-aaf-001.yaml").read_bytes()).hexdigest().startswith("4767130a")             # contract untouched
    assert {t["id"] for t in list_tasks()} >= {"improve-stage2-alias", TASK_ID} and load_task(TASK_ID).kind == "improvement"


def test_rules_are_carried_over_from_v1_verbatim_including_req_stab():
    y1, y2 = yaml.safe_load(V1.read_text()), yaml.safe_load(V2.read_text())
    for key in ("hard_constraints", "traded_metrics", "nonregression", "claims_never_made", "verdicts", "policy_violation_vs_circuit_failure"):
        assert y1[key] == y2[key], key
    assert y2["nonregression"]["failed_in_baseline"]["REQ-STAB"] == y1["nonregression"]["failed_in_baseline"]["REQ-STAB"]
    assert (y1["jitter"]["rel"], y1["jitter"]["abs"]) == (y2["jitter"]["rel"], y2["jitter"]["abs"]) and y1["baseline"]["components_sha256"] == y2["baseline"]["components_sha256"]
    changed = {k for k in y1 if k in y2 and y1[k] != y2[k]}
    assert changed == {"id", "policy_version", "description", "baseline", "allowed_edits", "objective", "jitter"}, changed             # exactly the flagged V2-CHANGEs (+ baseline/jitter prose)
    assert y1["objective"]["baseline_value_expected_dB"] == y2["objective"]["baseline_value_expected_dB"] and y2["objective"]["direction"] == "minimize"


def test_no_scripted_search_or_candidate_values_exist_for_this_task():
    y = yaml.safe_load(V2.read_text())
    assert "search" not in y and "candidates" not in y
    assert set(y["plumbing_controls"]) == {"X2_regression_stage2_pole_up", "X2_noop_c1_text", "X2_out_of_scope_r7"}


def test_v2_scope_accepts_all_eight_parts_and_nothing_else():
    scope = load_policy(V2).scope()
    sha = sha256_text(PRISTINE.read_text())
    for part in ("R1", "R2", "R3", "C1", "C2", "R4", "R5", "C3"):
        validate_patch({"summary": "s", "base_sha256": sha, "ops": [{"op": "set_field", "part": part, "field": "value", "to": "10k" if part[0] == "R" else "10n"}]}, PRISTINE.read_text(), scope)
    for part in ("R7", "R8", "R6", "C4", "C5", "U1"):
        with pytest.raises(Exception) as e:
            validate_patch({"summary": "s", "base_sha256": sha, "ops": [{"op": "set_field", "part": part, "field": "value", "to": "1k"}]}, PRISTINE.read_text(), scope)
        assert e.value.code == "out_of_scope"


# ---- governance: the enforcement truth stated in the policy
def make(tmp_path, script, limits):
    base_dir = tmp_path / "base"
    base = synth_pipeline(PRISTINE, base_dir)
    ev = TrustedEvaluator()
    ev.run_pipeline = synth_pipeline
    provider = ScriptedProvider(script)
    ctl = Controller(tmp_path / "run", load_task(TASK_ID), provider, ev, limits, Mode.ENGINEERED_SINGLE, baseline_record=base, baseline_dir=base_dir,
                     checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names})
    return ctl, provider


def test_model_call_cap_is_a_hard_stop_before_the_call(tmp_path):
    ctl, prov = make(tmp_path, [A("get_design")] * 6, Limits(max_model_calls=3, max_iterations=3))
    s = ctl.run()
    assert s["stop_reason"] == "max_model_calls" and prov.i == 3 and s["model_calls"] == 3                # the 4th call was never requested


def test_tool_call_cap_is_a_hard_stop_before_dispatch(tmp_path):
    ctl, prov = make(tmp_path, [A("get_design")] * 6, Limits(max_tool_calls=2, max_iterations=3))
    s = ctl.run()
    assert s["stop_reason"] == "max_tool_calls" and s["tool_calls"] == 2 and prov.i == 2


def test_iteration_cap_stops_after_the_evaluated_rejection_and_allows_no_further_evaluation(tmp_path):
    ctl, prov = make(tmp_path, [A("get_design"), NOOP, A("submit", summary="s"), A("get_design"), NOOP, A("submit", summary="s")], Limits(max_iterations=1))
    s = ctl.run()
    assert s["stop_reason"] == "max_iterations" and s["evaluations"] == 1 and prov.i == 3


def test_invalid_reply_cap_is_consecutive_with_a_cumulative_backstop(tmp_path):
    ctl, prov = make(tmp_path, ["junk", "junk", "junk", A("get_design")], Limits(max_iterations=3))
    s = ctl.run()
    assert s["stop_reason"] == "invalid_replies" and prov.i == 3                                      # 3 consecutive
    sub = tmp_path / "total"
    sub.mkdir()
    ctl, prov = make(sub, ["junk", A("get_design")] * 12 + ["junk"], Limits(max_iterations=3, max_model_calls=40, max_tool_calls=40, max_invalid_replies_total=12))
    s = ctl.run()
    assert s["stop_reason"] == "invalid_replies" and ctl.state["invalid_replies"] == 12 and s["tool_calls"] == 11       # scattered replies hit the cumulative backstop at 12


def test_noop_control_text_edit_is_refused_before_evaluation_under_the_state_based_gate(tmp_path):
    def patch(t):
        import re
        sha = re.findall(r'"manifest_sha256": "([0-9a-f]{64})"', t)[-1]
        return json.dumps(A("propose_patch", patch={"summary": "noop", "base_sha256": sha, "ops": [{"op": "set_field", "part": "C1", "field": "value", "to": "18.0n"}]}))
    ctl, _ = make(tmp_path, [A("get_design"), patch, A("submit", summary="s"), A("get_design")], Limits(max_iterations=1, max_model_calls=4))
    ctl.run()
    # (the original frozen control label says `rejected`: that was true before the state-based gate; see docs section 10)
    assert ctl.state["evaluations"] == 0 and ctl.state["refused_submits"] == 1 and not (tmp_path / "run/evaluations").exists()

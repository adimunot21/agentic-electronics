"""Plumbing tests for the frozen task improve-wide-retune-alias-v3 (policy v3). NO model, NO docker; stub pipelines."""
from __future__ import annotations

import hashlib
import json

import yaml

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.improvement import load_policy
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task
from ae.harness.workspace import REPO_ROOT

from tests.unit.test_improvement import A, PRISTINE, synth_pipeline

TASKS = REPO_ROOT / "src/ae/harness/tasks"
V1, V2, V3 = TASKS / "improve_stage2_alias.yaml", TASKS / "improve_wide_retune_alias.yaml", TASKS / "improve_wide_retune_alias_v3.yaml"
TASK_ID = "improve-wide-retune-alias-v3"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v3_loads_with_unchanged_loader():
    p2, p3 = load_policy(V2), load_policy(V3)
    assert (p3.task_id, p3.version, p3.min_improvement) == (TASK_ID, 3, 0.5)
    assert p3.allowed_parts == p2.allowed_parts and p3.max_ops == p2.max_ops
    assert load_task(TASK_ID).kind == "improvement"


def test_v2_and_v1_files_are_byte_identical_to_frozen_hashes():
    assert _sha(V2) == "7a02c5479daeb2a36cb8496c3b5a2cff8dfa7e648e708ebfa272a62386d9d660"
    assert _sha(V1) == "d278109a50e8a9f161a41f07bbdd9d5157f0d757ab5f37aea62e7599f88aabdc"


def test_v3_differs_from_v2_only_by_scoring_statement():
    y2, y3 = yaml.safe_load(V2.read_text()), yaml.safe_load(V3.read_text())
    assert set(y2) == set(y3)
    for key in y2:
        if key not in ("id", "policy_version", "objective"):
            assert y2[key] == y3[key], key
    obj = dict(y3["objective"])
    statement = obj.pop("scoring_statement")
    assert obj == y2["objective"]
    for phrase in ("WORST CASE", "complete tolerance-corner set", "measured value"):
        assert phrase in statement
    assert "search" not in y3 and "candidates" not in y3


def test_statement_reaches_brief_and_first_message(tmp_path):
    base_dir = tmp_path / "base"
    base = synth_pipeline(PRISTINE, base_dir)
    ev = TrustedEvaluator()
    ev.run_pipeline = synth_pipeline
    ctl = Controller(tmp_path / "run", load_task(TASK_ID), ScriptedProvider([A("get_design")]), ev, Limits(max_iterations=1), Mode.ENGINEERED_SINGLE,
                     baseline_record=base, baseline_dir=base_dir, checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names})
    ctl.prepare()
    brief = json.loads((tmp_path / "run" / "task_brief.json").read_text())
    assert "WORST CASE" in brief["objective"]["scoring_statement"]
    first = ctl.messages[0]["content"]
    assert "WORST CASE" in first, "statement truncated out of the first message"

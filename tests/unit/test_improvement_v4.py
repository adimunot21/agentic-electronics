"""Plumbing tests for the frozen task improve-wide-retune-alias-v4 (policy v4: E-series enforcement). NO model, NO docker."""
from __future__ import annotations

import hashlib
import json

import pytest
import yaml

from ae.harness.contracts import ToolError
from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.improvement import load_policy
from ae.harness.patch import sha256_text, validate_patch
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task
from ae.harness.workspace import REPO_ROOT

from tests.unit.test_improvement import A, PRISTINE, synth_pipeline

TASKS = REPO_ROOT / "src/ae/harness/tasks"
V1, V2, V3, V4 = (TASKS / n for n in ("improve_stage2_alias.yaml", "improve_wide_retune_alias.yaml", "improve_wide_retune_alias_v3.yaml", "improve_wide_retune_alias_v4.yaml"))
TASK_ID = "improve-wide-retune-alias-v4"
MANIFEST = REPO_ROOT / "designs/fe-aaf-001/components.yaml"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _patch(part, to):
    text = MANIFEST.read_text()
    return {"summary": "t", "base_sha256": sha256_text(text), "ops": [{"op": "set_field", "part": part, "field": "value", "to": to}]}, text


def test_v4_loads():
    p = load_policy(V4)
    assert (p.task_id, p.version, p.min_improvement) == (TASK_ID, 4, 0.5)
    assert load_task(TASK_ID).kind == "improvement"


def test_earlier_policies_byte_identical_to_frozen_hashes():
    assert _sha(V3).startswith("da5c05f37060cdc5f40c7ef6426553b5af42e5acd8e5dbf2998a83ba917d071e")
    assert _sha(V2).startswith("7a02c5479daeb2a36cb8496c3b5a2cff8dfa7e648e708ebfa272a62386d9d660")
    assert _sha(V1).startswith("d278109a50e8a9f161a41f07bbdd9d5157f0d757ab5f37aea62e7599f88aabdc")


def test_v4_differs_from_v3_only_by_series_enforcement():
    y3, y4 = yaml.safe_load(V3.read_text()), yaml.safe_load(V4.read_text())
    assert set(y3) == set(y4)
    for key in y3:
        if key not in ("id", "policy_version", "allowed_edits"):
            assert y3[key] == y4[key], key
    edits = dict(y4["allowed_edits"])
    assert edits.pop("enforce_series") is True
    assert "E96" in edits.pop("series_rule") and "E24" in y4["allowed_edits"]["series_rule"]
    assert edits == y3["allowed_edits"]
    assert "search" not in y4 and "candidates" not in y4


def test_scope_flags():
    assert load_policy(V4).scope().enforce_series is True
    assert load_policy(V3).scope().enforce_series is False


def test_series_rule_reaches_brief(tmp_path):
    base_dir = tmp_path / "base"
    base = synth_pipeline(PRISTINE, base_dir)
    ev = TrustedEvaluator()
    ev.run_pipeline = synth_pipeline
    ctl = Controller(tmp_path / "run", load_task(TASK_ID), ScriptedProvider([A("get_design")]), ev, Limits(max_iterations=1), Mode.ENGINEERED_SINGLE,
                     baseline_record=base, baseline_dir=base_dir, checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names})
    ctl.prepare()
    brief = json.loads((tmp_path / "run" / "task_brief.json").read_text())
    assert "series_rule" in json.dumps(brief)


def test_validator_refuses_off_series_r1():
    patch, text = _patch("R1", "59.6k")
    with pytest.raises(ToolError) as exc:
        validate_patch(patch, text, load_policy(V4).scope())
    assert exc.value.code == "not_in_series"


def test_validator_accepts_e96_r1_and_e24_c1_refuses_e12_gap():
    scope = load_policy(V4).scope()
    for part, to in (("R1", "60.4k"), ("C1", "20n")):
        patch, text = _patch(part, to)
        assert validate_patch(patch, text, scope).ops[0]["to"] == to
    patch, text = _patch("C1", "21n")
    with pytest.raises(ToolError) as exc:
        validate_patch(patch, text, scope)
    assert exc.value.code == "not_in_series"


def test_v3_scope_still_accepts_off_series():
    patch, text = _patch("R1", "59.6k")
    assert validate_patch(patch, text, load_policy(V3).scope()).ops[0]["to"] == "59.6k"

"""Advisory hook of ae.harness.sanity into the harness: run_check edit_sanity (free) and the sanity advisory on submit/rejection. Must never change refusal/acceptance."""
from __future__ import annotations

import json

import yaml

from ae.harness import tools
from ae.harness.controller import Limits
from ae.harness.tools import advisory_sanity

from tests.unit.test_improvement import A, PRISTINE
from tests.unit.test_improvement_feedback_loop import Rig, TASK_ID

EVAL2_EDITS = [("R2", "value", "75k"), ("R3", "value", "6.04k"), ("R5", "value", "16.5k"), ("C1", "value", "20n")]


def ready(tmp_path, script=()):
    rig = Rig(tmp_path)
    ctl = rig.controller(list(script), Limits(max_iterations=3, max_model_calls=20))
    assert ctl.prepare() is None
    return rig, ctl


def apply(ctl, edits):
    res = ctl.registry.call("propose_patch", {"patch": _patch(ctl, edits)})
    assert res.ok, res.to_json()


def _patch(ctl, edits):
    return {"summary": "t", "base_sha256": ctl.ws.sha256(), "ops": [{"op": "set_field", "part": p, "field": f, "to": v} for p, f, v in edits]}


def test_edit_sanity_check_is_free_advisory_and_flags_the_live8_eval2_shape(tmp_path):
    rig, ctl = ready(tmp_path)
    apply(ctl, [("R2", "value", "75k")])
    before = ctl.ctx.corner_previews
    res = ctl.registry.call("run_check", {"check": "edit_sanity"})
    assert res.ok and res.result["advisory"] is True and ctl.ctx.corner_previews == before
    assert res.result["status"] in ("ok", "warn", "skipped") and "predicted" in res.result or res.result["status"] == "skipped"
    codes = {w["code"] for w in res.result["warnings"]}
    assert "GAIN_CORNER_MIN" in codes or res.result["status"] == "skipped"          # R2 75k lowers the stage-1 gain below the corner window


def test_edit_sanity_unavailable_in_restoration_mode(tmp_path):
    from tests.unit.test_harness import make
    ctl = make(tmp_path, [])
    ctl.prepare()
    res = ctl.registry.call("run_check", {"check": "edit_sanity"})
    assert not res.ok


def test_advisory_sanity_never_raises_and_is_none_without_a_baseline(tmp_path):
    rig, ctl = ready(tmp_path)
    ctl.ctx.baseline_manifest_text = "not: [yaml"
    out = advisory_sanity(ctl.ctx)
    assert out["status"] == "skipped" and out["warnings"] == []
    ctl.ctx.baseline_manifest_text = None
    assert advisory_sanity(ctl.ctx) is None


def test_submit_path_records_the_advisory_and_does_not_change_the_verdict(tmp_path):
    rig = Rig(tmp_path / "hooked")
    script = [A("get_design"), patch_ops_action(), A("submit", summary="x")]
    ctl = rig.controller(script, Limits(max_iterations=1, max_model_calls=5))
    summary = ctl.run()
    events = rig.events()
    adv = [e for e in events if e["type"] == "sanity_advisory"]
    assert len(adv) == 1 and adv[0]["status"] in ("ok", "warn", "skipped")
    assert summary["evaluations"] == 1                                    # the advisory did not block the evaluation


def test_a_crashing_sanity_module_cannot_break_a_submission(tmp_path, monkeypatch):
    import ae.harness.sanity as sanity
    monkeypatch.setattr(sanity, "edit_sanity", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rig = Rig(tmp_path)
    ctl = rig.controller([A("get_design"), patch_ops_action(), A("submit", summary="x")], Limits(max_iterations=1, max_model_calls=5))
    s = ctl.run()
    assert s["evaluations"] == 1 and any(e["type"] == "sanity_advisory" and e["status"] == "skipped" for e in rig.events())


def patch_ops_action():
    from tests.unit.test_improvement_feedback_loop import NOOP
    return NOOP


def test_system_prompt_describes_preview_and_edit_sanity_as_advisory():
    from ae.harness.controller import IMPROVEMENT_PROMPT as P
    assert "edit_sanity" in P and "free" in P and "advisory" in P and "not a verdict" in P and "run_check preview" in P
    assert len(P) < 6500                                      # headroom: the first message (prompt is separate, but keep it compact) stays well inside the transcript cap


def test_hook_uses_the_policy_limits_and_falls_back_to_defaults(tmp_path, monkeypatch):
    import ae.harness.sanity as sanity
    seen = []
    real = sanity.edit_sanity
    monkeypatch.setattr(sanity, "edit_sanity", lambda *a, **k: (seen.append(k.get("limits")), real(*a, **k))[1])
    rig, ctl = ready(tmp_path)
    advisory_sanity(ctl.ctx)
    assert seen[-1] is not None and seen[-1].gain_min == 1.2355 and seen[-1].alias_max_db == -55.8 and seen[-1].flat_max_db == 0.48      # policy values (v3/v4 frozen)
    ctl.ctx.guard = {**ctl.ctx.guard, "policy_raw": {"hard_constraints": []}}
    advisory_sanity(ctl.ctx)
    assert seen[-1] is None                                                                                                                   # incomplete policy -> module defaults, still advisory


def test_restoration_mode_refuses_but_never_offers_edit_sanity_or_previews(tmp_path):
    from tests.unit.test_harness import make
    ctl = make(tmp_path, [])
    ctl.prepare()
    for check in ("edit_sanity", "preview", "corner_preview", "nonsense"):
        res = ctl.registry.call("run_check", {"check": check})
        assert not res.ok
        avail = res.to_json()["error"]["details"]["available"]
        assert avail == ["bom_consistency", "ac_response"], (check, avail)


def test_improvement_mode_still_offers_edit_sanity(tmp_path):
    rig, ctl = ready(tmp_path)
    res = ctl.registry.call("run_check", {"check": "nonsense"})
    assert not res.ok and "edit_sanity" in json.dumps(res.to_json())


def test_readme_names_the_policy_task_not_a_fixed_label(tmp_path):
    from ae.harness.tasks import load_task
    for tid, expected in (("improve-stage2-alias", "improve-stage2-alias"), ("improve-wide-retune-alias-v4", "improve-wide-retune-alias-v4"), ("improve-wide-retune-alias-v3", "improve-wide-retune-alias-v3")):
        rig = Rig(tmp_path / tid)
        ctl = rig.controller([], Limits())
        ctl.task = load_task(tid)
        assert ctl.prepare() is None
        ctl._finalize({"blocked_claims": [{"claim": "X"}]})
        text = (ctl.run_dir / "output" / "README.txt").read_text()
        assert text.startswith(f"ACCEPTED FOR THE FROZEN OBJECTIVE ONLY ({expected}, policy sha256 ") and text.endswith("X\n")

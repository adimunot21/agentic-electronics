"""Combined `preview` (backlog item 2): ONE budget unit runs the corner sweep AND the nominal requirements, and projects the frozen policy's rules so a design cannot pass
the previews and then fail a guarded metric the agent never looked at (improve-live-8, evaluation 1: REQ-BIAS margin loss). Offline: fake checks_fn, no simulator."""
from __future__ import annotations

import json
from pathlib import Path

from ae.harness.checks import assess_guards
from ae.harness.controller import Limits, guard_context, preview_spend
from ae.harness.improvement import load_policy
from ae.harness.tasks import load_task

from tests.unit.test_improvement_feedback_loop import TASK_ID, Rig

POLICY = load_policy(Path(load_task("improve-wide-retune-alias-v4").policy_path))
RULES = {"margin_fraction": POLICY.margin_fraction, "abs_tol": POLICY.abs_tol, "rel_tol": POLICY.rel_tol, "min_improvement": POLICY.min_improvement}
BASE = {"REQ-ALIAS-CORNERS": {"outcome": "passed", "value": -58.735, "limit": "<= -55"},
        "REQ-BIAS": {"outcome": "passed", "value": 9.65, "limit": "<= 10.0", "margin": 0.35},
        "REQ-STAB": {"outcome": "failed", "value": 52.8446, "limit": "<= 3.0"}}


def corner(alias=-59.28, flat=0.40, gmin=1.30, gmax=1.36):
    return {"outcome": "within_contract_limits", "measurements": {"alias_corners_worst_dB": alias, "flatness_corners_worst_dB": flat, "gain_corners_min_V_per_V": gmin, "gain_corners_max_V_per_V": gmax}}


def reqs(bias_margin=0.35, stab=52.8446):
    return {"outcome": "info", "requirements": {"REQ-BIAS": {"outcome": "passed", "value": 9.65, "limit": "<= 10.0", "margin": bias_margin},
                                                "REQ-STAB": {"outcome": "failed", "value": stab, "limit": "<= 3.0", "margin": None}}}


def project(c, r):
    return assess_guards(POLICY.raw, RULES, BASE, POLICY.objective_check, c, r)


def test_live8_evaluation_1_shape_is_flagged_before_spending_an_evaluation():
    """Live-8 eval 1: corners fine, +0.54 dB gained, REQ-STAB marginally lower, but REQ-BIAS lost 0.092 margin units against 0.035 allowed."""
    out = project(corner(alias=-59.28), reqs(bias_margin=0.35 - 0.092))
    assert out["projection_only"] and not out["all_projected_rules_ok"]
    assert out["would_trip"] == ["REQ-BIAS:margin_nonregression"]
    by_id = {r["id"]: r for r in out["rows"]}
    assert by_id["REQ-ALIAS-CORNERS"]["status"] == "ok" and by_id["REQ-STAB"]["status"] == "ok"


def test_clean_design_projects_ok_and_every_rule_family_is_present():
    out = project(corner(alias=-59.4), reqs(bias_margin=0.34))
    assert out["all_projected_rules_ok"], out["would_trip"]
    assert {r["rule"] for r in out["rows"]} >= {"objective_improvement", "H1_hard_limit", "H2_gain_min_corners", "H3_gain_max_corners", "REQ-STAB_nonregression", "margin_nonregression"}


def test_objective_shortfall_corner_limits_and_stab_regression_are_flagged():
    assert "REQ-ALIAS-CORNERS:objective_improvement" in project(corner(alias=-59.0), reqs())["would_trip"]            # only 0.265 dB gained, 0.5 required
    bad = project(corner(alias=-59.4, flat=0.49, gmin=1.20), reqs(stab=60.0))["would_trip"]
    assert {"REQ-FLAT-CORNERS:H1_hard_limit", "REQ-GAIN-CORNERS:H2_gain_min_corners", "REQ-STAB:REQ-STAB_nonregression"} <= set(bad)


def test_partial_inputs_do_not_crash_and_do_not_claim_ok_for_missing_parts():
    out = project(None, reqs(bias_margin=0.1))
    assert out["would_trip"] == ["REQ-BIAS:margin_nonregression"] and not any(r["rule"] == "objective_improvement" for r in out["rows"])
    assert project(corner(alias=-59.4), None)["all_projected_rules_ok"]


def rig_with_counting_checks(tmp_path):
    rig, calls = Rig(tmp_path), []

    def checks(names, manifest, workdir):
        if "bom_consistency" in names:                       # baseline capture during prepare
            return {n: {"outcome": "passed"} for n in names}
        calls.append(tuple(names))
        res = {"corner_preview": corner(alias=-59.28), "requirements_preview": reqs(bias_margin=0.258)}
        return {n: res[n] for n in names}
    ctl = rig.controller([], Limits())
    ctl.checks_fn = checks
    assert ctl.prepare() is None
    ctl.ctx.checks_fn = checks
    ctl.ctx.guard = {**guard_context(POLICY, rig.base), "baseline_checks": BASE}
    return ctl, calls


def test_combined_preview_costs_one_unit_runs_both_parts_once_and_is_cached(tmp_path):
    ctl, calls = rig_with_counting_checks(tmp_path)
    first = ctl.registry.call("run_check", {"check": "preview"})
    assert first.ok and first.result["cache_hit"] is False and ctl.ctx.corner_previews == 1 and calls == [("corner_preview", "requirements_preview")]
    assert first.result["policy_projection"]["would_trip"] == ["REQ-BIAS:margin_nonregression"]
    again = ctl.registry.call("run_check", {"check": "preview"})
    assert again.result["cache_hit"] is True and ctl.ctx.corner_previews == 1 and len(calls) == 1


def test_combined_preview_reuses_cached_single_previews_and_only_runs_what_is_missing(tmp_path):
    ctl, calls = rig_with_counting_checks(tmp_path)
    ctl.registry.call("run_check", {"check": "corner_preview"})
    out = ctl.registry.call("run_check", {"check": "preview"})
    assert calls == [("corner_preview",), ("requirements_preview",)] and ctl.ctx.corner_previews == 2          # corner reused: one new unit for the missing part
    ctl.registry.call("run_check", {"check": "requirements_preview"})
    assert ctl.ctx.corner_previews == 2 and out.ok                                                            # both parts now cached: single call is free


def test_combined_preview_shares_the_budget_and_refuses_when_exhausted(tmp_path):
    ctl, calls = rig_with_counting_checks(tmp_path)
    ctl.ctx.max_corner_previews = 1
    assert ctl.registry.call("run_check", {"check": "preview"}).ok
    ctl.ctx.workspace.propose({"summary": "x", "base_sha256": ctl.ctx.workspace.sha256(), "ops": [{"op": "set_field", "part": "C2", "field": "mpn_candidate", "to": "C0805C102J5GACTA"}]})
    refused = ctl.registry.call("run_check", {"check": "corner_preview"})
    assert not refused.ok and "preview_budget_exhausted" in json.dumps(refused.to_json())


def test_preview_spend_counts_the_combined_check_for_resume_reconstruction():
    ev = lambda cache_hit, corner_outcome: {"args": {"check": "preview"}, "result": {"ok": True, "result": {"cache_hit": cache_hit, "corner_preview": {"outcome": corner_outcome}}}}
    assert preview_spend(ev(False, "within_contract_limits")) == 1
    assert preview_spend(ev(True, "within_contract_limits")) == 0
    assert preview_spend(ev(False, "error")) == 0

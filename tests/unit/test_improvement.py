"""IMPROVEMENT-mode tests. Synthetic records/pipelines are FIXTURES that test the policy and controller machinery; real-EDA evidence is in
tests/integration/test_improvement_real.py and runs/agent/improve-stage2-alias-fixture/. The restoration mode's own tests (test_harness.py) are untouched."""
import copy
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ae.harness.controller import Controller, Limits, Mode, parse_action
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.improvement import ACCEPT, PolicyError, decide_improvement, load_policy
from ae.harness.patch import sha256_text, validate_patch
from ae.harness.providers import ClaudeCliProvider, ScriptedProvider
from ae.harness.tasks import load_task
from ae.harness.workspace import REPO_ROOT, Workspace, trusted_digest

POLICY_PATH = REPO_ROOT / "src/ae/harness/tasks/improve_stage2_alias.yaml"
PRISTINE = REPO_ROOT / "designs/fe-aaf-001/components.yaml"
POL = load_policy(POLICY_PATH)
BASE_MANIFEST = PRISTINE.read_text()


# ------------------------------------------------------------------ synthetic records (same structure as the real pipeline's record.json)
def chk(cid, outcome="passed", value=None, margin=None, limit="", detail=""):
    return {"id": cid, "outcome": outcome, "value": value, "margin": margin, "limit": limit, "detail": detail}


def baseline_checks():
    return [chk("ERC"), chk("NETLIST_INTENT"), chk("DECK_VS_NETLIST"), chk("DRC_VIOLATIONS"), chk("DRC_UNCONNECTED"), chk("DRC_PARITY"), chk("REQ-MFG"),
            chk("BOM_CONSISTENCY", "blocked", detail="15 lines: 14 consistent, 0 MISMATCH, 1 unresolved"),
            chk("REQ-BIAS", value=9.65, margin=0.35, limit="<= 10"), chk("REQ-IQ", value=0.3003, margin=0.6997),
            chk("REQ-GAIN", value=1.3011, limit="1.235..1.365"), chk("REQ-FLAT", value=0.0980, margin=0.402, limit="<= 0.5"), chk("REQ-ALIAS", value=-60.33, margin=4.5),
            chk("REQ-ZIN", value=59013.0, limit=">= 10000"), chk("REQ-STAB", "failed", 52.8446, -49.8446, "<= 3.0"),
            chk("REQ-STAB-R3-AC-PEAKING-PROPOSED", value=0.105, margin=2.895), chk("REQ-HEADROOM", margin=0.2169), chk("REQ-SETTLE", value=0.00144, margin=0.40156),
            chk("REQ-NOISE", value=0.009746, margin=0.19025), chk("REQ-BOM", "failed", detail="J1,J2 no MPN"),
            chk("MODEL_STARTUP", "not_tested"), chk("MODEL_SATURATION", "not_tested"), chk("MODEL_OVERLOAD_RECOVERY", "not_tested"),
            chk("REQ-FLAT-CORNERS", value=0.4022, margin=0.0978, limit="<= 0.5"), chk("REQ-GAIN-CORNERS", value=1.3637, margin=0.0013, limit="1.235..1.365"),
            chk("REQ-ALIAS-CORNERS", value=-58.73526536671747, margin=2.935, limit="<= -55.8")]


BOM_BASE = [{"Refs": "R4,R5", "MPN": "RC0603FR-0716K5L", "MPN_Status": "listing_verified", "Value": "16.5k"}, {"Refs": "C3", "MPN": "C0805C562J5GACTU", "MPN_Status": "verified", "Value": "5.6n"}]


def write_dir(d: Path, gain=(1.2434, 1.3637), bom=BOM_BASE):
    (d / "release").mkdir(parents=True, exist_ok=True)
    (d / "corners_summary.json").write_text(json.dumps({"gain_min": gain[0], "gain_max": gain[1]}))
    with (d / "release/bom.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Refs", "Value", "Footprint", "MPN", "MPN_Status", "Manufacturer"])
        w.writeheader()
        for r in bom:
            w.writerow({"Footprint": "x", "Manufacturer": "y", **r})


def candidate(tmp_path, mutate=None, gain=(1.2430, 1.3600), bom=None, manifest=None):
    """A GOOD candidate by default (alias corners -59.20 = +0.46 dB, flat corners 0.45, gain inside reserves); `mutate` edits {id: dict} entries."""
    checks = {c["id"]: c for c in copy.deepcopy(baseline_checks())}
    checks["REQ-ALIAS-CORNERS"].update(value=-59.20)
    checks["REQ-ALIAS"].update(value=-61.0)
    checks["REQ-FLAT-CORNERS"].update(value=0.45)
    checks["REQ-FLAT"].update(value=0.13)
    if mutate:
        mutate(checks)
    d = tmp_path / "cand"
    write_dir(d, gain, bom or [{"Refs": "R4,R5", "MPN": "RC0603FR-0717K4L", "MPN_Status": "unverified_candidate", "Value": "17.4k"}, BOM_BASE[1]])
    return {"checks": list(checks.values())}, d


def edited_manifest():
    return BASE_MANIFEST.replace('R4: {role: "stage-2 input resistor", value: "16.5k"', 'R4: {role: "stage-2 input resistor", value: "17.4k"')


def run_decide(tmp_path, mutate=None, **kw):
    base_dir = tmp_path / "base"
    write_dir(base_dir)
    rec, cdir = candidate(tmp_path, mutate, **kw)
    return decide_improvement({"checks": baseline_checks()}, rec, POL, base_dir, cdir, BASE_MANIFEST, kw.get("manifest") or edited_manifest())


def rules(d):
    return {v["rule"] for v in d["policy_violations"]}


# ------------------------------------------------------------------ frozen policy
def test_policy_loads_and_verifies_against_frozen_baseline_and_contract():
    assert POL.task_id == "improve-stage2-alias" and POL.min_improvement == 0.25 and POL.allowed_parts == ("R4", "R5", "C3")
    assert POL.scope().part_pattern == "^(?:R4|R5|C3)$" and POL.max_ops == 6 and POL.sha256 == sha256_text(POLICY_PATH.read_text())


def test_policy_refuses_changed_baseline_or_inconsistent_contract_limits(tmp_path):
    y = yaml.safe_load(POLICY_PATH.read_text())
    y["baseline"]["components_sha256"] = "0" * 64
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(y))
    with pytest.raises(PolicyError, match="baseline_changed"):
        load_policy(p)
    y = yaml.safe_load(POLICY_PATH.read_text())
    y["hard_constraints"][0]["max"] = 0.52                       # would silently relax the contract limit 0.5 -> must be refused
    p.write_text(yaml.safe_dump(y))
    with pytest.raises(PolicyError, match="H1"):
        load_policy(p)


def test_policy_file_and_evidence_ledger_are_in_the_trusted_digest():
    files = trusted_digest()["files"]
    assert "src/ae/harness/tasks/improve_stage2_alias.yaml" in files and "evidence/bom-verification.yaml" in files and "src/ae/harness/improvement.py" in files


# ------------------------------------------------------------------ pure decision function
def test_genuine_improvement_is_accepted_for_the_objective_only(tmp_path):
    d = run_decide(tmp_path)
    assert d["verdict"] == ACCEPT and d["policy_violations"] == [] and d["release_ready"] is False
    assert d["objective"]["met"] and d["objective"]["improvement"] == pytest.approx(0.46473, abs=1e-4)
    assert "REQ-STAB" in d["unresolved_baseline_failures"] and "REQ-BOM" in d["unresolved_baseline_failures"]
    claims = [c["claim"] for c in d["blocked_claims"]]
    assert any(c.startswith("BOM_VERIFICATION") for c in claims) and "REQ-STAB" in claims and "scope" in d and "NOT a released" in d["scope"]
    assert [f["id"] for f in d["circuit_requirement_failures"]] == ["REQ-BOM", "REQ-STAB"] and all(f["pre_existing"] for f in d["circuit_requirement_failures"])


def test_noop_is_not_an_improvement_even_if_the_text_differs(tmp_path):
    def same(c):
        c["REQ-ALIAS-CORNERS"]["value"] = -58.73526536671747
        c["REQ-ALIAS"]["value"], c["REQ-FLAT"]["value"], c["REQ-FLAT-CORNERS"]["value"] = -60.33, 0.0980, 0.4022
    d = run_decide(tmp_path, same, gain=(1.2434, 1.3637))
    assert d["verdict"] == "rejected" and d["objective"]["met"] is False
    assert [v["kind"] for v in d["policy_violations"] if v["rule"] == "objective_improvement"] == ["no_change_within_jitter"]


def test_improvement_below_threshold_and_regression_are_rejected(tmp_path):
    d = run_decide(tmp_path, lambda c: c["REQ-ALIAS-CORNERS"].update(value=-58.9))               # +0.16 dB < 0.25
    assert [v["kind"] for v in d["policy_violations"] if v["rule"] == "objective_improvement"] == ["improvement_below_threshold"]
    d = run_decide(tmp_path, lambda c: c["REQ-ALIAS-CORNERS"].update(value=-58.0))
    assert [v["kind"] for v in d["policy_violations"] if v["rule"] == "objective_improvement"] == ["regression"]


@pytest.mark.parametrize("mutate,gain,rule", [
    (lambda c: c["REQ-FLAT-CORNERS"].update(value=0.49), (1.243, 1.36), "H1_hard_limit"),              # inside the 0.5 contract limit, but eats the 0.02 reserve
    (lambda c: None, (1.243, 1.3648), "H3_gain_max_corners"),                                          # inside 1.365 but inside the reserve
    (lambda c: None, (1.2352, 1.36), "H2_gain_min_corners"),
    (lambda c: c["REQ-GAIN"].update(value=1.2349), (1.243, 1.36), "H5_gain_nominal"),
    (lambda c: c["REQ-ALIAS"].update(value=-60.0), (1.243, 1.36), "H6_alias_nominal_nonregression"),   # nominal rejection got worse although corners improved
    (lambda c: c["REQ-FLAT-CORNERS"].update(outcome="failed", value=0.6), (1.243, 1.36), "H7_passed_stays_passed"),
])
def test_hard_constraints_and_reserves(tmp_path, mutate, gain, rule):
    d = run_decide(tmp_path, mutate, gain=gain)
    assert d["verdict"] == "rejected" and rule in rules(d)


def test_failed_baseline_metric_may_not_degrade_even_though_it_is_still_failed(tmp_path):
    d = run_decide(tmp_path, lambda c: c["REQ-STAB"].update(value=52.9))
    assert d["verdict"] == "rejected" and "REQ-STAB_nonregression" in rules(d)
    assert [f["id"] for f in d["circuit_requirement_failures"]] == ["REQ-BOM", "REQ-STAB"]           # STAB was and is failed: reported, but the VIOLATION is the degradation
    d = run_decide(tmp_path, lambda c: c["REQ-STAB"].update(value=52.0))                              # a genuine improvement of a failed metric is allowed (still failed)
    assert d["verdict"] == ACCEPT
    d = run_decide(tmp_path, lambda c: c["REQ-STAB"].update(outcome="passed", value=2.0))
    assert "REQ-STAB_status_change_needs_review" in rules(d)


def test_other_metrics_may_consume_at_most_ten_percent_of_their_margin(tmp_path):
    d = run_decide(tmp_path, lambda c: c["REQ-SETTLE"].update(margin=0.40156 * 0.95))                 # loses 5 % of margin: allowed
    assert d["verdict"] == ACCEPT
    d = run_decide(tmp_path, lambda c: c["REQ-SETTLE"].update(margin=0.40156 * 0.85))                 # 15 %: rejected
    assert [v["check"] for v in d["policy_violations"] if v["rule"] == "margin_nonregression"] == ["REQ-SETTLE"]
    d = run_decide(tmp_path, lambda c: c["REQ-ZIN"].update(value=50000.0))                             # margin derived from the parsed ">= 10000" limit: 49013 -> 40000 is -18 %
    assert [v["check"] for v in d["policy_violations"] if v["rule"] == "margin_nonregression"] == ["REQ-ZIN"]


def test_model_not_tested_bom_and_status_rules(tmp_path):
    assert "MODEL_not_tested_stays" in rules(run_decide(tmp_path, lambda c: c["MODEL_STARTUP"].update(outcome="passed")))
    assert "BOM_CONSISTENCY_blocked_with_0_mismatch" in rules(run_decide(tmp_path, lambda c: c["BOM_CONSISTENCY"].update(outcome="failed", detail="1 MISMATCH")))
    assert "REQ-BOM_status" in rules(run_decide(tmp_path, lambda c: c["REQ-BOM"].update(outcome="passed")))
    assert "unchanged_status_required" in rules(run_decide(tmp_path, lambda c: c["DRC_PARITY"].update(outcome="failed")))
    d = run_decide(tmp_path, bom=[{"Refs": "R4,R5", "MPN": "RC0603FR-0717K4L", "MPN_Status": "verified", "Value": "17.4k"}, BOM_BASE[1]])     # agent cannot launder verification
    assert "bom_edited_mpn_not_marked_unverified" in rules(d)
    d = run_decide(tmp_path, bom=[{"Refs": "R4,R5", "MPN": "RC0603FR-0717K4L", "MPN_Status": "unverified_candidate", "Value": "17.4k"}, {**BOM_BASE[1], "MPN": "C0805C999J5GACTU"}])
    assert "bom_row_changed_outside_edit" in rules(d)


def test_missing_error_and_unreproduced_baseline_block_instead_of_judging(tmp_path):
    rec, cdir = candidate(tmp_path)
    rec["checks"] = [c for c in rec["checks"] if c["id"] != "REQ-ZIN"]
    base_dir = tmp_path / "base"
    write_dir(base_dir)
    d = decide_improvement({"checks": baseline_checks()}, rec, POL, base_dir, cdir, BASE_MANIFEST, edited_manifest())
    assert d["verdict"] == "blocked" and "missing_check:REQ-ZIN" in d["blocked"]["reason"]
    assert run_decide(tmp_path, lambda c: c["REQ-ALIAS-CORNERS"].update(outcome="blocked"))["verdict"] == "blocked"
    base = baseline_checks()
    for c in base:
        if c["id"] == "REQ-ALIAS-CORNERS":
            c["value"] = -57.0                                                                         # fresh baseline does not reproduce the frozen value -> toolchain drift -> blocked
    rec, cdir = candidate(tmp_path)
    d = decide_improvement({"checks": base}, rec, POL, base_dir, cdir, BASE_MANIFEST, edited_manifest())
    assert d["verdict"] == "blocked" and "baseline_not_reproduced" in d["blocked"]["reason"][0]


def test_nonfinite_candidate_values_are_rejected_not_accepted(tmp_path):
    d = run_decide(tmp_path, lambda c: c["REQ-ALIAS-CORNERS"].update(value=float("nan")))
    assert d["verdict"] == "rejected"
    d = run_decide(tmp_path, lambda c: c["REQ-FLAT-CORNERS"].update(value=float("nan")))
    assert "H1_hard_limit" in rules(d)
    assert "H3_gain_max_corners" in rules(run_decide(tmp_path, gain=(1.243, float("inf"))))


def test_decision_separates_policy_violations_from_circuit_failures(tmp_path):
    d = run_decide(tmp_path, lambda c: c["REQ-FLAT-CORNERS"].update(outcome="failed", value=0.6))
    new_failures = [f["id"] for f in d["circuit_requirement_failures"] if not f["pre_existing"]]
    assert new_failures == ["REQ-FLAT-CORNERS"] and {"H1_hard_limit", "H7_passed_stays_passed"} <= rules(d)


# ------------------------------------------------------------------ patch scope for this task
def test_out_of_scope_and_unpaired_edits_for_the_task_scope():
    scope = POL.scope()
    sha = sha256_text(BASE_MANIFEST)
    for part, field, to in [("R1", "value", "60.4k"), ("C1", "value", "20n"), ("U1", "value", "x"), ("R4", "tol", "5%"), ("R4", "mpn_status", "verified")]:
        with pytest.raises(Exception) as e:
            validate_patch({"summary": "s", "base_sha256": sha, "ops": [{"op": "set_field", "part": part, "field": field, "to": to}]}, BASE_MANIFEST, scope)
        assert getattr(e.value, "code", "") == "out_of_scope"
    ops = [{"op": "set_field", "part": p, "field": "value", "to": "1k"} for p in ("R4", "R5", "C3")] * 3             # 9 ops > max_ops 6
    with pytest.raises(Exception) as e:
        validate_patch({"summary": "s", "base_sha256": sha, "ops": ops}, BASE_MANIFEST, scope)
    assert e.value.code == "malformed_patch"
    vp = validate_patch({"summary": "s", "base_sha256": sha, "ops": [{"op": "set_field", "part": "R4", "field": "value", "to": "17.4k"}]}, BASE_MANIFEST, scope)
    assert vp.ops[0]["from"] == "16.5k"


# ------------------------------------------------------------------ controller in improvement mode (fake pipeline = fixture)
def synth_pipeline(components_path, out_dir):
    """FIXTURE: R4 value 17.4k => good improvement; C3 5.1n => regression; anything else => baseline numbers."""
    parts = yaml.safe_load(Path(components_path).read_text())["parts"]
    checks = {c["id"]: c for c in copy.deepcopy(baseline_checks())}
    gain = (1.2434, 1.3637)
    if parts["R4"]["value"] == "17.4k":
        checks["REQ-ALIAS-CORNERS"]["value"], checks["REQ-FLAT-CORNERS"]["value"], gain = -59.2, 0.45, (1.2430, 1.3600)
        bom = [{"Refs": "R4,R5", "MPN": "RC0603FR-0717K4L", "MPN_Status": "unverified_candidate", "Value": "17.4k"}, BOM_BASE[1]]
    else:
        bom = BOM_BASE
    if parts["C3"]["value"] == "5.1n":
        checks["REQ-ALIAS-CORNERS"]["value"] = -57.9
    write_dir(out_dir, gain, bom)
    rec = {"summary": {}, "checks": list(checks.values()), "inputs_sha256": {"components": sha256_text(Path(components_path).read_text())}}
    (out_dir / "record.json").write_text(json.dumps(rec))
    return rec


def imp_controller(tmp_path, script, **kw):
    base_dir = tmp_path / "base"
    base = synth_pipeline(PRISTINE, base_dir)
    ev = TrustedEvaluator()
    ev.run_pipeline = synth_pipeline
    ctl = Controller(tmp_path / "run", load_task("improve-stage2-alias"), ScriptedProvider(script), ev, kw.pop("limits", Limits(max_iterations=2)), Mode.ENGINEERED_SINGLE,
                     baseline_record=base, baseline_dir=base_dir, checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names}, **kw)
    return ctl


def A(tool, **args):
    return {"reasoning": "fixture", "action": {"tool": tool, "args": args}}


def patch_ops(ops):
    def f(transcript):
        sha = re.findall(r'"manifest_sha256": "([0-9a-f]{64})"', transcript)[-1]
        return json.dumps(A("propose_patch", patch={"summary": "fixture", "base_sha256": sha, "ops": [{"op": "set_field", "part": p, "field": fl, "to": t} for p, fl, t in ops]}))
    return f


def test_controller_accepts_a_scripted_improvement_and_labels_it_a_fixture(tmp_path):
    ctl = imp_controller(tmp_path, [A("get_design"), patch_ops([("R4", "value", "17.4k"), ("R4", "mpn_candidate", "RC0603FR-0717K4L"), ("R5", "value", "17.4k"), ("R5", "mpn_candidate", "RC0603FR-0717K4L")]), A("submit", summary="s")])
    s = ctl.run()
    assert s["final_state"] == "ACCEPTED" and s["task_kind"] == "improvement" and s["fixture"] is True and s["live_agent_success"] is False
    out = tmp_path / "run/output"
    dec = json.loads((out / "evaluator_decision.json").read_text())
    assert dec["verdict"] == ACCEPT and dec["release_ready"] is False and "NOT a released" in dec["scope"]
    assert "ACCEPTED FOR THE FROZEN OBJECTIVE ONLY" in (out / "README.txt").read_text()
    assert sha256_text(PRISTINE.read_text()) == sha256_text((REPO_ROOT / "designs/fe-aaf-001/components.yaml").read_text())       # adopted reference untouched
    assert (out / "candidate_vs_adopted_reference.diff").read_text().count("\n-  R4:") == 1


def test_controller_rejects_regression_noop_and_out_of_scope_without_side_effects(tmp_path):
    ctl = imp_controller(tmp_path, [A("get_design"), patch_ops([("C3", "value", "5.1n"), ("C3", "mpn_candidate", "C0805C512J5GACTU")]), A("submit", summary="s")], limits=Limits(max_iterations=1))
    s = ctl.run()
    d = json.loads((tmp_path / "run/evaluations/decision_01.json").read_text())
    assert s["stop_reason"] == "max_iterations" and d["verdict"] == "rejected" and d["objective"]["improvement"] < 0
    assert [v["kind"] for v in d["policy_violations"] if v["rule"] == "objective_improvement"] == ["regression"]
    # out of scope: R1 is not an allowed edit; the workspace is untouched and nothing is evaluated
    tmp2 = tmp_path / "t2"
    tmp2.mkdir()
    ctl = imp_controller(tmp2, [A("get_design"), patch_ops([("R1", "value", "60.4k")]), A("get_design")], limits=Limits(max_iterations=1))
    ctl.run()
    assert ctl.ws.sha256() == sha256_text(PRISTINE.read_text()) and ctl.ws.history == [] and ctl.state["evaluations"] == 0
    res = [json.loads(l) for l in (tmp2 / "run/trace.jsonl").read_text().splitlines()]
    assert [e["result"]["error"]["code"] for e in res if e["type"] == "tool_call" and e["tool"] == "propose_patch"] == ["out_of_scope"]
    assert not list((tmp2 / "run/workspace/patches").glob("*.json"))
    # numerically identical value written differently: the validator accepts the text change, the policy refuses to count it
    tmp3 = tmp_path / "t3"
    tmp3.mkdir()
    ctl = imp_controller(tmp3, [A("get_design"), patch_ops([("C3", "value", "5.60n")]), A("submit", summary="s"), A("get_design")], limits=Limits(max_iterations=1, max_model_calls=4))
    ctl.run()
    # since the state-based submit gate a numerically identical text edit is refused BEFORE evaluation (it used to be evaluated and rejected as a no-op)
    assert ctl.state["evaluations"] == 0 and ctl.state["refused_submits"] == 1 and not (tmp3 / "run/evaluations").exists()


def test_tampered_policy_blocks_the_evaluation(tmp_path):
    ctl = imp_controller(tmp_path, [A("get_design"), patch_ops([("R4", "value", "17.4k"), ("R4", "mpn_candidate", "RC0603FR-0717K4L")]), A("submit", summary="s")])
    real = trusted_digest
    import ae.harness.evaluator as evmod
    calls = {"n": 0}

    def tampered(repo=REPO_ROOT):
        d = real(repo)
        calls["n"] += 1
        return d if calls["n"] == 1 else {**d, "combined": "f" * 64}                                  # digest differs after the evaluator started => policy/evaluator changed
    evmod.trusted_digest = tampered
    try:
        s = ctl.run()
    finally:
        evmod.trusted_digest = real
    assert s["stop_reason"] == "evaluation_blocked"


# ------------------------------------------------------------------ action boundary (malformed / ambiguous / multiple / unknown / nonfinite) - rejection has NO side effects
@pytest.mark.parametrize("text,fragment", [
    ('{"action": {"tool": "a"}} {"action": {"tool": "b"}}', "ambiguous"),
    ('[{"action": {"tool": "a"}}, {"action": {"tool": "b"}}]', "ambiguous"),
    ('{"action": {"tool": "a", "args": {"x": NaN}}}', "non-finite"),
    ('{"action": {"tool": "a", "args": {"x": -Infinity}}}', "non-finite"),
    ('{"action": {"tool": "a", "args": {"x": 1e999}}}', "non-finite"),
    ('{"action": {"tool": "a", "shell": "rm -rf /"}}', "action must be"),
    ('{"action": {"tool": 7}}', "action must be"),
    ('{"action": {"tool": "a", "args": [1]}}', "action must be"),
    ('{"action": "get_design"}', "action must be"),
    ('{"reasoning": "r", "action": {"tool": "a"}, "extra": 1}', "unknown top-level"),
    ('{"tool": "a"}', "no 'action'"),
    ("no json here", "no JSON"),
    ("", "no JSON"),
])
def test_parse_action_rejects(text, fragment):
    act, err = parse_action(text)
    assert act is None and fragment in err


def test_parse_action_tolerates_prose_around_exactly_one_action():
    act, err = parse_action('<invoke name="x"></invoke> sure {not json} {"reasoning": "r", "action": {"tool": "get_design", "args": {"parts": ["R4"]}}} thanks')
    assert err is None and act["tool"] == "get_design" and act["args"] == {"parts": ["R4"]}


def test_rejected_actions_dispatch_nothing_and_change_nothing(tmp_path):
    bad = ['{"action": {"tool": "propose_patch", "args": {}}} {"action": {"tool": "submit", "args": {}}}', '{"action": {"tool": "run_check", "args": {"check": NaN}}}', "I will edit the file now"]
    ctl = imp_controller(tmp_path, list(bad), limits=Limits(max_iterations=1))
    before = sha256_text(PRISTINE.read_text())
    s = ctl.run()
    assert s["stop_reason"] == "invalid_replies" and ctl.state["invalid_replies"] == 3
    assert ctl.state["tool_calls"] == 0 and ctl.state["evaluations"] == 0 and ctl.ws.sha256() == before and ctl.ws.history == []
    assert not list((tmp_path / "run/workspace/patches").glob("*.json")) and not (tmp_path / "run/evaluations").exists()


def test_unknown_tool_unknown_op_and_nonfinite_patch_values_have_no_side_effects(tmp_path):
    def bad_ops(transcript):
        sha = re.findall(r'"manifest_sha256": "([0-9a-f]{64})"', transcript)[-1]
        return json.dumps(A("propose_patch", patch={"summary": "s", "base_sha256": sha, "ops": [{"op": "delete_part", "part": "R4", "field": "value", "to": "x"}]}))

    def nonfinite_value(transcript):
        sha = re.findall(r'"manifest_sha256": "([0-9a-f]{64})"', transcript)[-1]
        return json.dumps(A("propose_patch", patch={"summary": "s", "base_sha256": sha, "ops": [{"op": "set_field", "part": "R4", "field": "value", "to": 1e999}]}))
    ctl = imp_controller(tmp_path, [A("get_design"), A("format_disk"), bad_ops, nonfinite_value, A("run_check", check="rm -rf"), A("get_design")], limits=Limits(max_iterations=1, max_model_calls=6))
    before = ctl.run() and sha256_text(PRISTINE.read_text())
    res = {e["tool"]: e["result"]["error"]["code"] for e in (json.loads(l) for l in (tmp_path / "run/trace.jsonl").read_text().splitlines()) if e["type"] == "tool_call" and not e["result"]["ok"]}
    assert res["format_disk"] == "unknown_tool" and res["run_check"] == "invalid_arguments" and res["propose_patch"] == "malformed_patch"
    assert ctl.ws.sha256() == before and ctl.ws.history == [] and ctl.state["evaluations"] == 0


# ------------------------------------------------------------------ provider configuration (NO model inference is started by these tests)
def test_cli_provider_command_disables_tools_settings_mcp_and_persistence():
    cmd = ClaudeCliProvider(None, 0.5).command("SYSTEM")
    assert cmd[0] == "claude" and "-p" in cmd and "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == ""
    assert not {"--mcp-config", "--allowedTools", "--allowed-tools", "--dangerously-skip-permissions", "--permission-mode", "--add-dir"} & set(cmd)
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.5"


@pytest.mark.skipif(not shutil.which("claude"), reason="claude CLI not installed")
def test_installed_cli_documents_the_flags_we_rely_on_help_only_no_inference():
    out = subprocess.run(["claude", "--help"], capture_output=True, text=True, timeout=30).stdout
    for flag in ("--tools", "--setting-sources", "--strict-mcp-config", "--no-session-persistence", "--max-budget-usd", "--system-prompt"):
        assert flag in out

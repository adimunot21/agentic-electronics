"""Harness tests. FIXTURES (scripted agents, fake pipeline/checks) test the MACHINERY; real-EDA evidence is in tests/integration/test_harness_real.py and runs/agent/."""
import json
import time
from pathlib import Path

import pytest
import yaml

from ae import bomcheck
from ae.harness.contracts import Arg, ToolRegistry, ToolSpec
from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import AcceptancePolicy, TrustedEvaluator, decide
from ae.harness.patch import sha256_text, validate_patch
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task
from ae.harness.workspace import REPO_ROOT, Workspace
from ae.spice import parse_si

PRISTINE = REPO_ROOT / "designs/fe-aaf-001/components.yaml"
TASK = load_task("dev-c1-value")


# ------------------------------------------------------------------ fake EDA (FIXTURE): same observable structure as the real records
def _c1_ok(path):
    parts = yaml.safe_load(Path(path).read_text())["parts"]
    return parts["C1"]["value"] == "18n", parts


def fake_pipeline(components_path, out_dir):
    ok, parts = _c1_ok(components_path)
    d = bomcheck.decode(parts["C1"]["mpn_candidate"])
    bom_ok = abs(parse_si(parts["C1"]["value"]) - d["value"]) < 1e-3 * d["value"]
    checks = [{"id": "BOM_CONSISTENCY", "outcome": "blocked" if bom_ok else "failed", "value": None, "detail": "unresolved J1/J2" if bom_ok else "C1: VALUE mismatch"},
              {"id": "REQ-FLAT", "outcome": "passed" if ok else "failed", "value": 0.25 if ok else 3.0, "detail": ""},
              {"id": "REQ-GAIN", "outcome": "passed" if ok else "failed", "value": 1.30 if ok else 0.27, "detail": ""},
              {"id": "REQ-STAB", "outcome": "failed", "value": 52.8, "detail": "rev 2 (in force)"},
              {"id": "REQ-BOM", "outcome": "failed", "value": None, "detail": "J1/J2 no MPN"}]
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = {"summary": {}, "checks": checks, "inputs_sha256": {"components": sha256_text(Path(components_path).read_text())}}
    (out_dir / "record.json").write_text(json.dumps(rec))
    return rec


def fake_checks(names, components_path, workdir):
    ok, parts = _c1_ok(components_path)
    d = bomcheck.decode(parts["C1"]["mpn_candidate"])
    bom_ok = abs(parse_si(parts["C1"]["value"]) - d["value"]) < 1e-3 * d["value"]
    out = {}
    if "bom_consistency" in names:
        out["bom_consistency"] = {"outcome": "passed" if bom_ok else "failed", "detail": "ok" if bom_ok else "C1: VALUE mismatch"}
    if "ac_response" in names:
        out["ac_response"] = {"outcome": "passed" if ok else "failed", "measurements": {"gain": 1.3 if ok else 0.27}}
    return out


def make(tmp_path, script, limits=Limits(), calls=None, **kw):
    base = fake_pipeline(PRISTINE, tmp_path / "_b")
    if calls is not None:
        def counting(names, p, w):
            calls.append((tuple(names), sha256_text(Path(p).read_text())))
            return fake_checks(names, p, w)
        checks = counting
    else:
        checks = fake_checks
    ev = TrustedEvaluator()
    ev_pipeline = fake_pipeline
    ctl = Controller(tmp_path / "run", TASK, ScriptedProvider(script), ev, limits, baseline_record=base, checks_fn=checks, **kw)
    ev.run_pipeline = ev_pipeline                        # the evaluator's pipeline is replaced by the fake ONLY in this fixture
    return ctl


def A(tool, **args):
    return {"reasoning": "fixture", "action": {"tool": tool, "args": args}}


def patch_action(part, field, to, summary="fixture"):
    def f(transcript):
        import re
        sha = re.findall(r"([0-9a-f]{64})", transcript)[-1] if "manifest_sha256" in transcript else "0" * 64
        shas = re.findall(r'"(?:manifest_sha256|new_base_sha256|defective_sha256)": "([0-9a-f]{64})"', transcript)
        return json.dumps(A("propose_patch", patch={"summary": summary, "base_sha256": shas[-1] if shas else sha, "ops": [{"op": "set_field", "part": part, "field": field, "to": to}]}))
    return f


# ------------------------------------------------------------------ patches / boundary
def ws(tmp_path):
    w = Workspace(tmp_path / "w")
    w.init(PRISTINE)
    return w


def test_valid_patch_applies_and_changes_hash(tmp_path):
    w = ws(tmp_path)
    before = w.sha256()
    rec = w.propose({"summary": "s", "base_sha256": before, "ops": [{"op": "set_field", "part": "C1", "field": "value", "to": "22n"}]})
    assert w.sha256() != before and rec["before_sha256"] == before and rec["ops"][0]["from"] == "18n"
    assert yaml.safe_load(w.text())["parts"]["C1"]["value"] == "22n"


def test_mpn_change_forces_evidence_status_down(tmp_path):
    w = ws(tmp_path)
    rec = w.propose({"summary": "s", "base_sha256": w.sha256(), "ops": [{"op": "set_field", "part": "C1", "field": "mpn_candidate", "to": "C0805C102J5GACTU"}]})
    assert yaml.safe_load(w.text())["parts"]["C1"]["mpn_status"] == "unverified_candidate" and rec["forced_side_effects"]


@pytest.mark.parametrize("bad,code", [
    ({"op": "set_field", "part": "U1", "field": "value", "to": "x1"}, "out_of_scope"),
    ({"op": "set_field", "part": "REQ-GAIN", "field": "value", "to": "1n"}, "out_of_scope"),
    ({"op": "set_field", "part": "C1", "field": "mpn_status", "to": "verified"}, "out_of_scope"),
    ({"op": "set_field", "part": "C1", "field": "max", "to": "2"}, "out_of_scope"),
    ({"op": "write_file", "part": "C1", "field": "value", "to": "1n"}, "malformed_patch"),
    ({"op": "set_field", "part": "C1", "field": "value", "to": "1n", "path": "requirements/fe-aaf-001.yaml"}, "malformed_patch"),
    ({"op": "set_field", "part": "C1", "field": "value", "to": "18n"}, "malformed_patch"),          # no change
    ({"op": "set_field", "part": "C1", "field": "value", "to": "1n; rm -rf"}, "malformed_patch"),
])
def test_out_of_scope_and_malformed_patches_are_rejected_before_application(tmp_path, bad, code):
    w = ws(tmp_path)
    before = w.text()
    with pytest.raises(Exception) as e:
        w.propose({"summary": "s", "base_sha256": w.sha256(), "ops": [bad]})
    assert e.value.code == code and w.text() == before and not w.history


def test_patch_with_extra_top_level_keys_cannot_name_a_file(tmp_path):
    w = ws(tmp_path)
    with pytest.raises(Exception) as e:
        w.propose({"summary": "s", "base_sha256": w.sha256(), "ops": [], "target_file": "src/ae/harness/evaluator.py"})
    assert e.value.code == "malformed_patch"


def test_stale_base_hash_is_rejected_not_merged(tmp_path):
    w = ws(tmp_path)
    stale = w.sha256()
    w.propose({"summary": "s", "base_sha256": stale, "ops": [{"op": "set_field", "part": "C1", "field": "value", "to": "22n"}]})
    with pytest.raises(Exception) as e:
        w.propose({"summary": "s", "base_sha256": stale, "ops": [{"op": "set_field", "part": "C2", "field": "value", "to": "2n"}]})
    assert e.value.code == "stale_base" and yaml.safe_load(w.text())["parts"]["C2"]["value"] == "1n"


# ------------------------------------------------------------------ tools: failures are never successes
def test_tool_timeout_exception_unknown_and_invalid_args_are_explicit_failures():
    reg = ToolRegistry()
    reg.register(ToolSpec("slow", "d", {}, timeout_s=0.2), lambda: time.sleep(2))
    reg.register(ToolSpec("boom", "d", {}), lambda: 1 / 0)
    reg.register(ToolSpec("typed", "d", {"n": Arg("int", "n")}), lambda n: {"n": n})
    assert reg.call("slow", {}).error["code"] == "timeout" and not reg.call("slow", {}).ok
    assert reg.call("boom", {}).error["code"] == "internal"
    assert reg.call("nope", {}).error["code"] == "unknown_tool"
    assert reg.call("typed", {"n": "x"}).error["code"] == "invalid_arguments"
    assert reg.call("typed", {"n": 1, "extra": 2}).error["code"] == "invalid_arguments"
    assert reg.call("typed", {"n": 3}).ok


def test_unreadable_check_result_is_an_error_not_a_pass(tmp_path):
    from ae.harness.tools import AgentContext, build_registry
    w = ws(tmp_path)
    ctx = AgentContext(w, tmp_path / "s", {}, checks_fn=lambda n, p, d: {"bom_consistency": "garbage"})
    r = build_registry(ctx).call("run_check", {"check": "bom_consistency"})
    assert not r.ok and r.error["code"] == "unreadable_result"


def test_evidence_is_invalidated_by_an_edit(tmp_path):
    from ae.harness.tools import AgentContext, build_registry
    calls = []
    w = ws(tmp_path)
    ctx = AgentContext(w, tmp_path / "s", {}, checks_fn=lambda n, p, d: calls.append(1) or fake_checks(n, p, d), trusted_combined="T")
    reg = build_registry(ctx)
    a, b = reg.call("run_check", {"check": "ac_response"}), reg.call("run_check", {"check": "ac_response"})
    assert a.result["cache_hit"] is False and b.result["cache_hit"] is True and len(calls) == 1          # same design hash + trusted digest: evidence reusable
    reg.call("propose_patch", {"patch": {"summary": "s", "base_sha256": w.sha256(), "ops": [{"op": "set_field", "part": "C1", "field": "value", "to": "1n"}]}})
    c = reg.call("run_check", {"check": "ac_response"})
    assert c.result["cache_hit"] is False and len(calls) == 2                                            # edit => old evidence unreachable
    ctx.trusted_combined = "T2"
    assert reg.call("run_check", {"check": "ac_response"}).result["cache_hit"] is False                  # trusted code changed => also invalid


# ------------------------------------------------------------------ acceptance policy (pure)
BASE = fake_pipeline(PRISTINE, Path("/tmp/_pol"))["checks"]


def test_policy_accepts_exact_restoration_and_keeps_unresolved_visible():
    d = decide(BASE, [dict(c) for c in BASE])
    assert d["verdict"] == "accepted" and d["release_ready"] is False
    assert {"REQ-STAB", "REQ-BOM", "BOM_CONSISTENCY"} <= set(d["unresolved_baseline_failures"])


def test_policy_rejects_regression_even_when_the_scoped_check_is_fixed():
    cand = [dict(c) for c in BASE]
    next(c for c in cand if c["id"] == "REQ-FLAT").update(outcome="failed", value=3.0)
    d = decide(BASE, cand)
    assert d["verdict"] == "rejected" and {v["kind"] for v in d["violations"]} >= {"regression", "measurement_drift"}


def test_policy_does_not_let_stab_be_silently_improved_or_moved():
    cand = [dict(c) for c in BASE]
    next(c for c in cand if c["id"] == "REQ-STAB").update(outcome="passed", value=0.1)
    d = decide(BASE, cand)
    kinds = {v["kind"] for v in d["violations"]}
    assert d["verdict"] == "rejected" and "unexpected_improvement_needs_review" in kinds and "measurement_drift" in kinds
    moved = [dict(c) for c in BASE]
    next(c for c in moved if c["id"] == "REQ-STAB").update(value=55.0)                    # still failed but the raw evidence moved: cannot be assessed safely
    assert decide(BASE, moved)["verdict"] == "rejected"


def test_policy_rejects_missing_and_new_checks():
    cand = [c for c in BASE if c["id"] != "REQ-GAIN"] + [{"id": "EXTRA", "outcome": "passed", "value": None}]
    kinds = {v["kind"] for v in decide(BASE, cand)["violations"]}
    assert {"missing_check", "unknown_new_check"} <= kinds


def test_a_compliant_improvement_policy_is_explicit_not_hardcoded_failure():
    cand = [dict(c) for c in BASE]
    next(c for c in cand if c["id"] == "REQ-STAB").update(outcome="passed", value=52.8)
    assert decide(BASE, cand, AcceptancePolicy(allow_improvements=True))["verdict"] == "accepted"      # policy knob exists; default stays strict


# ------------------------------------------------------------------ controller
def test_scripted_repair_is_accepted_labelled_fixture_and_never_live_success(tmp_path):
    ctl = make(tmp_path, [A("get_design"), patch_action("C1", "value", "18n"), A("run_check", check="bom_consistency"), A("submit", summary="restore")])
    s = ctl.run()
    assert s["stop_reason"] == "accepted" and s["fixture"] is True and s["live_agent_success"] is False and s["accepted_by_independent_evaluator"] is True
    out = tmp_path / "run/output"
    assert (out / "agent_patch_vs_defect.diff").read_text() and (out / "candidate_vs_adopted_reference.diff").read_text() == ""           # exact restoration, reference untouched
    assert yaml.safe_load(PRISTINE.read_text())["parts"]["C1"]["value"] == "18n"
    assert "REQ-STAB" in json.loads((out / "evaluator_decision.json").read_text())["unresolved_baseline_failures"]
    assert s["usage"]["cost_usd_reported"] == 0.0 and "fixture (no model)" in (tmp_path / "run/trace.jsonl").read_text()


def test_wrong_fix_that_passes_the_scoped_check_is_rejected_then_correct_fix_accepted(tmp_path):
    script = [A("get_design"), patch_action("C1", "mpn_candidate", "C0805C102J5GACTU"), A("submit", summary="match MPN to value"),
              patch_action("C1", "mpn_candidate", "C0805C183J5GACTU"), patch_action("C1", "value", "18n"), A("submit", summary="restore value and MPN")]
    s = make(tmp_path, script, Limits(max_iterations=3)).run()
    d1 = json.loads((tmp_path / "run/evaluations/decision_01.json").read_text())
    assert d1["verdict"] == "rejected" and any(v["id"] == "REQ-FLAT" and v["kind"] == "regression" for v in d1["violations"])     # BOM check alone would have passed
    assert s["stop_reason"] == "accepted" and s["iterations"] == 2


def test_duplicate_submission_and_no_progress_stop(tmp_path):
    script = [A("submit", summary="no change"), A("submit", summary="again"), A("submit", summary="again")]
    s = make(tmp_path, script, Limits(no_progress_limit=2)).run()
    assert s["stop_reason"] == "no_progress" and s["evaluations"] == 1


def test_repeated_identical_patch_stops(tmp_path):
    script = [patch_action("C1", "value", "22n"), patch_action("C1", "value", "1n"), patch_action("C1", "value", "22n")]     # defect state is C1=1n: 1n->22n, 22n->1n, 1n->22n (identical to the first)
    s = make(tmp_path, script, Limits(identical_patch_limit=1)).run()
    assert s["stop_reason"] == "repeated_identical_patch"


def test_max_iterations_stop_and_resume_with_raised_limit_reaches_acceptance(tmp_path):
    script = [patch_action("C1", "value", "22n"), A("submit", summary="try 22n")]
    ctl = make(tmp_path, script, Limits(max_iterations=1))
    s = ctl.run()
    assert s["stop_reason"] == "max_iterations" and (tmp_path / "run/state.json").exists()
    ev = TrustedEvaluator(); ev.run_pipeline = fake_pipeline
    resumed = Controller.resume(tmp_path / "run", task=TASK, provider=ScriptedProvider([patch_action("C1", "value", "18n"), A("submit", summary="restore")]), evaluator=ev,
                                limits=Limits(max_iterations=3), checks_fn=fake_checks)
    s2 = resumed.continue_run()
    assert s2["stop_reason"] == "accepted" and s2["iterations"] == 2


def test_invalid_replies_and_provider_error_stop_explicitly(tmp_path):
    assert make(tmp_path / "a", ["not json", "still not", "nope"], Limits(max_invalid_replies=3)).run()["stop_reason"] == "invalid_replies"
    assert make(tmp_path / "b", []).run()["stop_reason"] == "provider_error"


def test_trusted_code_tamper_blocks_evaluation_instead_of_passing(tmp_path):
    ctl = make(tmp_path, [patch_action("C1", "value", "18n"), A("submit", summary="x")])
    ctl.evaluator.digest_at_start = {**ctl.evaluator.digest_at_start, "combined": "tampered"}
    s = ctl.run()
    assert s["stop_reason"] == "evaluation_blocked" and not s["accepted_by_independent_evaluator"]


def test_tool_limit_and_budget_stops(tmp_path):
    s = make(tmp_path, [A("get_design")] * 5, Limits(max_tool_calls=2)).run()
    assert s["stop_reason"] == "max_tool_calls"


def test_run_record_is_reproducible_for_identical_scripted_runs(tmp_path):
    def run(sub):
        s = make(tmp_path / sub, [A("get_design"), patch_action("C1", "value", "18n"), A("submit", summary="r")]).run()
        events = [json.loads(l) for l in (tmp_path / sub / "run/trace.jsonl").read_text().splitlines()]
        keep = [(e["type"], e.get("verdict"), e.get("after_sha256"), e.get("to")) for e in events if e["type"] in ("transition", "evaluation")]
        return s["stop_reason"], keep, json.loads((tmp_path / sub / "run/workspace/patches/001.json").read_text())["after_sha256"]
    assert run("one") == run("two")


def test_unimplemented_modes_and_heldout_tasks_refuse():
    for m in (Mode.BASIC_SINGLE, Mode.COORDINATED_SPECIALISTS):
        with pytest.raises(NotImplementedError):
            Controller(Path("/tmp/x"), TASK, ScriptedProvider([]), TrustedEvaluator(), mode=m)
    with pytest.raises(PermissionError):
        load_task("heldout-r2-value")


def test_trace_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "abcdefghijklmnop1234567890")
    ctl = make(tmp_path, [{"reasoning": "token sk-ant-abcdef1234567890 and abcdefghijklmnop1234567890", "action": {"tool": "get_design", "args": {}}}], Limits(max_model_calls=1))
    ctl.run()
    text = (tmp_path / "run/trace.jsonl").read_text()
    assert "sk-ant-abcdef" not in text and "abcdefghijklmnop1234567890" not in text and "[REDACTED]" in text

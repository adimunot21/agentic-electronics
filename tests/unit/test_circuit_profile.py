"""Circuit-profile seam (docs/second-circuit/harness-seam.md): FE-AAF-001 stays byte-identical, a toy second circuit is added purely by data (tmp repo, fake pipeline/checks, no simulator)."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ae.harness.circuit import FE_AAF_001, ProfileError, load_profile
from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.improvement import PolicyError, decide_improvement, load_policy
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import Task, load_task
from ae.harness.workspace import REPO_ROOT, TRUSTED_GLOBS, trusted_digest

from tests.unit.test_improvement import A

OLD_GLOBS = ["requirements/fe-aaf-001.yaml", "src/ae/**/*.py", "tools_kicad/*.py", "analysis/derive_filter.py", "evidence/pins/*.yaml",
             "designs/fe-aaf-001/placement.yaml", "designs/fe-aaf-001/fe_aaf_001.kicad_pro", "models/shim-record.json", "models/cache/OPA2314/opa2314_ngspice_shim.lib",
             "src/ae/harness/tasks/*.yaml", "evidence/bom-verification.yaml"]


def old_digest(repo: Path) -> dict:
    files = {}
    for pattern in OLD_GLOBS:
        for path in sorted(repo.glob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                files[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"combined": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(), "n_files": len(files), "files": files}


# ---------------------------------------------------------------- FE regression pins
def test_fe_profile_literals_are_pinned():
    p = FE_AAF_001
    assert (p.id, p.requirements_path, p.baseline_design, p.corner_suffix) == ("fe-aaf-001", "requirements/fe-aaf-001.yaml", "designs/fe-aaf-001/components.yaml", "-CORNERS")
    assert p.corner_check_ids == ("REQ-ALIAS-CORNERS", "REQ-FLAT-CORNERS", "REQ-GAIN-CORNERS") and p.nominal_objective_check == "REQ-ALIAS" and p.stability_check_id == "REQ-STAB"
    assert p.guard_traded_ids == ("REQ-ALIAS", "REQ-GAIN", "REQ-FLAT") and p.checks == "ae.harness.checks:run_fast" and p.pipeline is None
    assert p.brief_keys == ("REQ-FLAT-CORNERS", "REQ-GAIN-CORNERS", "REQ-FLAT", "REQ-GAIN", "REQ-ALIAS", "REQ-STAB", "REQ-BIAS", "REQ-IQ", "REQ-ZIN", "REQ-HEADROOM", "REQ-SETTLE", "REQ-NOISE")
    assert (p.evidence_ledger, p.derivation_record, p.extra_trusted_globs, p.edit_sanity) == ("evidence/bom-verification.yaml", "analysis/filter_design_0002.json", (), True)
    assert [c["claim"] for c in p.standing_claims] == ["MODEL_STARTUP/SATURATION/OVERLOAD_RECOVERY", "REQ-STAB"]


def test_fe_trusted_digest_is_unchanged_with_and_without_a_profile():
    assert TRUSTED_GLOBS == OLD_GLOBS
    assert trusted_digest()["combined"] == old_digest(REPO_ROOT)["combined"]
    assert trusted_digest(REPO_ROOT, ())["combined"] == trusted_digest()["combined"]
    assert TrustedEvaluator(profile=FE_AAF_001).digest_at_start["combined"] == TrustedEvaluator().digest_at_start["combined"] == old_digest(REPO_ROOT)["combined"]


def test_frozen_fe_policies_resolve_to_the_fe_profile():
    for tid in ("improve-stage2-alias", "improve-wide-retune-alias-v3"):
        try:
            task = load_task(tid)
        except KeyError:
            continue
        assert load_policy(Path(task.policy_path)).profile is FE_AAF_001


# ---------------------------------------------------------------- toy second circuit (all data in tmp_path)
def toy_checks(names, manifest, workdir):
    return {n: {"outcome": "passed"} for n in names}


def toy_pipeline(components_path, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    return {"checks": [], "summary": "toy", "inputs_sha256": {"components": hashlib.sha256(Path(components_path).read_bytes()).hexdigest()}}


PROFILE_YAML = {"id": "toy", "requirements_path": "requirements/toy.yaml", "baseline_design": "designs/toy/components.yaml", "corner_suffix": "-CORNERS",
                "corner_check_ids": ["REQ-X-CORNERS"], "nominal_objective_check": "REQ-X", "stability_check_id": "REQ-OSC", "guard_traded_ids": ["REQ-X"],
                "standing_claims": [{"claim": "TOY_CLAIM", "why": "toy standing claim"}], "brief_keys": ["REQ-X", "REQ-OSC"], "labels": {"reference_path": "designs/toy/components.yaml", "improvement_readme_task": "toy-task", "improvement_readme_caveats": "TOY caveats. Blocked claims: "},
                "checks": "tests.unit.test_circuit_profile:toy_checks", "pipeline": "tests.unit.test_circuit_profile:toy_pipeline", "extra_trusted_globs": ["designs/toy/*.yaml", "requirements/toy.yaml"], "edit_sanity": False}


class Toy:
    def __init__(self, root: Path, profile_overrides: dict | None = None, policy_overrides: dict | None = None):
        self.repo = root / "repo"
        for d in ("requirements", "designs/toy", "src/ae/harness/circuits", "src/ae/harness/tasks"):
            (self.repo / d).mkdir(parents=True)
        (self.repo / "requirements/toy.yaml").write_text(yaml.safe_dump({"requirements": [{"id": "REQ-X", "check": {"max": -40.0}}, {"id": "REQ-OSC", "check": {"max": 5.0}}]}))
        (self.repo / "designs/toy/components.yaml").write_text(yaml.safe_dump({"parts": {"R1": {"role": "toy", "value": "1k"}}}))
        (self.repo / "src/ae/harness/circuits/toy.yaml").write_text(yaml.safe_dump({**PROFILE_YAML, **(profile_overrides or {})}))
        sha = lambda rel: hashlib.sha256((self.repo / rel).read_bytes()).hexdigest()
        self.policy_dict = {"id": "toy-improve", "policy_version": 1, "kind": "improvement", "circuit": "toy",
                            "baseline": {"design": "designs/toy/components.yaml", "components_sha256": sha("designs/toy/components.yaml"), "requirements_sha256": sha("requirements/toy.yaml")},
                            "objective": {"check": "REQ-X-CORNERS", "min_improvement": 1.0, "baseline_value_expected_dB": -50.0, "unit": "dB"}, "jitter": {"rel": 0.0, "abs": 0.01},
                            "hard_constraints": [{"id": "H1", "check": "REQ-X-CORNERS", "contract_limit": -40.0, "max": -42.0, "reserve": 2.0}],
                            "nonregression": {"generic_margin_fraction": 0.1, "not_tested_in_baseline": [], "unchanged_status_required": []}, "traded_metrics": ["REQ-X-CORNERS"],
                            "allowed_edits": {"parts": ["R1"], "fields": ["value"], "max_ops": 2}, "claims_never_made": ["none"], **(policy_overrides or {})}
        self.policy_path = self.repo / "src/ae/harness/tasks/toy.yaml"
        self.policy_path.write_text(yaml.safe_dump(self.policy_dict))

    def policy(self):
        return load_policy(self.policy_path, self.repo)


def record(x_corner=-50.0, osc=6.0, osc_outcome="failed", corner_outcome="passed"):
    return {"checks": [{"id": "REQ-X-CORNERS", "outcome": corner_outcome, "value": x_corner, "limit": "<= -40"}, {"id": "REQ-X", "outcome": "passed", "value": x_corner, "limit": "<= -40"},
                       {"id": "REQ-OSC", "outcome": osc_outcome, "value": osc, "limit": "<= 5"}], "summary": "toy", "inputs_sha256": {"components": "x"}}


def run_dirs(tmp_path: Path) -> tuple[Path, Path]:
    for name in ("b", "c"):
        (tmp_path / name).mkdir(exist_ok=True)
        (tmp_path / name / "corners_summary.json").write_text(json.dumps({"gain_min": 1.0, "gain_max": 1.0}))
    return tmp_path / "b", tmp_path / "c"


def test_toy_policy_loads_with_its_profile_and_fe_default_is_untouched(tmp_path):
    toy = Toy(tmp_path)
    pol = toy.policy()
    assert pol.profile.id == "toy" and pol.profile.edit_sanity is False and pol.profile.stability_check_id == "REQ-OSC"
    assert pol.profile.extra_trusted_globs[-1] == "src/ae/harness/circuits/toy.yaml"          # the profile file itself is trusted
    no_key = {k: v for k, v in toy.policy_dict.items() if k != "circuit"}
    assert load_profile("fe-aaf-001") is FE_AAF_001 and "circuit" not in no_key


def test_requirements_hash_mismatch_is_refused(tmp_path):
    toy = Toy(tmp_path)
    (toy.repo / "requirements/toy.yaml").write_text(yaml.safe_dump({"requirements": [{"id": "REQ-X", "check": {"max": -41.0}}, {"id": "REQ-OSC", "check": {"max": 5.0}}]}))
    with pytest.raises(PolicyError, match="baseline_changed"):
        toy.policy()


def test_contract_limit_is_checked_against_the_profiles_requirements(tmp_path):
    toy = Toy(tmp_path, policy_overrides={"hard_constraints": [{"id": "H1", "check": "REQ-X-CORNERS", "contract_limit": -39.0, "max": -41.0, "reserve": 2.0}]})
    with pytest.raises(PolicyError, match="contract_limit"):
        toy.policy()


@pytest.mark.parametrize("override,message", [({"surprise": 1}, "unknown key"), ({"checks": "nope.module:fn"}, "not importable"), ({"pipeline": "tests.unit.test_circuit_profile:missing"}, "not importable"),
                                              ({"checks": "no_colon"}, "module:function"), ({"corner_check_ids": "REQ-X-CORNERS"}, "must be a list")])
def test_bad_profile_data_is_a_policy_error(tmp_path, override, message):
    with pytest.raises(PolicyError, match=message):
        Toy(tmp_path, profile_overrides=override).policy()


def test_unknown_circuit_and_unsafe_ids_are_policy_errors(tmp_path):
    for circuit in ("does-not-exist", "../../etc/passwd"):
        with pytest.raises(PolicyError):
            Toy(tmp_path / circuit.replace("/", "_"), policy_overrides={"circuit": circuit}).policy()


def test_profile_corner_and_stability_ids_drive_decisions(tmp_path):
    toy = Toy(tmp_path)
    pol = toy.policy()
    bdir, cdir = run_dirs(tmp_path)
    manifest = (toy.repo / "designs/toy/components.yaml").read_text()
    base = record()
    # a blocked corner check blocks the verdict only because the PROFILE names it
    blocked = decide_improvement(base, record(-52.0, corner_outcome="blocked"), pol, bdir, cdir, manifest, manifest)
    assert blocked["verdict"] == "blocked" and "corners_incomplete:REQ-X-CORNERS" in blocked["blocked"]["reason"]
    # the profile's stability check regresses -> its special rule fires
    worse = decide_improvement(base, record(-52.0, osc=7.0), pol, bdir, cdir, manifest, manifest)
    assert worse["verdict"] == "rejected" and any(v["rule"] == "REQ-STAB_nonregression" and v["check"] == "REQ-OSC" for v in worse["policy_violations"])
    # same data under the FE profile: neither id means anything, so the same records are accepted / not blocked
    fe_pol = dataclasses.replace(pol, profile=FE_AAF_001)
    assert not any(v["check"] == "REQ-OSC" and v["rule"] == "REQ-STAB_nonregression" for v in decide_improvement(base, record(-52.0, osc=7.0), fe_pol, bdir, cdir, manifest, manifest)["policy_violations"])
    assert decide_improvement(base, record(-52.0, corner_outcome="blocked"), fe_pol, bdir, cdir, manifest, manifest)["verdict"] != "blocked"
    ok = decide_improvement(base, record(-52.0), pol, bdir, cdir, manifest, manifest)
    assert ok["verdict"] == "accepted_for_objective" and [c["claim"] for c in ok["blocked_claims"]] == ["TOY_CLAIM"]


def test_extra_trusted_globs_change_the_digest_only_for_that_circuit(tmp_path):
    toy = Toy(tmp_path)
    pol = toy.policy()
    plain = TrustedEvaluator(repo=toy.repo)
    with_profile = TrustedEvaluator(repo=toy.repo, profile=pol.profile)
    fe = TrustedEvaluator(repo=toy.repo, profile=FE_AAF_001)
    assert fe.digest_at_start["combined"] == plain.digest_at_start["combined"]
    assert with_profile.digest_at_start["combined"] != plain.digest_at_start["combined"]
    assert {"designs/toy/components.yaml", "requirements/toy.yaml", "src/ae/harness/circuits/toy.yaml"} <= set(with_profile.digest_at_start["files"])
    (toy.repo / "requirements/toy.yaml").write_text("tampered: true\n")                                  # a change to a trusted toy file is visible only to the toy digest
    assert with_profile._digest()["combined"] != with_profile.digest_at_start["combined"] and plain._digest()["combined"] == plain.digest_at_start["combined"]


def test_evaluator_runs_the_profiles_pipeline(tmp_path):
    toy = Toy(tmp_path)
    ev = TrustedEvaluator(repo=toy.repo, profile=toy.policy().profile)
    rec = ev.run_pipeline(toy.repo / "designs/toy/components.yaml", tmp_path / "out")
    assert rec["summary"] == "toy"


def toy_controller(tmp_path: Path, toy: Toy, evaluator, script, **kw) -> Controller:
    task = Task("toy-improve", "dev", "toy task", {}, "improvement", str(toy.policy_path))
    return Controller(tmp_path / "run", task, ScriptedProvider(script), evaluator, Limits(max_iterations=2, max_model_calls=6), Mode.ENGINEERED_SINGLE, baseline_record=record(),
                      baseline_dir=tmp_path / "base", repo=toy.repo, **kw)


def test_controller_takes_design_checks_brief_and_sanity_hook_from_the_profile(tmp_path):
    toy = Toy(tmp_path)
    pol = toy.policy()
    (tmp_path / "base").mkdir()
    ctl = toy_controller(tmp_path, toy, TrustedEvaluator(repo=toy.repo, profile=pol.profile), [A("get_design"), A("run_check", check="edit_sanity"), A("declare_no_improvement", summary="toy")])
    summary = ctl.run()
    assert summary["stop_reason"] == "no_improvement_declared" and ctl.profile.id == "toy"
    assert ctl.pristine == toy.repo / "designs/toy/components.yaml" and ctl.checks_fn is toy_checks      # defaults resolved from the profile, not run_fast
    brief = json.loads((tmp_path / "run/task_brief.json").read_text())
    assert list(brief["baseline_measurements"]) == ["REQ-X-CORNERS", "REQ-X", "REQ-OSC"]
    tool_events = [json.loads(l) for l in (tmp_path / "run/trace.jsonl").read_text().splitlines() if '"tool_call"' in l]
    sanity = next(e for e in tool_events if e["args"].get("check") == "edit_sanity")
    assert sanity["result"]["ok"] is False and "only in improvement tasks" in json.dumps(sanity["result"])      # hook is off: no model, so None -> refused
    assert not [e for e in (json.loads(l) for l in (tmp_path / "run/trace.jsonl").read_text().splitlines()) if e["type"] == "sanity_advisory"]


def test_controller_refuses_an_evaluator_that_does_not_cover_the_circuit(tmp_path):
    toy = Toy(tmp_path)
    (tmp_path / "base").mkdir()
    ctl = toy_controller(tmp_path, toy, TrustedEvaluator(repo=toy.repo), [A("get_design")])
    summary = ctl.run()
    assert summary["stop_reason"] == "policy_blocked"
    assert "not built for circuit 'toy'" in [json.loads(l) for l in (tmp_path / "run/trace.jsonl").read_text().splitlines() if "policy_blocked" in l][-1]["detail"]

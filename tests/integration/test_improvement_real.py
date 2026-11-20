"""REAL-tool checks for the improvement mode (docker + KiCad + ngspice + shimmed TI model). Fast checks only (~10 s each); the FULL-pipeline decisions for the frozen
candidates are recorded in runs/agent/improve-stage2-alias-fixture/ (search_summary.json) by scripts/improvement_search_fixture.py."""
import shutil

import pytest

from ae.harness.checks import run_fast
from ae.harness.improvement import load_policy
from ae.harness.patch import sha256_text, validate_patch, apply_validated
from ae.harness.workspace import REPO_ROOT

pytestmark = pytest.mark.integration
ready = shutil.which("docker") and (REPO_ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib").exists()
PRISTINE = REPO_ROOT / "designs/fe-aaf-001/components.yaml"
POLICY = load_policy(REPO_ROOT / "src/ae/harness/tasks/improve_stage2_alias.yaml")


def candidate(tmp_path, label):
    text = PRISTINE.read_text()
    ops = [{"op": "set_field", "part": p, "field": f, "to": t} for p, f, t in POLICY.raw["search"]["candidates"][label]["ops"]]
    vp = validate_patch({"summary": "t", "base_sha256": sha256_text(text), "ops": ops}, text, POLICY.scope())
    path = tmp_path / f"{label}.yaml"
    path.write_text(apply_validated(vp, text))
    return path


@pytest.mark.skipif(not ready, reason="needs docker + shimmed model")
def test_real_fast_checks_paired_edit_keeps_bom_blocked_and_value_only_edit_fails(tmp_path):
    base = run_fast(["bom_consistency", "ac_response"], PRISTINE, tmp_path / "base")
    paired = run_fast(["bom_consistency", "ac_response"], candidate(tmp_path, "A_r45_16k9"), tmp_path / "paired")
    assert base["bom_consistency"]["outcome"] == paired["bom_consistency"]["outcome"] == "blocked" and "0 MISMATCH" in paired["bom_consistency"]["detail"]
    assert paired["ac_response"]["measurements"] != base["ac_response"]["measurements"]            # the circuit really changed
    # value-only edit (no MPN edit): the pre-registered X_noop candidate changes text only, so craft the unpaired edit directly
    text = PRISTINE.read_text()
    unpaired = tmp_path / "unpaired.yaml"
    unpaired.write_text(apply_validated(validate_patch({"summary": "t", "base_sha256": sha256_text(text), "ops": [{"op": "set_field", "part": "R4", "field": "value", "to": "17.4k"}]}, text, POLICY.scope()), text))
    bad = run_fast(["bom_consistency"], unpaired, tmp_path / "unpaired_run")
    assert bad["bom_consistency"]["outcome"] == "failed" and "VALUE" in bad["bom_consistency"]["detail"]


@pytest.mark.skipif(not ready, reason="needs docker + shimmed model")
def test_real_noop_text_change_is_numerically_identical(tmp_path):
    base = run_fast(["ac_response"], PRISTINE, tmp_path / "base")
    same = run_fast(["ac_response"], candidate(tmp_path, "X_noop_c3_5n60"), tmp_path / "noop")
    assert same["ac_response"]["measurements"] == base["ac_response"]["measurements"]


@pytest.mark.skipif(not ready, reason="needs docker + shimmed model")
def test_real_corner_preview_reproduces_the_evaluators_baseline_corner_numbers(tmp_path):
    """Real docker/ngspice: the agent-facing corner_preview must give the SAME worst-case numbers the full pipeline records for the baseline (same trusted code, deterministic)."""
    from ae.harness.checks import run_fast
    out = run_fast(["corner_preview"], PRISTINE, tmp_path)["corner_preview"]
    m = out["measurements"]
    assert out["outcome"] == "within_contract_limits" and "not_acceptance" in out and m["corners_completed"] == m["corners_total"] == 1024
    assert m["alias_corners_worst_dB"] == pytest.approx(-58.73526536671747, abs=1e-6)
    assert m["flatness_corners_worst_dB"] == pytest.approx(0.4022427579821821, abs=1e-6)
    assert m["gain_corners_min_V_per_V"] == pytest.approx(1.24338294379962, abs=1e-9) and m["gain_corners_max_V_per_V"] == pytest.approx(1.3636736113526455, abs=1e-9)


@pytest.mark.skipif(not ready, reason="needs docker + shimmed model")
def test_real_requirements_preview_reproduces_the_baseline_nominal_numbers_including_req_stab(tmp_path):
    """Real docker/ngspice: requirements_preview runs ae.sim.evaluate_all, so on the baseline it must equal the full pipeline's recorded nominal values (REQ-STAB = 52.8446 dB, FAILED)."""
    out = run_fast(["requirements_preview"], PRISTINE, tmp_path)["requirements_preview"]
    r = out["requirements"]
    assert out["outcome"] == "info" and "not_acceptance" in out
    assert r["REQ-STAB"]["value"] == pytest.approx(52.844602146847194, abs=1e-6) and r["REQ-STAB"]["outcome"] == "failed"
    assert r["REQ-ALIAS"]["value"] == pytest.approx(-60.33030524960851, abs=1e-6) and r["REQ-BIAS"]["value"] == pytest.approx(9.65, abs=1e-6)

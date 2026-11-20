"""Offline tests for ae.harness.sanity (advisory ideal-op-amp edit check).

Real validation values are hardcoded from runs/agent/improve-live-8 (baseline/corners_summary.json, evaluations/iter_01, iter_02: gain_min, gain_max,
worst_flat.passband_flatness_dB_max_abs, stop_worst). Tests do not read runs/.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
import yaml

from ae.harness.sanity import SanityLimits, edit_sanity, limits_from_policy

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_TEXT = (REPO_ROOT / "designs" / "fe-aaf-001" / "components.yaml").read_text()

# provenance: runs/agent/improve-live-8 real (ngspice, OPA2314 model) 1024-corner results. Edits are value overrides on the baseline manifest.
EVAL1_EDITS = {"R2": "75k", "R3": "6.04k", "R4": "16.2k", "R5": "16.5k", "C1": "20n"}
EVAL2_EDITS = {**EVAL1_EDITS, "R4": "16.5k"}
REAL = {   # name: (edits, gain_min, gain_max, flat_worst_db, alias_worst_db)
    "baseline": ({}, 1.24338294379962, 1.3636736113526455, 0.4022427579821821, -58.73526536671747),
    "eval1": (EVAL1_EDITS, 1.242218096280434, 1.363464325660134, 0.4001053751063799, -59.278229651449706),
    "eval2": (EVAL2_EDITS, 1.2196325813518352, 1.3386743073892298, 0.4001096175351113, -59.437488877441595),
}


def manifest_with(edits: dict[str, str]) -> str:
    """Baseline manifest text with `value` edits applied to parts (yaml round trip; fine since sanity only reads parts)."""
    doc = yaml.safe_load(BASELINE_TEXT)
    for ref, value in edits.items():
        doc["parts"][ref]["value"] = value
    return yaml.safe_dump(doc, sort_keys=False)


def codes(result: dict, severity: str | None = None) -> list[str]:
    return [w["code"] for w in result["warnings"] if severity is None or w["severity"] == severity]


def test_validation_against_real_evaluations() -> None:
    limits = SanityLimits()
    for name, (edits, gain_min, gain_max, flat, alias) in REAL.items():
        predicted = edit_sanity(manifest_with(edits), BASELINE_TEXT)["predicted"]
        assert abs(predicted["corner_gain_min"] - gain_min) <= limits.model_error_gain, name
        assert abs(predicted["corner_gain_max"] - gain_max) <= limits.model_error_gain, name
        assert abs(predicted["corner_flat_worst_db"] - flat) <= limits.model_error_flat_db, name
        assert abs(predicted["corner_alias_worst_db"] - alias) <= limits.model_error_alias_db, name


def test_eval2_flags_gain_min() -> None:
    result = edit_sanity(manifest_with(EVAL2_EDITS), BASELINE_TEXT)
    assert result["status"] == "warn" and result["advisory"] is True
    gain_warning = next(w for w in result["warnings"] if w["code"] == "GAIN_CORNER_MIN")
    assert gain_warning["severity"] == "warn" and "1.2355" in gain_warning["message"] and "raise" in gain_warning["message"] and "R5/R4" in gain_warning["message"]
    assert result["stage2_ratio"]["candidate"] == pytest.approx(1.0)
    assert "STAGE2_RATIO_CHANGED" not in codes(result)      # R4 was reverted to baseline: the ratio itself equals baseline; the low gain comes from R2 75k


def test_stage2_ratio_partial_pair_edit() -> None:
    """eval1 changed R4 only (R5 equals baseline): R5/R4 = 1.0185, +1.85% vs baseline -> info with 'partial pair edit'."""
    result = edit_sanity(manifest_with(EVAL1_EDITS), BASELINE_TEXT)
    warning = next(w for w in result["warnings"] if w["code"] == "STAGE2_RATIO_CHANGED")
    assert warning["severity"] == "info" and "partial pair edit" in warning["message"] and warning["detail"]["changed"] == ["R4"]
    assert result["stage2_ratio"]["change_pct"] == pytest.approx(1.85, abs=0.01)


def test_eval1_has_no_gain_warning() -> None:
    result = edit_sanity(manifest_with(EVAL1_EDITS), BASELINE_TEXT)
    assert not [c for c in codes(result, "warn") if c.startswith("GAIN_")]
    assert result["status"] == "ok"


def test_unchanged_baseline_is_ok() -> None:
    result = edit_sanity(BASELINE_TEXT, BASELINE_TEXT)
    assert result["status"] == "ok" and not codes(result, "warn")
    assert "STAGE2_RATIO_CHANGED" not in codes(result) and "previous_predicted" not in result


def test_partial_revert_vs_previous() -> None:
    result = edit_sanity(manifest_with(EVAL2_EDITS), BASELINE_TEXT, previous_manifest_text=manifest_with(EVAL1_EDITS))
    assert "PARTIAL_REVERT_VS_PREVIOUS" in codes(result) and "previous_predicted" in result
    same = edit_sanity(manifest_with(EVAL2_EDITS), BASELINE_TEXT, previous_manifest_text=manifest_with(EVAL2_EDITS))
    assert "PARTIAL_REVERT_VS_PREVIOUS" not in codes(same)       # nothing changed vs previous


@pytest.mark.parametrize("candidate,baseline", [("", BASELINE_TEXT), ("a: [", BASELINE_TEXT), ("parts: 3", BASELINE_TEXT), (None, BASELINE_TEXT), (BASELINE_TEXT, "x: 1"),
                                                (manifest_with({"R4": "abc"}), BASELINE_TEXT), (manifest_with({"R4": "-5k"}), BASELINE_TEXT)])
def test_malformed_inputs_are_skipped(candidate, baseline) -> None:
    result = edit_sanity(candidate, baseline)
    assert result["status"] == "skipped" and result["advisory"] is True and result["reason"] and result["warnings"] == []


def test_deterministic_and_fast() -> None:
    start = time.time()
    first = edit_sanity(manifest_with(EVAL2_EDITS), BASELINE_TEXT, previous_manifest_text=manifest_with(EVAL1_EDITS))
    assert time.time() - start < 5.0
    assert first == edit_sanity(manifest_with(EVAL2_EDITS), BASELINE_TEXT, previous_manifest_text=manifest_with(EVAL1_EDITS))


def test_module_independence() -> None:
    source = (REPO_ROOT / "src" / "ae" / "harness" / "sanity.py").read_text()
    imported_modules = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, flags=re.M)
    for forbidden in ("improvement", "evaluator", "tools", "checks"):
        assert not [m for m in imported_modules if forbidden in m.split(".")], forbidden


def test_borderline_severity_logic() -> None:
    eval2 = manifest_with(EVAL2_EDITS)            # predicted gain min 1.2192
    beyond = edit_sanity(eval2, BASELINE_TEXT, limits=SanityLimits(gain_min=1.2192 + 0.0001, model_error_gain=0.0005))
    assert next(w for w in beyond["warnings"] if w["code"] == "GAIN_CORNER_MIN")["severity"] == "warn"
    borderline = edit_sanity(eval2, BASELINE_TEXT, limits=SanityLimits(gain_min=1.2192 - 0.0002, model_error_gain=0.0005))
    w = next(w for w in borderline["warnings"] if w["code"] == "GAIN_CORNER_MIN")
    assert w["severity"] == "info" and "borderline" in w["message"] and borderline["status"] == "ok"
    clear = edit_sanity(eval2, BASELINE_TEXT, limits=SanityLimits(gain_min=1.2192 - 0.01, model_error_gain=0.0005))
    assert "GAIN_CORNER_MIN" not in codes(clear)


def test_limits_from_policy_matches_defaults_v4() -> None:
    policy_path = REPO_ROOT / "src" / "ae" / "harness" / "tasks" / "improve_wide_retune_alias_v4.yaml"
    assert limits_from_policy(policy_path) == SanityLimits()
    assert limits_from_policy(yaml.safe_load(policy_path.read_text())) == SanityLimits()


def test_limits_from_policy_missing_constraint() -> None:
    with pytest.raises(ValueError):
        limits_from_policy({"hard_constraints": [{"id": "H1", "check": "REQ-FLAT-CORNERS", "max": 0.48}]})
    with pytest.raises(ValueError):
        limits_from_policy({})


def test_gain_nominal_borderline_band() -> None:
    eval2 = manifest_with(EVAL2_EDITS)           # nominal gain Rs=100 predicted 1.2761
    near = edit_sanity(eval2, BASELINE_TEXT, limits=SanityLimits(gain_min=1.2761 + 0.0002, model_error_gain=0.0005))
    assert next(w for w in near["warnings"] if w["code"] == "GAIN_NOMINAL")["severity"] == "warn"
    near = edit_sanity(eval2, BASELINE_TEXT, limits=SanityLimits(gain_min=1.2761 - 0.0002, model_error_gain=0.0005))
    assert next(w for w in near["warnings"] if w["code"] == "GAIN_NOMINAL")["severity"] == "info"


def test_validation_fixture_real_pipeline() -> None:
    """11 designs (E24 cap / E96 resistor steps, several breaking gain/flatness) measured with the real pipeline (tests/unit/data/sanity_validation.json):
    model errors stay within the SanityLimits defaults, and every real breach of a policy limit is flagged (warn) by the matching code - no false negatives."""
    import json
    data = json.loads((REPO_ROOT / "tests" / "unit" / "data" / "sanity_validation.json").read_text())["designs"]
    limits = SanityLimits()
    tolerance = {"corner_gain_min": limits.model_error_gain, "corner_gain_max": limits.model_error_gain, "nominal_gain_rs100": limits.model_error_gain,
                 "corner_flat_worst_db": limits.model_error_flat_db, "nominal_flat_db_worst_rs": limits.model_error_flat_db,
                 "corner_alias_worst_db": limits.model_error_alias_db, "nominal_alias_db_worst_rs": limits.model_error_alias_db}
    breaches = 0
    for name, entry in data.items():
        result = edit_sanity(manifest_with(entry["edits"]), BASELINE_TEXT)
        for key, tol in tolerance.items():
            assert abs(result["predicted"][key] - entry["real"][key]) <= tol, (name, key)
        real, warned = entry["real"], set(codes(result, "warn"))
        expected = {"GAIN_CORNER_MIN": real["corner_gain_min"] < limits.gain_min, "GAIN_CORNER_MAX": real["corner_gain_max"] > limits.gain_max,
                    "FLAT_CORNERS": real["corner_flat_worst_db"] > limits.flat_max_db, "FLAT_NOMINAL": real["nominal_flat_db_worst_rs"] > limits.flat_max_db,
                    "ALIAS_NOMINAL": real["nominal_alias_db_worst_rs"] > limits.alias_max_db, "ALIAS_CORNERS": real["corner_alias_worst_db"] > limits.alias_max_db}
        measured = {"GAIN_CORNER_MIN": ("corner_gain_min", limits.gain_min, limits.model_error_gain), "GAIN_CORNER_MAX": ("corner_gain_max", limits.gain_max, limits.model_error_gain),
                    "FLAT_CORNERS": ("corner_flat_worst_db", limits.flat_max_db, limits.model_error_flat_db), "FLAT_NOMINAL": ("nominal_flat_db_worst_rs", limits.flat_max_db, limits.model_error_flat_db),
                    "ALIAS_NOMINAL": ("nominal_alias_db_worst_rs", limits.alias_max_db, limits.model_error_alias_db), "ALIAS_CORNERS": ("corner_alias_worst_db", limits.alias_max_db, limits.model_error_alias_db)}
        for code, breached in expected.items():
            if breached:
                breaches += 1
                assert code in warned, (name, code)       # false negative
            else:
                key, limit, error = measured[code]
                if code in warned:                        # a warn without a real breach is only tolerated inside the model-error band (conservative edge call)
                    assert abs(real[key] - limit) <= error, (name, code)
    assert breaches >= 8
    alias_breaches = [n for n, e in data.items() if e["real"]["nominal_alias_db_worst_rs"] > limits.alias_max_db]
    assert len(alias_breaches) >= 4 and all("ALIAS_NOMINAL" in codes(edit_sanity(manifest_with(data[n]["edits"]), BASELINE_TEXT), "warn") for n in alias_breaches)
    # ideal reads the stopband HIGHER than real (conservative) everywhere, including the breach region
    assert all(edit_sanity(manifest_with(e["edits"]), BASELINE_TEXT)["predicted"]["nominal_alias_db_worst_rs"] >= e["real"]["nominal_alias_db_worst_rs"] for e in data.values())


if __name__ == "__main__":
    print(f"defaults: {SanityLimits()}")
    print(f"{'design':9s} {'metric':14s} {'ideal':>10s} {'real':>10s} {'err':>9s}")
    for name, (edits, *real_values) in REAL.items():
        predicted = edit_sanity(manifest_with(edits), BASELINE_TEXT)["predicted"]
        for metric, key, real_value in zip(("gain_min", "gain_max", "flat_db", "alias_db"), ("corner_gain_min", "corner_gain_max", "corner_flat_worst_db", "corner_alias_worst_db"), real_values):
            print(f"{name:9s} {metric:14s} {predicted[key]:10.5f} {real_value:10.5f} {predicted[key] - real_value:+9.5f}")


def test_mpn_only_edit_and_baseline_as_previous_are_quiet():
    """Review follow-up (H-lead): an MPN-only edit has identical values (no OBJECTIVE_NOT_IMPROVED), and previous == baseline raises no partial-revert note."""
    from pathlib import Path
    from ae.harness.sanity import edit_sanity
    text = (Path(__file__).resolve().parents[2] / "designs" / "fe-aaf-001" / "components.yaml").read_text()
    out = edit_sanity(text.replace("RC0603FR-0716K5L", "RC0603FR-0716K5X"), text, previous_manifest_text=text)
    assert out["status"] == "ok" and out["warnings"] == []

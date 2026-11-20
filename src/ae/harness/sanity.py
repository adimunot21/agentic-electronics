"""Advisory "edit sanity" pre-check: predicts, with the IDEAL op-amp cascade, whether a candidate manifest edit will violate a guarded metric.

Why: improve-live-8 evaluation 2 reverted only R4 (16.2k -> 16.5k) after R4/R5 had been changed together; the stage-2 gain (R5/R4) dropped, the corner gain
min fell to 1.2196 < 1.2355 and the candidate was rejected with no preview left. The ideal model (analysis/derive_filter.py::ideal_total_response, ~35 ms for
1024 corners) reproduces the real corner gain window to ~0.0004 V/V, so such an edit can be flagged BEFORE a preview or a ~100 s evaluation.

ADVISORY ONLY: nothing here changes acceptance; the evaluator (real op-amp model, full requirement set) decides. Pure and deterministic; never raises.
Deliberately independent of ae.harness.improvement / evaluator / tools / checks (limits are a dataclass the caller may override).

Frequency grids replicate ae.sim_metrics.ac_metrics inputs: nominal AC = `ac dec 40 10 10Meg` (sim.ac_run), corners = `ac dec 20 10 10Meg` (mc.run_corners),
plus an exact 19 kHz point for the stopband. Flatness = max|20log10|H(f)| - gain@400Hz| over grid points in 50..1000 Hz, with gain@400Hz log-log interpolated;
stopband = max over grid points in 19 kHz..1 MHz and exactly 19 kHz. Corner box = mc.CORNER_SPAN (2^10 vertices, hardcoded below).
KNOWN DIFFERENCE: ngspice grid points are 10^(k/n) from 10 Hz; numpy logspace reproduces them to rounding only.
"""
from __future__ import annotations

import importlib.util
import functools
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# mc.CORNER_SPAN copy (kept local so this module has no dependency on the sim stack). C4 is the coupling cap Cin; Rs = source resistance.
CORNER_SPAN: dict[str, tuple[float, float]] = {
    "R1": (.99, 1.01), "R2": (.99, 1.01), "R3": (.99, 1.01), "R4": (.99, 1.01), "R5": (.99, 1.01),
    "C1": (.95, 1.05), "C2": (.95, 1.05), "C3": (.95, 1.05), "C4": (.6, 1.2), "Rs": (0.01, 100.0)}
RS_NOMINAL_VALUES = (0.01, 100.0)   # REQ-GAIN/FLAT/ALIAS are evaluated at Rs=0.01 and Rs=100 (sim.evaluate_all)
PARTS = ("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3", "C4")
REPO_ROOT = Path(__file__).resolve().parents[3]
STAGE2_CHANGE_PCT = 0.2             # STAGE2_RATIO_CHANGED threshold (% change of R5/R4 vs baseline)
NOT_ACCEPTANCE = "Advisory ideal-op-amp prediction only; it does not change acceptance, the evaluator (real op-amp model) decides."
MODEL_TEXT = ("Ideal op-amp cascade (Rs-C4-MFB(R1,R2,R3,C1,C2)-active 1st order(R4,R5,C3)) over all 1024 tolerance-box corners; measured vs the real-op-amp "
              "evaluator on improve-live-8 (baseline, eval 1, eval 2): corner gain min/max within ~0.0005 V/V, worst flatness within ~0.016 dB (limit band {flat} dB), "
              "worst alias within ~0.05 dB (band {alias} dB; ideal reads ~0.05 dB higher than real). Cannot see REQ-STAB, bias or headroom.")


@dataclass(frozen=True)
class SanityLimits:
    """Pass/fail windows plus the ideal-vs-real model error used for the 'borderline' band.

    gain_min / gain_max: v4 policy H2/H3 corner gain window 1.235..1.365 tightened by a 0.0005 / 0.0005 reserve -> 1.2355 / 1.3645 (policy hard constraint).
    flat_max_db: v4 H1 corner flatness limit 0.5 dB minus a 0.02 dB reserve.  alias_max_db: v4 H6 (REQ-ALIAS) stopband limit -55.8 dB plus 0 reserve.
    model_error_*: measured ideal-vs-real error on the 3 recorded designs (see tests/unit/test_sanity.py validation table), rounded up with margin:
      gain 0.001 V/V (measured max 0.0005), flat 0.02 dB (measured max 0.0155), alias 0.1 dB (measured 0.05: ideal reads that much higher/less negative than real; see the table in tests/unit/test_sanity.py).
    """
    gain_min: float = 1.2355
    gain_max: float = 1.3645
    flat_max_db: float = 0.48
    alias_max_db: float = -55.8
    model_error_gain: float = 0.001
    model_error_flat_db: float = 0.02
    model_error_alias_db: float = 0.1


def limits_from_policy(policy: "str | Path | dict") -> SanityLimits:
    """Build SanityLimits from a policy's hard_constraints (yaml only; no dependency on ae.harness.improvement). Accepts a yaml path or an already-loaded dict.
    Mapping (v4 ids, matched by `check`/min/max fields so renumbering is tolerated): REQ-FLAT-CORNERS max -> flat_max_db; REQ-GAIN-CORNERS min/max -> gain_min/gain_max;
    REQ-ALIAS max -> alias_max_db. model_error_* keep their defaults.
    The FIRST matching hard constraint wins for each field (e.g. alias comes from the first REQ-ALIAS entry with a numeric `max`, H6 in v4). Raises ValueError when a
    needed constraint is missing or the policy has no hard_constraints list: callers MUST catch it (the advisory check should fall back to SanityLimits() or skip)."""
    import yaml
    raw = yaml.safe_load(Path(policy).read_text()) if isinstance(policy, (str, Path)) else policy
    constraints = raw.get("hard_constraints") if isinstance(raw, dict) else None
    if not isinstance(constraints, list):
        raise ValueError("policy has no hard_constraints list")

    def find(check: str, field: str) -> float:
        for item in constraints:
            if isinstance(item, dict) and item.get("check") == check and isinstance(item.get(field), (int, float)):
                return float(item[field])
        raise ValueError(f"policy hard_constraints has no {check} {field}")
    return SanityLimits(gain_min=find("REQ-GAIN-CORNERS", "min"), gain_max=find("REQ-GAIN-CORNERS", "max"),
                        flat_max_db=find("REQ-FLAT-CORNERS", "max"), alias_max_db=find("REQ-ALIAS", "max"))


_IDEAL_MODULE: Any = None


def _ideal_module() -> Any:
    """Lazily load analysis/derive_filter.py once (same mechanism as sim.load_ideal)."""
    global _IDEAL_MODULE
    if _IDEAL_MODULE is None:
        spec = importlib.util.spec_from_file_location("derive_filter_sanity", REPO_ROOT / "analysis" / "derive_filter.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _IDEAL_MODULE = module
    return _IDEAL_MODULE


def _parse_si(text: Any) -> float:
    """Local SI parser (R/C values only; mirrors ae.spice.parse_si). Raises ValueError on garbage."""
    import re
    multipliers = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "meg": 1e6, "g": 1e9}
    match = re.fullmatch(r"([0-9.]+)\s*(meg|[fpnumkg])?", str(text).strip().lower())
    if not match:
        raise ValueError(f"cannot parse component value {text!r}")
    value = float(match.group(1)) * (multipliers[match.group(2)] if match.group(2) else 1.0)
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"non-positive component value {text!r}")
    return value


def parse_values(manifest_text: str) -> dict[str, float]:
    """Manifest YAML text -> {R1..R5, C1..C3, C4: ohms/farads}. Raises ValueError on any problem."""
    import yaml
    try:
        doc = yaml.safe_load(manifest_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML: {str(exc)[:80]}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("parts"), dict):
        raise ValueError("manifest has no 'parts' mapping")
    values: dict[str, float] = {}
    for ref in PARTS:
        part = doc["parts"].get(ref)
        if not isinstance(part, dict) or "value" not in part:
            raise ValueError(f"part {ref} missing or has no 'value'")
        values[ref] = _parse_si(part["value"])
    return values


def _grid(points_per_decade: int) -> np.ndarray:
    return np.logspace(1, 7, 6 * points_per_decade + 1)   # 10 Hz .. 10 MHz inclusive, like ngspice `ac dec n 10 10Meg`


def _metrics(values: dict[str, float], points_per_decade: int, c4_scale: np.ndarray, rs: np.ndarray, scale: dict[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    """Vectorised ac_metrics over n parameter sets. Returns arrays (n,) of gain@400, worst flat dB, stopband max dB."""
    ideal = _ideal_module().ideal_total_response
    n_sets = len(rs)
    params = {k: (values[k] * (scale[k] if scale else np.ones(n_sets)))[:, None] for k in ("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3")}
    params["Cin"] = (values["C4"] * c4_scale)[:, None]
    freq = _grid(points_per_decade)
    rs_col = rs[:, None]
    gain = np.abs(ideal(freq[None, :], params, rs=rs_col))                   # (n, nf)
    gain_db = 20 * np.log10(gain)
    log_freq = np.log10(freq)
    upper = int(np.searchsorted(log_freq, np.log10(400.0)))             # same linear-in-log10(f) interpolation of dB as sim_metrics.interp_db
    weight = (np.log10(400.0) - log_freq[upper - 1]) / (log_freq[upper] - log_freq[upper - 1])
    gain_400_db = gain_db[:, upper - 1] * (1 - weight) + gain_db[:, upper] * weight
    in_band = (freq >= 50) & (freq <= 1000)
    flat = np.max(np.abs(gain_db[:, in_band] - gain_400_db[:, None]), axis=1)
    stop = (freq >= 19_000) & (freq <= 1.0e6)
    g19_db = 20 * np.log10(np.abs(ideal(np.array([19_000.0])[None, :], params, rs=rs_col)))[:, 0]
    stop_db = np.maximum(np.max(gain_db[:, stop], axis=1), g19_db)
    return {"gain": 10 ** (gain_400_db / 20), "flat_db": flat, "stop_db": stop_db}


def predict(values: dict[str, float]) -> dict[str, float]:
    """Cached wrapper: the baseline is re-predicted on every agent query, so memoise on the (sorted) value tuple."""
    return dict(_predict_cached(tuple(sorted(values.items()))))


@functools.lru_cache(maxsize=64)
def _predict_cached(items: tuple) -> tuple:
    return tuple(_predict_uncached(dict(items)).items())


def _predict_uncached(values: dict[str, float]) -> dict[str, float]:
    """Predicted guarded metrics for one design (see module docstring for definitions)."""
    names = list(CORNER_SPAN)
    combos = np.array(list(itertools.product((0, 1), repeat=len(names))))
    span = {n: np.array([CORNER_SPAN[n][c] for c in combos[:, i]]) for i, n in enumerate(names)}
    corner = _metrics(values, 20, span["C4"], span["Rs"], {k: span[k] for k in ("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3")})
    ones = np.ones(len(RS_NOMINAL_VALUES))
    nominal = _metrics(values, 40, ones, np.array(RS_NOMINAL_VALUES), {k: ones for k in ("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3")})
    return {
        "nominal_gain_rs100": float(nominal["gain"][1]),
        "nominal_flat_db_worst_rs": float(np.max(nominal["flat_db"])),
        "nominal_alias_db_worst_rs": float(np.max(nominal["stop_db"])),
        "corner_gain_min": float(np.min(corner["gain"])), "corner_gain_max": float(np.max(corner["gain"])),
        "corner_flat_worst_db": float(np.max(corner["flat_db"])), "corner_alias_worst_db": float(np.max(corner["stop_db"])),
    }


def _skipped(reason: str) -> dict[str, Any]:
    return {"advisory": True, "status": "skipped", "reason": reason, "warnings": []}


def _warning(code: str, severity: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "detail": detail}


def _limit_check(code: str, label: str, predicted: float, limit: float, error: float, upper: bool, unit: str, hint: str) -> dict[str, Any] | None:
    """warn when beyond the limit; info ('borderline') when within the model error band on the passing side."""
    excess = predicted - limit if upper else limit - predicted
    if excess > 0:
        return _warning(code, "warn", f"{label} predicted {predicted:.4f}{unit} is beyond the limit {limit}{unit} (by {excess:.4f}{unit}); {hint}",
                        predicted=predicted, limit=limit, excess=excess)
    if -excess <= error:
        return _warning(code, "info", f"{label} predicted {predicted:.4f}{unit} is borderline vs the limit {limit}{unit} (margin {-excess:.4f}{unit} <= model error {error}{unit}); "
                        f"the ideal model may be off by that much either way; {hint}", predicted=predicted, limit=limit, margin=-excess)
    return None


def edit_sanity(candidate_manifest_text: str, baseline_manifest_text: str, *, previous_manifest_text: str | None = None, limits: SanityLimits = SanityLimits()) -> dict:
    """Advisory ideal-op-amp check of a candidate manifest against the guarded-metric limits. Never raises."""
    try:
        return _edit_sanity(candidate_manifest_text, baseline_manifest_text, previous_manifest_text, limits)
    except Exception as exc:   # advisory tool: must never break the caller
        return _skipped(f"{type(exc).__name__}: {str(exc)[:160]}")


def _edit_sanity(candidate_text: str, baseline_text: str, previous_text: str | None, limits: SanityLimits) -> dict[str, Any]:
    try:
        candidate, baseline = parse_values(candidate_text), parse_values(baseline_text)
        previous = parse_values(previous_text) if previous_text is not None else None
    except ValueError as exc:
        return _skipped(str(exc))
    predicted, baseline_predicted = predict(candidate), predict(baseline)
    previous_predicted = predict(previous) if previous is not None else None
    ratio = {k: v["R5"] / v["R4"] for k, v in (("candidate", candidate), ("baseline", baseline))}
    ratio["change_pct"] = (ratio["candidate"] / ratio["baseline"] - 1) * 100
    warnings: list[dict[str, Any]] = []
    g_lo, g_hi = predicted["corner_gain_min"], predicted["corner_gain_max"]
    e_g = limits.model_error_gain
    if g_lo < limits.gain_min + e_g:
        need = (limits.gain_min / g_lo - 1) * 100
        hint = f"raise the stage gain (R2/R1 or R5/R4) by about {max(need, 0):.1f}% (R5/R4 is {ratio['candidate']:.4f} vs baseline {ratio['baseline']:.4f})"
        w = _limit_check("GAIN_CORNER_MIN", "corner gain min", g_lo, limits.gain_min, e_g, False, " V/V", hint)
        if w:
            warnings.append(w)
    if g_hi > limits.gain_max - e_g:
        need = (1 - limits.gain_max / g_hi) * 100
        hint = f"lower the stage gain (R2/R1 or R5/R4) by about {max(need, 0):.1f}%"
        w = _limit_check("GAIN_CORNER_MAX", "corner gain max", g_hi, limits.gain_max, e_g, True, " V/V", hint)
        if w:
            warnings.append(w)
    nominal_gain = predicted["nominal_gain_rs100"]
    for upper, limit in ((False, limits.gain_min), (True, limits.gain_max)):
        beyond_or_near = nominal_gain < limit + e_g if not upper else nominal_gain > limit - e_g
        if beyond_or_near:
            w = _limit_check("GAIN_NOMINAL", "nominal gain (Rs=100)", nominal_gain, limit, e_g, upper, " V/V",
                             "raise the stage gain (R2/R1 or R5/R4)" if not upper else "lower the stage gain (R2/R1 or R5/R4)")
            if w:
                warnings.append(w)
    w = _limit_check("FLAT_CORNERS", "worst corner passband flatness", predicted["corner_flat_worst_db"], limits.flat_max_db, limits.model_error_flat_db, True, " dB",
                     "re-balance the MFB C1/C2/R3 or stage-2 pole (R5*C3) toward the baseline values")
    if w:
        warnings.append(w)
    w = _limit_check("FLAT_NOMINAL", "nominal passband flatness", predicted["nominal_flat_db_worst_rs"], limits.flat_max_db, limits.model_error_flat_db, True, " dB",
                     "re-balance the filter corner / Q toward the baseline values")
    if w:
        warnings.append(w)
    w = _limit_check("ALIAS_CORNERS", "worst corner stopband (19 kHz..1 MHz)", predicted["corner_alias_worst_db"], limits.alias_max_db, limits.model_error_alias_db, True, " dB",
                     "lower the filter corner frequency (larger R*C, especially C3 / R5) to gain stopband attenuation across tolerances")
    if w:
        warnings.append(w)
    w = _limit_check("ALIAS_NOMINAL", "nominal stopband (19 kHz..1 MHz)", predicted["nominal_alias_db_worst_rs"], limits.alias_max_db, limits.model_error_alias_db, True, " dB",
                     "lower the filter corner frequency (larger R*C) to gain stopband attenuation")
    if w:
        warnings.append(w)

    changed = {k for k in ("R4", "R5") if candidate[k] != baseline[k]}
    if abs(ratio["change_pct"]) > STAGE2_CHANGE_PCT:
        partial = len(changed) == 1
        sign = "lower" if ratio["change_pct"] < 0 else "higher"
        text = (f"R5/R4 is {ratio['candidate']:.4f} vs baseline {ratio['baseline']:.4f} ({ratio['change_pct']:+.2f}%); stage-2 gain is {sign}, "
                f"so the predicted gain effect is ~{ratio['change_pct']:+.2f}% (corner gain window predicted {g_lo:.4f}..{g_hi:.4f}).")
        if partial:
            text += f" This is a partial pair edit: only {sorted(changed)[0]} differs from baseline while the other equals baseline; edit R4 and R5 together to keep the ratio."
        warnings.append(_warning("STAGE2_RATIO_CHANGED", "info", text, partial_pair_edit=partial, changed=sorted(changed), **{k: ratio[k] for k in ratio}))

    if previous is not None:
        pair_changed = [k for k in ("R4", "R5") if candidate[k] != previous[k]]
        shift_lo, shift_hi = g_lo - previous_predicted["corner_gain_min"], g_hi - previous_predicted["corner_gain_max"]
        if len(pair_changed) == 1 and max(abs(shift_lo), abs(shift_hi)) > e_g:
            severity = "warn" if any(w["code"].startswith("GAIN_CORNER") and w["severity"] == "warn" for w in warnings) else "info"
            warnings.append(_warning("PARTIAL_REVERT_VS_PREVIOUS", severity,
                                     f"Only {pair_changed[0]} of the coupled pair R4/R5 changed vs the previous design; predicted corner gain window moved from "
                                     f"{previous_predicted['corner_gain_min']:.4f}..{previous_predicted['corner_gain_max']:.4f} to {g_lo:.4f}..{g_hi:.4f} "
                                     f"(min {shift_lo:+.4f}, max {shift_hi:+.4f} V/V) because R5/R4 changed; change R4 and R5 together.",
                                     changed=pair_changed, shift_min=shift_lo, shift_max=shift_hi))
    alias_delta = predicted["corner_alias_worst_db"] - baseline_predicted["corner_alias_worst_db"]
    if alias_delta >= 0 and candidate != baseline:
        warnings.append(_warning("OBJECTIVE_NOT_IMPROVED", "info",
                                 f"Estimated corner alias worst change vs baseline is {alias_delta:+.2f} dB (ideal(candidate) - ideal(baseline); >= 0 means no improvement predicted). "
                                 "This is an estimate; the objective is judged by the evaluator.", alias_delta_db=alias_delta))
    result: dict[str, Any] = {"advisory": True, "status": "warn" if any(w["severity"] == "warn" for w in warnings) else "ok", "not_acceptance": NOT_ACCEPTANCE,
                              "model": MODEL_TEXT.format(flat=limits.model_error_flat_db, alias=limits.model_error_alias_db),
                              "predicted": predicted, "baseline_predicted": baseline_predicted}
    if previous_predicted is not None:
        result["previous_predicted"] = previous_predicted
    result["stage2_ratio"] = ratio
    result["warnings"] = warnings
    return result


if __name__ == "__main__":
    import time
    import yaml
    text = (REPO_ROOT / "designs" / "fe-aaf-001" / "components.yaml").read_text()
    t0 = time.time()
    out = edit_sanity(text, text)
    print(f"defaults: {SanityLimits()}")
    print(f"status={out['status']} elapsed={time.time() - t0:.2f}s")
    for key, val in out["predicted"].items():
        print(f"  {key:28s} {val:.5f}")
    print("warnings:", [w["code"] for w in out["warnings"]])

"""Pure analysis of ngspice raw output -> numbers for the requirements (no tools, unit-testable)."""
from __future__ import annotations

import numpy as np

IDEAL_FLOOR_DB = -90.0   # requirements/fe-aaf-001.yaml REQ-STAB (rev 3)


def read_wrdata(path, n_vectors: int, complex_data: bool) -> tuple[np.ndarray, list[np.ndarray]]:
    """ngspice `wrdata` layout: for each vector: [x, re(, im)] repeated. Returns (x, [vectors])."""
    raw = np.atleast_2d(np.loadtxt(path))
    stride = 3 if complex_data else 2
    if raw.shape[1] != stride * n_vectors:
        raise ValueError(f"{path}: {raw.shape[1]} columns, expected {stride * n_vectors}")
    x = raw[:, 0]
    vectors = []
    for i in range(n_vectors):
        base = i * stride
        vectors.append(raw[:, base + 1] + 1j * raw[:, base + 2] if complex_data else raw[:, base + 1])
    return x, vectors


def interp_db(freq: np.ndarray, gain_mag: np.ndarray, at_hz: float) -> float:
    return float(np.interp(np.log10(at_hz), np.log10(freq), 20 * np.log10(gain_mag)))


def ac_metrics(freq, v_src, v_ain, v_aout, rs_ohm: float, ideal_mag: np.ndarray | None = None, ratio_19k: complex | None = None) -> dict:
    """ratio_19k: v(aout)/v(src) from a dedicated single-point AC run AT EXACTLY 19 kHz. The sweep grid has no 19 kHz point (18.84k, 19.95k), and the
    response falls ~60 dB/decade there, so evaluating only grid points >= 19 kHz flatters the alias result by ~1.3 dB."""
    gain = np.abs(v_aout / v_src)
    g400_db = interp_db(freq, gain, 400.0)
    in_band = (freq >= 50) & (freq <= 1000)
    flat = 20 * np.log10(gain[in_band]) - g400_db
    stop = (freq >= 19_000) & (freq <= 1.0e6)
    i_in = (v_src - v_ain) / rs_ohm
    zin = np.abs(v_ain / i_in) if rs_ohm > 0 else None
    out = {
        "gain_400hz_V_per_V": 10 ** (g400_db / 20),
        "passband_flatness_dB_max_abs": float(np.max(np.abs(flat))),
        "stopband_abs_gain_dB_max_19k_to_1M": float(max(np.max(20 * np.log10(gain[stop])), 20 * np.log10(abs(ratio_19k)) if ratio_19k is not None else -1e9)),
        "gain_at_exactly_19kHz_dB": (float(20 * np.log10(abs(ratio_19k))) if ratio_19k is not None else None),
        "zin_min_ohm": float(np.min(zin[in_band])) if zin is not None else None,
    }
    if ideal_mag is not None:
        excess = 20 * np.log10(gain) - np.maximum(20 * np.log10(ideal_mag), IDEAL_FLOOR_DB)   # contract rev 3: floor at -90 dB
        out["peaking_above_ideal_dB_max"] = float(np.max(excess))                                   # rev 3 (PROPOSED): ideal floored at -90 dB
        out["peaking_above_ideal_dB_max_unfloored"] = float(np.max(20 * np.log10(gain) - 20 * np.log10(ideal_mag)))   # rev 2 (IN FORCE)
        out["peaking_at_hz"] = float(freq[int(np.argmax(excess))])
        out["gain_at_peak_dB"] = float(20 * np.log10(gain[int(np.argmax(excess))]))
        out["ideal_at_peak_dB"] = float(max(20 * np.log10(ideal_mag[int(np.argmax(excess))]), IDEAL_FLOOR_DB))
    return out


def tran_metrics(t, v_aout, settle_fraction: float = 0.6) -> dict:
    keep = t >= t[0] + settle_fraction * (t[-1] - t[0])
    return {"output_min_V": float(np.min(v_aout[keep])), "output_max_V": float(np.max(v_aout[keep]))}


def acquisition_error_mv(t_loaded, v_cs, t_ref, v_aout_ref, t_end: float) -> float:
    """Sample-cap voltage at t_end minus the undisturbed output at t_end, in mV."""
    return float((np.interp(t_end, t_loaded, v_cs) - np.interp(t_end, t_ref, v_aout_ref)) * 1e3)


def open_loop_margins(freq, a_open) -> dict:
    """Unity-gain frequency and phase margin from a complex open-loop gain sweep."""
    mag = np.abs(a_open)
    idx = np.where((mag[:-1] >= 1) & (mag[1:] < 1))[0]
    if len(idx) == 0:
        return {"gbw_hz": None, "phase_margin_deg": None}
    i = idx[0]
    frac = (0 - np.log10(mag[i])) / (np.log10(mag[i + 1]) - np.log10(mag[i]))
    f_unity = 10 ** (np.log10(freq[i]) + frac * (np.log10(freq[i + 1]) - np.log10(freq[i])))
    phase = np.unwrap(np.angle(a_open, deg=False)) * 180 / np.pi
    ph = float(np.interp(np.log10(f_unity), np.log10(freq), phase))
    return {"gbw_hz": float(f_unity), "phase_margin_deg": float(180 + ph)}   # A(s) with non-inverting sign convention


if __name__ == "__main__":
    f = np.geomspace(10, 1e7, 400)
    a = 1e5 / (1 + 1j * f / 30.0)                         # single-pole op-amp, GBW = 3 MHz
    m = open_loop_margins(f, a)
    assert abs(m["gbw_hz"] - 3e6) / 3e6 < 0.02 and 85 < m["phase_margin_deg"] < 95, m
    print("sim_metrics ok:", m)

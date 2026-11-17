import numpy as np

from ae import sim_metrics as sm


def test_open_loop_margins_single_pole():
    f = np.geomspace(10, 1e7, 400)
    m = sm.open_loop_margins(f, 1e5 / (1 + 1j * f / 30.0))
    assert abs(m["gbw_hz"] - 3e6) / 3e6 < 0.02 and 85 < m["phase_margin_deg"] < 95


def test_real_resonance_is_flagged_but_numerical_noise_floor_is_not():
    f = np.geomspace(10, 1e7, 300)
    ideal = 1.0 / (1 + (f / 1600.0) ** 6) ** 0.5                       # monotone 3rd-order Butterworth magnitude
    ones = np.ones_like(f)
    clean = sm.ac_metrics(f, ones, 0 * ones, ideal * 1.0, rs_ohm=100.0, ideal_mag=ideal)
    assert clean["peaking_above_ideal_dB_max"] < 0.1
    noise_floor = np.maximum(ideal, 1e-9)                              # sim sits at -180 dB where ideal is -225 dB: not peaking
    assert sm.ac_metrics(f, ones, 0 * ones, noise_floor, 100.0, ideal)["peaking_above_ideal_dB_max"] < 0.1
    peaked = np.maximum(ideal, 1e-9) + 10 ** (-50 / 20) * np.exp(-((np.log10(f) - np.log10(2e6)) ** 2) / 0.01)   # a -50 dB resonance at 2 MHz
    got = sm.ac_metrics(f, ones, 0 * ones, peaked, 100.0, ideal)
    assert got["peaking_above_ideal_dB_max"] > 3.0 and 1e6 < got["peaking_at_hz"] < 4e6


def test_acquisition_error_sign_and_units():
    t = np.linspace(0, 2e-6, 201)
    ref = np.full_like(t, 2.9)
    loaded = 2.9 - 0.001 * np.exp(-t / 0.3e-6)
    assert abs(sm.acquisition_error_mv(t, loaded, t, ref, 1e-6) - (-1000 * np.exp(-1e-6 / 0.3e-6) * 1e-3)) < 1e-6

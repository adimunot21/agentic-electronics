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


def test_alias_is_evaluated_at_exactly_19khz_not_only_on_grid_points():
    f = 10 * 10 ** (np.arange(0, 261) / 40)                       # the suite's sweep grid: 18.84 kHz, 19.95 kHz, no 19 kHz point
    mag = (1.0 + (f / 1600.0) ** 6) ** -0.5                       # monotone 3rd-order roll-off
    ones = np.ones_like(f)
    grid_only = sm.ac_metrics(f, ones, 0 * ones, mag, 100.0)["stopband_abs_gain_dB_max_19k_to_1M"]
    exact = (1.0 + (19000 / 1600.0) ** 6) ** -0.5
    with_exact = sm.ac_metrics(f, ones, 0 * ones, mag, 100.0, ratio_19k=exact)
    assert with_exact["stopband_abs_gain_dB_max_19k_to_1M"] > grid_only + 1.0        # grid-only flatters by > 1 dB
    assert abs(with_exact["gain_at_exactly_19kHz_dB"] - 20 * np.log10(exact)) < 1e-9


def test_read_wrdata_accepts_a_single_row_file(tmp_path):
    p = tmp_path / "one.csv"
    p.write_text(" 1.9e4 1.0 0.0 1.9e4 2.0 0.5\n")
    x, (a, b) = sm.read_wrdata(p, 2, True)
    assert x.shape == (1,) and a[0] == 1.0 and b[0] == 2.0 + 0.5j

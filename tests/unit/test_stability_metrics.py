"""Pure-metric tests for the stability benches, against analytic cases. No tools needed."""
import numpy as np
import pytest

from ae import stability as st


def test_loop_margins_two_pole_loop_matches_analytic_phase_margin():
    f = np.geomspace(1, 1e8, 4000)
    s = 2j * np.pi * f
    T = 1e4 / ((1 + s / (2 * np.pi * 100)) * (1 + s / (2 * np.pi * 2e6)))     # crossover ~1 MHz, second pole 2 MHz
    m = st.loop_margins(f, T)
    fc = m["crossover_hz"]
    expected_pm = 180 - np.degrees(np.arctan(fc / 100)) - np.degrees(np.arctan(fc / 2e6))
    assert abs(m["phase_margin_deg"] - expected_pm) < 0.05 and 8e5 < fc < 1.1e6


def test_loop_margins_sign_convention_and_gain_margin():
    f = np.geomspace(1, 1e8, 4000)
    s = 2j * np.pi * f
    T = 1e3 / (1 + s / (2 * np.pi * 10)) ** 3                                  # triple pole: phase crosses -180, GM defined
    m = st.loop_margins(f, T)
    assert m["gain_margin_dB"] is not None and m["phase_cross_hz"] > 0
    flipped = st.loop_margins(f, -T)                                           # a sign-flipped T must give the same answer
    assert flipped["phase_margin_deg"] == pytest.approx(m["phase_margin_deg"], abs=1e-6)


def test_step_metrics_detects_added_ringing_vs_ideal():
    t = np.linspace(0, 4e-3, 8000)
    t_step = 100e-6
    ideal = np.where(t > t_step, 0.26 * (1 - np.exp(-(t - t_step) / 150e-6)), 0.0)
    zeta, wn = 0.2, 2 * np.pi * 200e3
    ring = np.where(t > t_step, 0.26 * 0.0, 0.0)
    ringing = ideal + np.where(t > t_step, 0.01 * np.exp(-zeta * wn * (t - t_step)) * np.sin(wn * np.sqrt(1 - zeta ** 2) * (t - t_step)), 0.0)
    clean = st.step_metrics(t, ideal, ideal, t_step, 4e-3)
    bad = st.step_metrics(t, ringing, ideal, t_step, 4e-3)
    assert clean["max_abs_diff_mV"] < 1e-9 and bad["max_abs_diff_mV"] > 2.0


def test_load_step_metrics_first_order_vs_underdamped():
    t = np.linspace(0, 100e-6, 20001)
    on, off = 20e-6, 60e-6
    static = -0.024
    first = np.where(t > on, static * (1 - np.exp(-(t - on) / 150e-9)), 0.0)
    zeta, wn = 0.15, 2 * np.pi * 2e6
    under = np.where(t > on, static * (1 - np.exp(-zeta * wn * (t - on)) * (np.cos(wn * np.sqrt(1 - zeta ** 2) * (t - on)) + zeta / np.sqrt(1 - zeta ** 2) * np.sin(wn * np.sqrt(1 - zeta ** 2) * (t - on)))), 0.0)
    m1, m2 = st.load_step_metrics(t, 1.65 + first, on, off), st.load_step_metrics(t, 1.65 + under, on, off)
    assert m1["static_droop_mV"] == pytest.approx(-24.0, abs=0.05) and m1["overshoot_beyond_static_mV"] < 0.05 and m1["sign_changes_outside_band"] == 0
    assert m2["overshoot_beyond_static_mV"] > 5.0 and m2["sign_changes_outside_band"] >= 2


def test_inject_series_moves_only_the_named_elements_pin():
    deck = "R3 X N_INV 6.19k\nC2 N_INV N_S1 1n\nXU1A VMID N_INV P5V 0 N_S1 OPA2314\n"
    out = st.inject_series(deck, "XU1A", "N_INV", "t").splitlines()
    assert out[0] == "R3 X N_INV 6.19k" and out[1] == "C2 N_INV N_S1 1n"          # the network stays on the node
    assert out[2] == "XU1A VMID N_INVA P5V 0 N_S1 OPA2314"                          # only the amplifier pin moves
    assert out[-1] == "Vinj_t N_INVA N_INV DC 0 AC 1"
    with pytest.raises(ValueError):
        st.inject_series(deck, "XU1A", "N_S9", "t")
    with pytest.raises(ValueError):
        st.inject_series(deck, "XU9", "N_INV", "t")


def test_ideal_opamp_deck_replaces_instances_with_vcvs():
    out = st.ideal_opamp_deck("XU1A VMID N_INV P5V 0 N_S1 OPA2314\nR1 A B 1k\n").splitlines()
    assert out[0] == "EU1A N_S1 0 VMID N_INV 1e6" and out[1] == "R1 A B 1k"


def test_regression_phase_near_minus_90_at_first_point_is_not_sign_flipped():
    """BUG 4 (2026-11-17): `if real(T[0]) < 0: T = -T` flipped a loop whose phase is -90 deg at the first sweep point (dominant pole near 1.5 Hz,
    tiny negative real part), adding 180 deg and reporting a 250 deg phase margin for stage 1. The sign rule may only fire near +-180 deg."""
    f = np.geomspace(10, 1e8, 3000)
    T = 5e4 / (1j * f) / (1 + 1j * f / 1e6)                       # integrator-like: phase -90 deg, real part slightly negative at f = 10 Hz
    assert T[0].real < 0 and abs(np.degrees(np.angle(T[0])) + 90) < 1
    m = st.loop_margins(f, T)
    expected = 180 - 90 - np.degrees(np.arctan(m["crossover_hz"] / 1e6))
    assert m["phase_margin_deg"] == pytest.approx(expected, abs=0.1) and m["phase_margin_deg"] < 90
    assert m["phase_margin_deg"] < 180        # a phase margin above 180 deg would be the flipped-sign signature

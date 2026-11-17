"""Unit tests for the pure parts of analysis/derive_filter.py (no tools, no docker)."""
import importlib.util
from pathlib import Path

import numpy as np

_SPEC = importlib.util.spec_from_file_location("derive_filter", Path(__file__).resolve().parents[2] / "analysis" / "derive_filter.py")
df = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(df)


def test_e96_series_is_official_and_snapping_is_nearest():
    assert len(df.E96) == 96 and df.E96[0] == 100 and df.E96[-1] == 976
    neighbours = df.nearest_e96_neighbours(10_000.0, 1)
    assert 10_000.0 in [round(v, 3) for v in neighbours]
    assert all(9_000 < v < 11_000 for v in neighbours)


def test_mfb_design_hits_target_f0_q_and_gain():
    fc, gain, q, c1, c2 = 1600.0, 1.3, 1.0, 18e-9, 1e-9
    r1, r2, r3 = df.design_mfb_ideal(fc, gain, q, c1, c2)
    assert abs(r2 / r1 - gain) < 1e-9                       # gain = R2/R1
    w0 = 1 / np.sqrt(r2 * r3 * c1 * c2)
    assert abs(w0 / (2 * np.pi) - fc) / fc < 1e-9           # natural frequency
    q_actual = 1 / (w0 * c2 * (r2 + r3 + r2 * r3 / r1))     # damping term multiplies C2 (feedback cap)
    assert abs(q_actual - q) < 1e-9


def test_mfb_rejects_cap_ratio_that_cannot_reach_q():
    # needs C1/C2 >= 4 Q^2 (1+K) = 9.2; ratio 2 must be rejected, not silently approximated
    assert df.design_mfb_ideal(1600.0, 1.3, 1.0, 2e-9, 1e-9) is None


def test_symbolic_mfb_matches_closed_form():
    h_sym, syms = df._SYM
    values = dict(zip(syms, (2j * np.pi * 800.0, 20e3, 25e3, 8e3, 15e-9, 1e-9)))
    sym = complex(h_sym.subs(values))
    cf = complex(df.mfb_closed_form(20e3, 25e3, 8e3, 15e-9, 1e-9, values[syms[0]]))
    assert abs(sym - cf) < 1e-9 * abs(cf)


def test_two_pole_filter_cannot_meet_alias_requirement():
    att = df.family_response("butterworth", 2, 0.25, 1000.0, 19000.0)
    assert att < 53.6            # the requirement; this is why order 2 / "gain-2 Butterworth" is rejected
    assert df.family_response("butterworth", 3, 0.25, 1000.0, 19000.0) > 53.6 + 3.0

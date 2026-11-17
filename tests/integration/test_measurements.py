"""Integration regression: the exact-19 kHz alias point comes from a REAL dedicated ngspice run (needs docker + TI model)."""
import shutil
from pathlib import Path

import numpy as np
import pytest

from ae.sim import SimRun, ac_run, load_ideal
from ae.toolchain import REPO_ROOT

pytestmark = pytest.mark.integration
NETLIST = REPO_ROOT / "runs/ref/baseline/fe_aaf_001.net"
ready = shutil.which("docker") and (REPO_ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib").exists() and NETLIST.exists()


@pytest.mark.skipif(not ready, reason="needs docker, shimmed TI model and a baseline netlist")
def test_exact_19khz_point_matches_interpolation_and_is_binding(tmp_path):
    run = SimRun(work=tmp_path / "w", netlist=NETLIST)
    run.prepare()
    m = ac_run(run, load_ideal(NETLIST), 100.0, "rs100")
    from ae import sim_metrics as sm
    f, (vs, va, vo) = sm.read_wrdata(run.work / "ac_rs100.csv", 3, True)
    g = 20 * np.log10(np.abs(vo / vs))
    interp = float(np.interp(np.log10(19000), np.log10(f), g))
    assert abs(m["gain_at_exactly_19kHz_dB"] - interp) < 0.05                    # dedicated run agrees with log-f interpolation
    assert m["stopband_abs_gain_dB_max_19k_to_1M"] == pytest.approx(m["gain_at_exactly_19kHz_dB"])   # 19 kHz is the binding point
    assert m["stopband_abs_gain_dB_max_19k_to_1M"] > g[f >= 19000].max() + 1.0   # the old grid-only value was > 1 dB optimistic


@pytest.mark.skipif(not shutil.which("docker"), reason="needs docker")
def test_loop_injection_reproduces_a_known_loop_gain(tmp_path):
    """Inverting amp, single-pole amplifier A = 1e5/(1+s/2pi30), beta = Rin/(Rin+Rf) = 1/11 -> T0 = 9090.9, crossover ~ 272.7 kHz, PM ~ 90 deg.
    Validates the injection method, sign convention and post-processing on a circuit whose answer is known analytically."""
    from ae import sim_metrics as sm
    from ae.stability import inject_series, loop_margins
    from ae.toolchain import run_in_image
    deck = ("Vs in 0 DC 0\nRin in inn 1k\nRf inn out 10k\nG1 x 0 inn 0 1m\nRx x 0 100Meg\nCx x 0 53.05p\nE1 out 0 x 0 1\n")
    (tmp_path / "t.cir").write_text("* known loop\n" + inject_series(deck, "G1", "inn", "k") + ".control\nop\nac dec 50 10 100Meg\nwrdata t.csv v(inn) v(innA)\n.endc\n.end\n")
    r = run_in_image("known", ["ngspice", "-b", "-o", "t.log", "t.cir"], tmp_path)
    f, (vo, vf) = sm.read_wrdata(tmp_path / "t.csv", 2, True)
    m = loop_margins(f, -vo / vf)      # vo = V(inn) (return), vf = V(innA) (amplifier pin)
    expected_at_10hz = 20 * np.log10(1e5 / 11 / np.sqrt(1 + (f[0] / 30.0) ** 2))      # the pole is already acting at the first sweep point
    assert m["dc_loop_gain_dB"] == pytest.approx(expected_at_10hz, abs=0.05)
    assert m["crossover_hz"] == pytest.approx(1e5 * 30 / 11, rel=0.02)
    assert m["phase_margin_deg"] == pytest.approx(90.0, abs=1.5)

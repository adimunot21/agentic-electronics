"""Regression tests for measurement bugs that were actually hit (2026-11-17) + guards that keep the REQ-STAB failure visible.

BUG 1  alias evaluated at the first sweep point >= 19 kHz (19.95 kHz), flattering the result by ~1.27 dB.
BUG 2  a settling-robustness script scaled R6/C5 relative to pre-patch values while the netlist held the patched ones (bogus 7.77 mV).
BUG 3  REQ-STAB's ideal reference came from design-time JSON values instead of the run's own netlist ("same component values").
GUARD  REQ-STAB rev 2 stays in force and FAILED; rev 3 stays PROPOSED / AC-peaking only; the run record reports both.
"""
import json
import re
from pathlib import Path

import numpy as np
import pytest
import yaml

from ae import sim_metrics as sm
from ae.sim import absolute_scale, load_ideal
from ae.spice import build_dut_deck, parse_si

ROOT = Path(__file__).resolve().parents[2]
NETLIST = ROOT / "runs/ref/baseline/fe_aaf_001.net"
needs_net = pytest.mark.skipif(not NETLIST.exists(), reason="needs runs/ref/baseline netlist")
CONTRACT = yaml.safe_load((ROOT / "requirements/fe-aaf-001.yaml").read_text())


# ---- BUG 1 -------------------------------------------------------------------------------------------------------------------
def test_bug1_sweep_grid_has_no_19khz_point_so_exact_run_is_required():
    f = 10 * 10 ** (np.arange(0, 261) / 40)                      # `ac dec 40 10 10Meg`
    assert not np.any(np.isclose(f, 19000, rtol=1e-3))
    assert f[f < 19000].max() < 19000 < f[f >= 19000].min()
    assert f[f >= 19000].min() == pytest.approx(19952.6, rel=1e-3)   # the point the old check used


def test_bug1_grid_only_alias_is_optimistic_and_exact_value_wins():
    f = 10 * 10 ** (np.arange(0, 261) / 40)
    mag = (1 + (f / 1730.0) ** 6) ** -0.5
    ones = np.ones_like(f)
    grid_only = sm.ac_metrics(f, ones, 0 * ones, mag, 100.0)["stopband_abs_gain_dB_max_19k_to_1M"]
    exact = (1 + (19000 / 1730.0) ** 6) ** -0.5
    m = sm.ac_metrics(f, ones, 0 * ones, mag, 100.0, ratio_19k=exact)
    assert m["stopband_abs_gain_dB_max_19k_to_1M"] - grid_only > 1.0          # ~1.27 dB for a 3rd-order roll-off
    assert m["gain_at_exactly_19kHz_dB"] == pytest.approx(20 * np.log10(exact))


def test_bug1_every_ac_path_requests_the_exact_point():
    for path in ("src/ae/sim.py", "src/ae/mc.py"):
        text = (ROOT / path).read_text()
        assert "ac lin 1 19k 19k" in text and "ratio_19k=" in text, f"{path} must run and use an exact 19 kHz AC point"


# ---- BUG 2 -------------------------------------------------------------------------------------------------------------------
@needs_net
def test_bug2_absolute_scale_is_relative_to_the_netlist_not_to_stale_values():
    held_r6 = {c: parse_si(v) for c, v in re.findall(r'\(ref "(R6)"\)\s*\(value "([^"]+)"\)', NETLIST.read_text())}["R6"]
    assert absolute_scale(NETLIST, {"R6": held_r6}) == {"R6": pytest.approx(1.0)}          # NOT held/100 (the old bug)
    assert absolute_scale(NETLIST, {"R6": 2 * held_r6})["R6"] == pytest.approx(2.0)


@needs_net
def test_bug2_applying_the_scale_reproduces_the_target_value_in_the_deck():
    ev = yaml.safe_load((ROOT / "evidence/pins/OPA2314AID.yaml").read_text())
    deck = build_dut_deck(NETLIST, ev, "m.lib", scale=absolute_scale(NETLIST, {"R6": 330.0, "C5": 220e-12}))
    vals = {l.split()[0]: l.split()[3] for l in deck.splitlines() if l.startswith(("R6 ", "C5 "))}
    assert float(vals["R6"]) == pytest.approx(330.0) and float(vals["C5"]) == pytest.approx(220e-12)


def test_bug2_robustness_script_does_not_hardcode_base_values():
    text = (ROOT / "scripts/robust_settle.py").read_text()
    assert "absolute_scale" in text and "/ 100" not in text and "/ 1000" not in text


# ---- BUG 3 -------------------------------------------------------------------------------------------------------------------
@needs_net
def test_bug3_ideal_reference_follows_the_netlist_values(tmp_path):
    f = np.array([1000.0, 5000.0])
    from_netlist = np.abs(load_ideal(NETLIST)(f, 100.0))
    from_design_json = np.abs(load_ideal(None)(f, 100.0))
    assert not np.allclose(from_netlist, from_design_json, rtol=1e-3), "adopted design differs from the pre-0002 JSON: reference must follow the netlist"
    edited = tmp_path / "edited.net"
    edited.write_text(re.sub(r'(\(ref "R1"\)\s*\(value ")[^"]+', r"\g<1>50k", NETLIST.read_text(), count=1))
    assert not np.allclose(np.abs(load_ideal(edited)(f, 100.0)), from_netlist, rtol=1e-3)


# ---- GUARD: STAB stays failed / rev 3 stays proposed --------------------------------------------------------------------------
def test_guard_contract_keeps_rev2_in_force_and_rev3_unapproved():
    stab = next(r for r in CONTRACT["requirements"] if r["id"] == "REQ-STAB")
    assert stab["check"]["metric"] == "peaking_above_ideal_dB_max_unfloored" and "ideal_floor_dB" not in stab["check"]
    assert stab["proposed_revision_3"]["status"].startswith("PROPOSED - NOT APPROVED")
    assert stab["proposed_revision_3"]["reported_as"] == "REQ-STAB-R3-AC-PEAKING-PROPOSED"
    rev3 = next(r for r in CONTRACT["revisions"] if r["rev"] == 3)
    assert "NOT APPROVED" in rev3["status"] and rev3["made_after_result"] is True


def test_guard_baseline_record_reports_stab_failed_and_rev3_as_ac_peaking_only():
    rec = ROOT / "runs/ref/baseline/record.json"
    if not rec.exists():
        pytest.skip("no baseline record")
    checks = {c["id"]: c for c in json.loads(rec.read_text())["checks"]}
    assert checks["REQ-STAB"]["outcome"] == "failed"
    r3 = checks["REQ-STAB-R3-AC-PEAKING-PROPOSED"]
    assert "AC-PEAKING ONLY" in r3["detail"] and "NOT APPROVED" in r3["detail"] and "does NOT establish stability" in r3["detail"]

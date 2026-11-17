import math
from pathlib import Path

import pytest
import yaml

from ae.spice import build_dut_deck, check_deck_vs_netlist, parse_si

ROOT = Path(__file__).resolve().parents[2]
EV = yaml.safe_load((ROOT / "evidence/pins/OPA2314AID.yaml").read_text())
NETLIST = ROOT / "runs/ref/dev/fe_aaf_001.net"


@pytest.mark.parametrize("text,value", [("64.9k", 64900), ("2.2u", 2.2e-6), ("100", 100), ("5.6n", 5.6e-9), ("10meg", 1e7), ("1n", 1e-9)])
def test_parse_si(text, value):
    assert math.isclose(parse_si(text), value, rel_tol=1e-12)


def test_parse_si_rejects_garbage():
    with pytest.raises(ValueError):
        parse_si("ten ohms")


@pytest.mark.skipif(not NETLIST.exists(), reason="needs a KiCad-exported netlist (run scripts/render_sch.py)")
class TestDeckVsNetlist:
    def test_generated_deck_matches_kicad_connectivity(self):
        deck = build_dut_deck(NETLIST, EV, "m.lib")
        assert check_deck_vs_netlist(deck, NETLIST, EV)[0] == "passed"

    def test_swapped_opamp_inputs_in_deck_are_caught(self):
        deck = build_dut_deck(NETLIST, EV, "m.lib").replace("XU1A VMID N_INV", "XU1A N_INV VMID")
        outcome, detail = check_deck_vs_netlist(deck, NETLIST, EV)
        assert outcome == "failed" and "U1" in detail

    def test_dropped_element_is_caught(self):
        deck = "\n".join(l for l in build_dut_deck(NETLIST, EV, "m.lib").splitlines() if not l.startswith("R3 "))
        assert check_deck_vs_netlist(deck, NETLIST, EV)[0] == "failed"

    def test_scale_applies_numerically_and_offsets_are_tb_only(self):
        deck = build_dut_deck(NETLIST, EV, "m.lib", vos_a=0.0025, scale={"R1": 1.01})
        assert any(l.startswith("R1 N_IN X 65549") for l in deck.splitlines())
        assert "TB_ONLY" in deck and check_deck_vs_netlist(deck, NETLIST, EV)[0] == "passed"

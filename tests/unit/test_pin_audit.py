"""The pin audit must FAIL on the mistakes it exists to catch (negative controls) and pass on the real files."""
from pathlib import Path

import pytest
import yaml

from ae.kicad_sch import SymbolLibrary
from ae.pin_audit import audit_footprint, audit_spice_subckt, audit_symbol

ROOT = Path(__file__).resolve().parents[2]
SYM_DIR = ROOT / "vendor/kicad-libs-10.0.8/symbols"
FP = ROOT / "vendor/kicad-libs-10.0.8/footprints/Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"
needs_libs = pytest.mark.skipif(not SYM_DIR.exists(), reason="KiCad libs not extracted (vendor/kicad-libs-10.0.8)")
REF = yaml.safe_load((ROOT / "evidence/pins/OPA2314AID.yaml").read_text())


class SwappedInputsLib(SymbolLibrary):
    """Symbol whose '+' and '-' pin NUMBERS of unit A are exchanged (a classic copy/paste symbol bug)."""

    def pins(self, lib_id, unit=1):
        pins = super().pins(lib_id, unit)
        if unit == 1:
            numbers = {p.name: p.number for p in pins}
            for p in pins:
                if p.name in "+-" and p.name:
                    p.number = numbers["-" if p.name == "+" else "+"]
        return pins


@needs_libs
def test_real_symbol_and_footprint_pass():
    assert audit_symbol(REF, SymbolLibrary(SYM_DIR), "Amplifier_Operational:LM358").outcome == "passed"
    assert all(r.outcome == "passed" for r in audit_footprint(REF, FP))


@needs_libs
def test_swapped_inverting_inputs_is_caught():
    result = audit_symbol(REF, SwappedInputsLib(SYM_DIR), "Amplifier_Operational:LM358")
    assert result.outcome == "failed" and "pin 2" in result.detail and "pin 3" in result.detail


@needs_libs
def test_swapped_pad_numbers_is_caught(tmp_path):
    text = FP.read_text().replace('(pad "1"', '(pad "TMP"').replace('(pad "4"', '(pad "1"').replace('(pad "TMP"', '(pad "4"')
    bad = tmp_path / "bad.kicad_mod"
    bad.write_text(text)
    results = {r.check: r for r in audit_footprint(REF, bad)}
    assert results["footprint_numbering_and_pin1"].outcome == "failed"
    assert results["footprint_pad_numbers"].outcome == "passed"      # same set of numbers: only order/geometry shows the error


def test_wrong_spice_node_order_is_caught(tmp_path):
    lib = tmp_path / "m.lib"
    lib.write_text(".subckt OPA2314 IN- IN+ VCC VEE OUT\n.ends\n")
    assert audit_spice_subckt(REF, lib).outcome == "failed"


def test_missing_model_is_blocked_not_passed(tmp_path):
    assert audit_spice_subckt(REF, tmp_path / "nope.lib").outcome == "blocked"

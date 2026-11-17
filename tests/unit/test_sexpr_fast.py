from pathlib import Path

import pytest

from ae.sexpr import extract_symbol, extract_symbol_slow

SYM = Path(__file__).resolve().parents[2] / "vendor/kicad-libs-10.0.8/symbols"


@pytest.mark.skipif(not SYM.exists(), reason="KiCad libs not extracted")
@pytest.mark.parametrize("lib,name", [("Device", "R"), ("Device", "C"), ("power", "GND"), ("Amplifier_Operational", "LM358"), ("Connector", "TestPoint")])
def test_fast_extraction_equals_full_parse(lib, name):
    text = (SYM / f"{lib}.kicad_sym").read_text()
    assert extract_symbol(text, name) == extract_symbol_slow(text, name)


def test_missing_symbol_raises():
    with pytest.raises(KeyError):
        extract_symbol('(kicad_symbol_lib\n\t(symbol "A"\n\t)\n)', "B")

"""Tiny probe circuit (NOT the demonstrator): 2-resistor divider with two headers.

Exists only to prove the netlist -> board -> DSN -> route -> SES -> zone -> DRC chain
in the pinned image before building the real design.
"""
from __future__ import annotations

from pathlib import Path

from ae.kicad_sch import Part, SchematicBuilder, SymbolLibrary

LIB_DIR = Path(__file__).resolve().parents[3] / "vendor/kicad-libs-10.0.8/symbols"
FP_R = "Resistor_SMD:R_0603_1608Metric"
FP_J = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"


def build(name: str = "tiny") -> SchematicBuilder:
    b = SchematicBuilder(name, SymbolLibrary(LIB_DIR), "Tiny routing-chain probe (not a design)")
    b.add_part(Part("J1", "Connector_Generic:Conn_01x02", "VIN_IN", FP_J, (50.8, 76.2)))
    b.add_part(Part("R1", "Device:R", "10k", FP_R, (101.6, 76.2)))
    b.add_part(Part("R2", "Device:R", "10k", FP_R, (127.0, 76.2)))
    b.add_part(Part("J2", "Connector_Generic:Conn_01x02", "MID_OUT", FP_J, (177.8, 76.2)))
    for ref, pin, net in [("J1", "1", "VIN"), ("J1", "2", "GND"), ("R1", "1", "VIN"), ("R1", "2", "MID"),
                          ("R2", "1", "MID"), ("R2", "2", "GND"), ("J2", "1", "MID"), ("J2", "2", "GND")]:
        b.connect(ref, pin, net)
    b.add_power_flag("GND", (76.2, 101.6))
    b.add_power_flag("VIN", (88.9, 101.6))
    return b


if __name__ == "__main__":
    out = Path("/tmp/tiny_selftest.kicad_sch")
    build().write(out)
    print("wrote", out, out.stat().st_size, "bytes")

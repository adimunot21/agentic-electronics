"""FE-AAF-001 reference design: MANUALLY AUTHORED (not agent output).

Seed connectivity below is the *intent*. After generation the editable KiCad schematic owns connectivity and
values (docs/ownership.md); `intended_nets()` exists so a check can compare KiCad's own netlist to the intent.
Identities (value, footprint, MPN fields) come from designs/fe-aaf-001/components.yaml.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ae.kicad_sch import Part, SchematicBuilder, SymbolLibrary

ROOT = Path(__file__).resolve().parents[3]
DESIGN_DIR = ROOT / "designs" / "fe-aaf-001"
SYM_DIR = ROOT / "vendor" / "kicad-libs-10.0.8" / "symbols"

FOOTPRINTS = {
    "0603_R": "Resistor_SMD:R_0603_1608Metric", "0805_C": "Capacitor_SMD:C_0805_2012Metric",
    "1206_C": "Capacitor_SMD:C_1206_3216Metric", "0603_C": "Capacitor_SMD:C_0603_1608Metric",
}

# (ref, pin) lists per net. Power nets use KiCad power symbols. "intent" only: KiCad's netlist is the truth.
INTENDED_NETS: dict[str, list[tuple[str, str]]] = {
    "AIN": [("J1", "1"), ("C4", "1")],
    "N_IN": [("C4", "2"), ("R1", "1")],
    "X": [("R1", "2"), ("R2", "1"), ("R3", "1"), ("C1", "1")],
    "N_INV": [("R3", "2"), ("C2", "1"), ("U1", "2")],
    "N_S1": [("U1", "1"), ("R2", "2"), ("C2", "2"), ("R4", "1"), ("TP1", "1")],
    "N_INV2": [("R4", "2"), ("R5", "1"), ("C3", "1"), ("U1", "6")],
    "N_S2": [("U1", "7"), ("R5", "2"), ("C3", "2"), ("R6", "1")],
    "AOUT": [("R6", "2"), ("C5", "1"), ("J2", "1"), ("TP2", "1")],
    "VMID": [("R7", "2"), ("R8", "1"), ("C6", "1"), ("U1", "3"), ("U1", "5"), ("TP3", "1")],
    "VREF": [("R7", "1"), ("J2", "3")],
    "+5V": [("J1", "3"), ("U1", "8"), ("C8", "1"), ("C7", "1")],
    "GND": [("J1", "2"), ("J2", "2"), ("C1", "2"), ("C5", "2"), ("R8", "2"), ("C6", "2"), ("C8", "2"), ("C7", "2"), ("U1", "4"), ("TP4", "1")],
}

# schematic placement (mm, 1.27 grid) and rotation. Horizontal passives use rotation 90 => pin 1 left, pin 2 right.
PLACEMENT: dict[str, tuple[float, float, int]] = {
    "J1": (58.42, 63.5, 0), "C4": (88.9, 50.8, 90), "R1": (127.0, 50.8, 90),
    "R3": (190.5, 50.8, 90), "R2": (165.1, 76.2, 90), "C1": (152.4, 83.82, 0), "C2": (190.5, 76.2, 90),
    "U1A": (228.6, 63.5, 0), "TP1": (254.0, 50.8, 0),
    "R4": (165.1, 114.3, 90), "R5": (190.5, 127.0, 90), "C3": (190.5, 142.24, 90),
    "U1B": (228.6, 127.0, 0), "R6": (266.7, 127.0, 90), "C5": (299.72, 139.7, 0), "J2": (274.32, 101.6, 0), "TP2": (254.0, 152.4, 0),
    "R7": (50.8, 127.0, 0), "R8": (50.8, 152.4, 0), "C6": (76.2, 139.7, 0), "TP3": (101.6, 139.7, 0),
    "C8": (50.8, 177.8, 0), "C7": (76.2, 177.8, 0), "U1C": (127.0, 177.8, 0), "TP4": (101.6, 177.8, 0),
}
STUB_LENGTH: dict[tuple[str, str], float] = {("J1", "2"): 20.32, ("J2", "2"): 20.32}
DOGLEG: dict[tuple[str, str], float] = {("J1", "2"): -10.16, ("J1", "3"): 7.62, ("J2", "2"): -10.16}   # GND goes up, +5V goes down
OPAMP_PIN_STUB = 10.16     # keeps net labels clear of the pin-number text

LIB_IDS = {"R": "Device:R", "C": "Device:C", "J": "Connector_Generic:Conn_01x03", "TP": "Connector:TestPoint", "U": "Amplifier_Operational:LM358"}


def load_components(path: Path | None = None) -> dict:
    """Part manifest. `path` lets a harness workspace supply a CANDIDATE manifest; default is the adopted design."""
    return yaml.safe_load((path or DESIGN_DIR / "components.yaml").read_text())["parts"]


def footprint_for(ref: str, spec: dict) -> str:
    if "footprint" in spec:
        return spec["footprint"]
    pkg, kind = spec["package"], "R" if ref.startswith("R") else "C"
    return FOOTPRINTS[f"{pkg}_{kind}"]


def build(name: str = "fe_aaf_001", nets: dict | None = None, components_path: Path | None = None) -> SchematicBuilder:
    comps = load_components(components_path)
    builder = SchematicBuilder(name, SymbolLibrary(SYM_DIR), "FE-AAF-001 anti-alias front end (manual reference design)", paper="A3")
    pin_net = {pin: net for net, pins in (nets or INTENDED_NETS).items() for pin in pins}
    for ref, spec in comps.items():
        letter = "R" if ref[0] == "R" else "C" if ref[0] == "C" else "TP" if ref.startswith("TP") else ref[0]
        props = {"MPN": str(spec.get("mpn") or spec.get("mpn_candidate", "")), "MPN_Status": spec.get("mpn_status", "unknown")}
        for field_name, key in (("Tolerance", "tol"), ("Dielectric", "dielectric"), ("Voltage", "voltage")):   # fields the MPN consistency check reads from the schematic
            if key in spec:
                props[field_name] = str(spec[key])
        if "datasheet_url" in spec:      # the generic LM358 symbol carries the LM358 datasheet link: override with the real part's
            props["Datasheet"] = spec["datasheet_url"]
        if "manufacturer" in spec or "manufacturer_candidate" in spec:
            props["Manufacturer"] = spec.get("manufacturer") or spec["manufacturer_candidate"]
        value = spec.get("value", spec.get("mpn", "")) if ref != "U1" else spec["mpn"]
        if ref.startswith("TP"):
            value = "TP"
        footprint = footprint_for(ref, spec)
        lib_id = LIB_IDS[letter]
        if ref == "U1":
            for unit, key in ((1, "U1A"), (2, "U1B"), (3, "U1C")):
                x, y, rot = PLACEMENT[key]
                builder.add_part(Part("U1", lib_id, value, footprint, (x, y), rot, unit=unit, props=props,
                                      ref_offset=(0.0, -8.89) if unit < 3 else None, value_offset=(0.0, 8.89) if unit < 3 else None))
            continue
        x, y, rot = PLACEMENT[ref]
        builder.add_part(Part(ref, lib_id, value, footprint, (x, y), rot, props=props, in_bom=not ref.startswith("TP")))
    for (ref, pin), net in pin_net.items():
        unit = 1
        if ref == "U1":
            unit = {"1": 1, "2": 1, "3": 1, "5": 2, "6": 2, "7": 2, "4": 3, "8": 3}[pin]
        builder.connect(ref, pin, net, unit=unit, stub=STUB_LENGTH.get((ref, pin), OPAMP_PIN_STUB if ref == "U1" and unit < 3 else 5.08),
                        dogleg=DOGLEG.get((ref, pin), 0.0))
    builder.add_power_flag("GND", (165.1, 190.5))
    builder.add_power_flag("+5V", (190.5, 190.5))
    builder.note("FE-AAF-001: 3rd-order Butterworth anti-alias filter. MANUAL REFERENCE DESIGN (not agent-generated).", (25.4, 25.4), 2.0)
    builder.note("Stage 1: inverting MFB (Q=1, gain 1.3). Stage 2: inverting 1st-order. Net non-inverting, fc ~ 1.73 kHz (patch 0002; was 1.60). Bias VMID = VREF/2.", (25.4, 31.75), 1.27)
    return builder


def intended_nets(nets: dict | None = None) -> dict[str, set[tuple[str, str]]]:
    return {net: set(pins) for net, pins in (nets or INTENDED_NETS).items()}


if __name__ == "__main__":
    b = build()
    text = b.to_text()
    parts = {k for k in b.parts if not k.startswith("#")}
    print(f"schematic: {len(text)} bytes, {len(parts)} part-units, {sum(len(p) for p in INTENDED_NETS.values())} pin connections")
    assert {r.split(":")[0] for r in parts} >= set(load_components()), "a manifest part has no schematic symbol"

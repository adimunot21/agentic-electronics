"""Pin-mapping audit: manufacturer pin function -> KiCad symbol pin -> footprint pad -> SPICE node.

The reference is evidence/pins/<MPN>.yaml, hand-transcribed from the datasheet. It is compared with:
  (a) the KiCad symbol (.kicad_sym): pin number, name, electrical type, unit
  (b) the KiCad footprint (.kicad_mod): pad numbers, pitch, pin-1 corner, numbering direction, lead-span overlap
  (c) the schematic's actual wiring of each pin (from `kicad-cli sch export netlist`) vs. an expected function table
  (d) the SPICE model's .subckt node order
Netlist/parity agreement is NOT evidence here: a wrongly-numbered symbol still passes ERC and parity.

Outcomes: passed | failed | blocked | not_tested  (a missing input is `blocked`, never `passed`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ae.sexpr import find, find_all, parse
from ae.kicad_sch import SymbolLibrary


@dataclass
class AuditResult:
    check: str
    outcome: str
    detail: str


class EmbeddedSymbols(SymbolLibrary):
    """Symbols as embedded in a schematic file (what KiCad actually uses), not the library on disk."""

    def __init__(self, schematic_text: str):
        super().__init__(Path("."))
        self._embedded = {str(sym[1]): sym for sym in find_all(find(parse(schematic_text), "lib_symbols"), "symbol")}

    def get(self, lib_id: str) -> list:
        return self._embedded[lib_id]


def symbol_functions(lib: SymbolLibrary, lib_id: str, units: tuple[int, ...] = (1, 2, 3)) -> dict[str, str]:
    """pin number -> normalised function ('+IN A', '-IN B', 'OUT A', 'V+', 'V-') for a generic dual op-amp symbol."""
    result: dict[str, str] = {}
    for unit in units:
        letter = "AB"[unit - 1] if unit in (1, 2) else ""
        for pin in lib.pins(lib_id, unit):
            if pin.name == "+":
                result[pin.number] = f"+IN {letter}"
            elif pin.name == "-":
                result[pin.number] = f"-IN {letter}"
            elif pin.etype == "output":
                result[pin.number] = f"OUT {letter}"
            elif pin.name in ("V+", "V-"):
                result[pin.number] = pin.name
            else:
                result[pin.number] = f"?{pin.name}"
    return result


def audit_symbol(reference: dict, lib: SymbolLibrary, lib_id: str) -> AuditResult:
    expected = {str(n): spec["function"] for n, spec in reference["pin_table"].items()}
    actual = symbol_functions(lib, lib_id)
    diffs = [f"pin {n}: datasheet={expected.get(n)!r} symbol={actual.get(n)!r}" for n in sorted(set(expected) | set(actual)) if expected.get(n) != actual.get(n)]
    return AuditResult("symbol_pin_functions", "failed" if diffs else "passed", "; ".join(diffs) or f"{len(expected)} pins match datasheet pin table")


def audit_footprint(reference: dict, fp_path: Path) -> list[AuditResult]:
    geo = reference["package_geometry"]
    fp = parse(fp_path.read_text())
    pads = {str(p[1]): (float(find(p, "at")[1]), float(find(p, "at")[2]), float(find(p, "size")[1])) for p in find_all(fp, "pad")}
    results = []
    expected_numbers = {str(n) for n in reference["pin_table"]}
    ok = set(pads) == expected_numbers
    results.append(AuditResult("footprint_pad_numbers", "passed" if ok else "failed", f"pads={sorted(pads)} datasheet={sorted(expected_numbers)}"))
    # pitch: consecutive pads in one row
    left = sorted([(y, n) for n, (x, y, _) in pads.items() if x < 0])
    right = sorted([(y, n) for n, (x, y, _) in pads.items() if x > 0])
    pitches = {round(b[0] - a[0], 3) for row in (left, right) for a, b in zip(row, row[1:])}
    results.append(AuditResult("footprint_pitch", "passed" if pitches == {geo["pitch_mm"]} else "failed", f"pad pitches={sorted(pitches)} datasheet={geo['pitch_mm']}"))
    # numbering: KiCad y grows downward => pin 1 top-left means smallest y on left, numbers ascending downward, then 5.. up the right
    left_order = [n for _, n in left]
    right_order = [n for _, n in reversed(right)]  # bottom -> top ... pins 5..8 ascend bottom->top
    n_half = geo["pin_count"] // 2
    want_left = [str(i) for i in range(1, n_half + 1)]
    want_right = [str(i) for i in range(n_half + 1, geo["pin_count"] + 1)]
    ok_num = left_order == want_left and right_order == want_right
    results.append(AuditResult("footprint_numbering_and_pin1", "passed" if ok_num else "failed",
                               f"left top->bottom={left_order} right bottom->top={right_order}; datasheet: 1-{n_half} down left, {n_half+1}-{geo['pin_count']} up right, pin1 top-left"))
    # lead overlap: datasheet outer lead span must fall inside the pad x-extent
    x, _, width = next(iter(pads.values()))
    pad_half_outer = abs(x) + width / 2
    pad_half_inner = abs(x) - width / 2
    lo, hi = (v / 2 for v in geo["lead_span_outer_mm"])
    ok_span = pad_half_inner < lo and pad_half_outer > hi
    results.append(AuditResult("footprint_lead_overlap", "passed" if ok_span else "failed",
                               f"pad x-extent {pad_half_inner:.2f}..{pad_half_outer:.2f} mm vs lead outer edge {lo:.2f}..{hi:.2f} mm"))
    return results


def audit_spice_subckt(reference: dict, lib_path: Path | None) -> AuditResult:
    if lib_path is None or not lib_path.exists():
        return AuditResult("spice_subckt_nodes", "blocked", f"model file missing: {lib_path}")
    want = reference["spice_model"]
    pattern = re.compile(rf"^\.subckt\s+{re.escape(want['subckt'])}\s+(.*)$", re.IGNORECASE)
    for line in lib_path.read_text().replace("\r", "").splitlines():
        m = pattern.match(line.strip())
        if m:
            nodes = m.group(1).split()
            return AuditResult("spice_subckt_nodes", "passed" if nodes == want["node_order"] else "failed", f"model header nodes={nodes} evidence={want['node_order']}")
    return AuditResult("spice_subckt_nodes", "failed", f"no .subckt {want['subckt']} in {lib_path}")


def audit_wiring(reference: dict, netlist_path: Path, ref: str, expected_nets: dict[str, str]) -> AuditResult:
    """Check the schematic's REAL wiring (KiCad-exported netlist) of part ``ref`` by datasheet pin function.

    expected_nets: datasheet function -> net name the design intends (e.g. {'V+': '+5V', '+IN A': 'VMID'}).
    This ties the datasheet pin table to the electrical meaning; a swapped symbol cannot hide here."""
    tree = parse(netlist_path.read_text())
    pin_net: dict[str, str] = {}
    for net in find_all(find(tree, "nets"), "net"):
        for node in find_all(net, "node"):
            if str(find(node, "ref")[1]) == ref:
                pin_net[str(find(node, "pin")[1])] = str(find(net, "name")[1]).lstrip("/")
    table = {str(n): s["function"] for n, s in reference["pin_table"].items()}
    errors = []
    for function, want_net in expected_nets.items():
        pin = next((n for n, f in table.items() if f == function), None)
        got = pin_net.get(pin)
        if got != want_net:
            errors.append(f"{function} (pin {pin}): expected net {want_net!r}, netlist has {got!r}")
    return AuditResult(f"wiring_{ref}", "failed" if errors else "passed", "; ".join(errors) or f"{len(expected_nets)} datasheet functions wired as intended")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    ref = yaml.safe_load((root / "evidence/pins/OPA2314AID.yaml").read_text())
    lib = SymbolLibrary(root / "vendor/kicad-libs-10.0.8/symbols")
    results = [audit_symbol(ref, lib, "Amplifier_Operational:LM358")]
    results += audit_footprint(ref, root / "vendor/kicad-libs-10.0.8/footprints/Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod")
    results.append(audit_spice_subckt(ref, root / ref["spice_model"]["file"]))
    for r in results:
        print(f"[{r.outcome:>10}] {r.check}: {r.detail}")
    assert all(r.outcome == "passed" for r in results)

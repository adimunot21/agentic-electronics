"""Emit an editable KiCad 10 ``.kicad_sch`` that embeds REAL library symbols.

Contract (see docs/data-contract.md):
  * Symbol geometry/pins come verbatim from the KiCad library file (never invented here).
  * Pin->net connections are drawn as a short wire stub ending in a net label
    (or a power symbol), so connectivity is explicit and readable in the editor.
  * UUIDs are deterministic (uuid5 of design name + key) so regeneration is byte-stable.
  * Connectivity is ultimately judged by KiCad itself (ERC + netlist export), not by this module.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ae.sexpr import Q, dumps, extract_symbol, find, find_all, parse

GRID = 1.27  # mm, KiCad default schematic grid
STUB = 5.08  # mm wire stub from a pin to its label/power symbol
POWER_NETS = {"GND": "power:GND", "+5V": "power:+5V", "+3V3": "power:+3V3"}
_NS = uuid.UUID("6f1c2d34-0000-4000-8000-00000000ae01")


def _num(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") if x else "0"


@dataclass
class PinGeom:
    number: str
    name: str
    etype: str
    x: float  # symbol coords, y up
    y: float
    angle: float  # direction the pin line points *toward the body*


@dataclass
class Part:
    ref: str
    lib_id: str  # "Device:R"
    value: str
    footprint: str
    at: tuple[float, float]
    rotation: int = 0
    unit: int = 1
    props: dict[str, str] = field(default_factory=dict)
    in_bom: bool = True
    ref_offset: tuple[float, float] | None = None    # (dx, dy) mm of the Reference text from the part origin
    value_offset: tuple[float, float] | None = None


class SymbolLibrary:
    """Reads symbols (with ``extends`` flattened) from a directory of .kicad_sym files."""

    def __init__(self, sym_dir: Path):
        self.sym_dir = sym_dir
        self._text: dict[str, str] = {}

    def _lib_text(self, lib: str) -> str:
        if lib not in self._text:
            self._text[lib] = (self.sym_dir / f"{lib}.kicad_sym").read_text()
        return self._text[lib]

    def get(self, lib_id: str) -> list:
        """Flattened symbol form named ``lib_id`` (e.g. ``Device:R``) ready for lib_symbols."""
        lib, name = lib_id.split(":")
        sym = extract_symbol(self._lib_text(lib), name)
        ext = find(sym, "extends")
        if ext:
            base = self.get(f"{lib}:{ext[1]}")
            base_name = ext[1]
            merged = [Q(f"{lib}:{name}")] if False else None  # noqa: F841 (readability marker)
            props = {p[1]: p for p in find_all(sym, "property")}
            out = ["symbol", Q(lib_id)]
            for child in base[2:]:
                if isinstance(child, list) and child[0] == "property" and child[1] in props:
                    out.append(props.pop(child[1]))
                elif isinstance(child, list) and child[0] == "symbol":
                    sub = list(child)
                    sub[1] = Q(str(child[1]).replace(f"{base_name}_", f"{name}_", 1))
                    out.append(sub)
                else:
                    out.append(child)
            out.extend(props.values())
            return out
        flat = list(sym)
        flat[1] = Q(lib_id)
        return flat

    def pins(self, lib_id: str, unit: int = 1) -> list[PinGeom]:
        """Pins of ``unit`` (plus unit-0 shared graphics pins) in symbol coordinates."""
        sym = self.get(lib_id)
        result: list[PinGeom] = []
        for sub in find_all(sym, "symbol"):
            tail = str(sub[1]).rsplit("_", 2)  # "<name>_<unit>_<style>"
            sub_unit, sub_style = int(tail[-2]), int(tail[-1])
            if sub_unit not in (0, unit) or sub_style not in (0, 1):
                continue
            for pin in find_all(sub, "pin"):
                at = find(pin, "at")
                result.append(
                    PinGeom(
                        number=str(find(pin, "number")[1]),
                        name=str(find(pin, "name")[1]),
                        etype=str(pin[1]),
                        x=float(at[1]),
                        y=float(at[2]),
                        angle=float(at[3]),
                    )
                )
        return result


def _rotate(x: float, y: float, rotation_deg: int) -> tuple[float, float]:
    rad = math.radians(rotation_deg)
    return (x * math.cos(rad) - y * math.sin(rad), x * math.sin(rad) + y * math.cos(rad))


class SchematicBuilder:
    """Accumulates parts, connections and notes, then serialises the schematic."""

    def __init__(self, name: str, library: SymbolLibrary, title: str, paper: str = "A4"):
        self.name, self.library, self.title, self.paper = name, library, title, paper
        self.root_uuid = str(uuid.uuid5(_NS, f"{name}/root"))
        self.parts: dict[str, Part] = {}
        self.items: list[list] = []  # wires, labels, power symbols, texts
        self._counter = 0
        self._power_flags: set[str] = set()
        self._used_lib_ids: list[str] = []

    # -- identity -----------------------------------------------------------------
    def uid(self, key: str) -> str:
        self._counter += 1
        return str(uuid.uuid5(_NS, f"{self.name}/{key}/{self._counter}"))

    def symbol_uuid(self, ref: str) -> str:
        return str(uuid.uuid5(_NS, f"{self.name}/symbol/{ref}"))

    # -- building -----------------------------------------------------------------
    def add_part(self, part: Part) -> Part:
        if part.ref in self.parts and part.unit == 1:
            raise ValueError(f"duplicate reference {part.ref}")
        self.parts[f"{part.ref}:{part.unit}"] = part
        if part.lib_id not in self._used_lib_ids:
            self._used_lib_ids.append(part.lib_id)
        return part

    def pin_point(self, part: Part, pin_number: str) -> tuple[float, float, tuple[float, float]]:
        """Absolute (x, y) of a pin's connection point and its outward unit vector (y down)."""
        for pin in self.library.pins(part.lib_id, part.unit):
            if pin.number == pin_number:
                dx, dy_up = _rotate(pin.x, pin.y, part.rotation)
                tx, ty_up = _rotate(math.cos(math.radians(pin.angle)), math.sin(math.radians(pin.angle)), part.rotation)
                return (
                    round(part.at[0] + dx, 4),
                    round(part.at[1] - dy_up, 4),
                    (-round(tx), round(ty_up)),  # outward = away from body; flip y for screen
                )
        raise KeyError(f"{part.lib_id} unit {part.unit} has no pin {pin_number}")

    def connect(self, ref: str, pin_number: str, net: str, unit: int = 1, stub: float = STUB, dogleg: float = 0.0) -> None:
        """Draw pin -> wire stub -> net label (or power symbol for GND/+5V/+3V3)."""
        part = self.parts[f"{ref}:{unit}"]
        px, py, (ox, oy) = self.pin_point(part, pin_number)
        ex, ey = round(px + ox * stub, 4), round(py + oy * stub, 4)
        self.items.append(self._wire((px, py), (ex, ey)))
        if dogleg:   # extra vertical segment so power symbols clear neighbouring pins' wires
            self.items.append(self._wire((ex, ey), (ex, round(ey + dogleg, 4))))
            ey = round(ey + dogleg, 4)
            ox, oy = 0, (1 if dogleg > 0 else -1)
        if net in POWER_NETS:
            self._power_symbol(net, ex, ey, outward=(ox, oy))
        else:
            angle = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}[(ox, oy)]
            self.items.append(
                [
                    "label", Q(net),
                    ["at", _num(ex), _num(ey), str(angle)],
                    ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left", "bottom"]],
                    ["uuid", Q(self.uid(f"label/{net}"))],
                ]
            )

    def note(self, text: str, at: tuple[float, float], size: float = 1.27) -> None:
        self.items.append(
            [
                "text", Q(text), ["exclude_from_sim", "yes"],
                ["at", _num(at[0]), _num(at[1]), "0"],
                ["effects", ["font", ["size", str(size), str(size)]], ["justify", "left", "bottom"]],
                ["uuid", Q(self.uid("text"))],
            ]
        )

    def _wire(self, a: tuple[float, float], b: tuple[float, float]) -> list:
        return [
            "wire",
            ["pts", ["xy", _num(a[0]), _num(a[1])], ["xy", _num(b[0]), _num(b[1])]],
            ["stroke", ["width", "0"], ["type", "solid"]],
            ["uuid", Q(self.uid("wire"))],
        ]

    def _power_symbol(self, net: str, x: float, y: float, outward: tuple[float, float]) -> None:
        lib_id = POWER_NETS[net]
        ref = f"#PWR{len([k for k in self.parts if k.startswith('#PWR')]) + 1:02d}"
        # Power symbols have one pin at their origin; GND points down, +V points up.
        up, down = outward[1] < 0, outward[1] > 0          # screen coordinates: y grows downward
        rotation = (180 if up else 0) if net == "GND" else (180 if down else 0)
        part = Part(ref, lib_id, net, "", (x, y), rotation=rotation)
        self.add_part(part)

    def add_power_flag(self, net: str, near: tuple[float, float]) -> None:
        """PWR_FLAG on ``net``: tells ERC this net is driven by an off-sheet source (e.g. a connector)."""
        if net in self._power_flags:
            return
        self._power_flags.add(net)
        ref = f"#FLG{len(self._power_flags):02d}"
        flag = self.add_part(Part(ref, "power:PWR_FLAG", "PWR_FLAG", "", near, rotation=180))
        px, py, (ox, oy) = self.pin_point(flag, "1")
        endx, endy = round(px + ox * STUB * 0.5, 4), round(py + oy * STUB * 0.5, 4)
        self.items.append(self._wire((px, py), (endx, endy)))
        if net in POWER_NETS:  # global power net: end on a power symbol (a local label would clash)
            self._power_symbol(net, endx, endy, outward=(ox, oy))
        else:
            self.items.append(
                ["label", Q(net), ["at", _num(endx), _num(endy), "90"],
                 ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left", "bottom"]],
                 ["uuid", Q(self.uid(f"flaglabel/{net}"))]]
            )

    # -- serialisation ------------------------------------------------------------
    def _instance(self, part: Part) -> list:
        is_power = part.ref.startswith("#")
        sym = self.library.get(part.lib_id)
        pin_nums = sorted({p.number for p in self.library.pins(part.lib_id, part.unit)})
        text_angle = 0 if is_power else (360 - part.rotation) % 360   # symbol rotation also rotates field text; compensate
        horizontal_body = part.rotation in (90, 270)
        def prop(name: str, value: str, dx: float, dy: float, hide: bool = False) -> list:
            eff = ["effects", ["font", ["size", "1.27", "1.27"]]]
            if name in ("Reference", "Value") and not hide and not is_power and not horizontal_body and part.ref_offset is None:
                eff.append(["justify", "left"])
            if hide:
                eff.append(["hide", "yes"])
            return ["property", Q(name), Q(value), ["at", _num(part.at[0] + dx), _num(part.at[1] + dy), str(text_angle)], eff]
        ref_dx, ref_dy = part.ref_offset or ((0.0, -3.5) if horizontal_body else (2.54, -1.27))
        val_dx, val_dy = part.value_offset or ((0.0, 3.5) if horizontal_body else (2.54, 1.27))
        lib_fields = {p[1]: p[2] for p in find_all(sym, "property")}
        children = [
            ["lib_id", Q(part.lib_id)],
            ["at", _num(part.at[0]), _num(part.at[1]), str(part.rotation)],
            ["unit", str(part.unit)],
            ["exclude_from_sim", "no"], ["in_bom", "no" if (is_power or not part.in_bom) else "yes"],
            ["on_board", "no" if is_power else "yes"], ["dnp", "no"],
            ["uuid", Q(self.symbol_uuid(part.ref) if part.unit == 1 else str(uuid.uuid5(_NS, f"{self.name}/symbol/{part.ref}/u{part.unit}")))],
            prop("Reference", part.ref, ref_dx, ref_dy, hide=is_power),
            prop("Value", part.value, val_dx, val_dy),
            prop("Footprint", part.footprint, 0, 0, hide=True),
            prop("Datasheet", part.props.get("Datasheet", str(lib_fields.get("Datasheet", ""))), 0, 0, hide=True),
        ]
        for key, val in part.props.items():
            if key != "Datasheet":
                children.append(prop(key, val, 0, 0, hide=True))
        for num in pin_nums:
            children.append(["pin", Q(num), ["uuid", Q(self.uid(f"pin/{part.ref}/{num}"))]])
        children.append(
            ["instances", ["project", Q(self.name), ["path", Q(f"/{self.root_uuid}"), ["reference", Q(part.ref)], ["unit", str(part.unit)]]]]
        )
        return ["symbol", *children]

    def to_text(self) -> str:
        lib_symbols = ["lib_symbols", *[self.library.get(lib_id) for lib_id in self._used_lib_ids]]
        root = [
            "kicad_sch",
            ["version", "20250114"], ["generator", Q("ae-kicad-sch")], ["generator_version", Q("0.0.1")],
            ["uuid", Q(self.root_uuid)], ["paper", Q(self.paper)],
            ["title_block", ["title", Q(self.title)]],
            lib_symbols,
            *self.items,
            *[self._instance(p) for p in self.parts.values()],
            ["sheet_instances", ["path", Q("/"), ["page", Q("1")]]],
            ["embedded_fonts", "no"],
        ]
        return dumps(root) + "\n"

    def write(self, path: Path) -> str:
        text = self.to_text()
        parse(text)  # must re-parse; catches serialiser bugs before KiCad does
        path.write_text(text)
        return text


if __name__ == "__main__":
    lib = SymbolLibrary(Path(__file__).resolve().parents[2] / "vendor/kicad-libs-10.0.8/symbols")
    pins_r = {p.number: (p.x, p.y, p.angle) for p in lib.pins("Device:R")}
    assert pins_r == {"1": (0.0, 3.81, 270.0), "2": (0.0, -3.81, 90.0)}, pins_r
    b = SchematicBuilder("selftest", lib, "selftest")
    r1 = b.add_part(Part("R1", "Device:R", "10k", "Resistor_SMD:R_0603_1608Metric", (50.8, 50.8)))
    b.connect("R1", "1", "VIN")
    b.connect("R1", "2", "GND")
    text = b.to_text()
    assert text.count("(symbol\n") == 2 and "(label \"VIN\"" in text, text[:300]
    print("kicad_sch selftest ok;", len(text), "bytes; R1 pin1 at", b.pin_point(r1, "1"))

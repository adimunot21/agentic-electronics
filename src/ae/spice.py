"""SPICE deck generation from KiCad's exported netlist + a connectivity cross-check back against that netlist.

Ownership: connectivity and values come from the KiCad schematic (via `sch export netlist`); this module only
translates. Test-bench-only elements (sources, loads, offset corners) are marked `* TB_ONLY` and excluded from the
cross-check, so the check compares exactly the circuit under test.
Net naming: KiCad "GND" -> SPICE node 0; "+5V" -> P5V; leading "/" dropped.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ae.netlist import net_partition, normalise_net, read_netlist

SPICE_NET = {"GND": "0", "+5V": "P5V"}
SKIP_PREFIXES = ("J", "TP")      # connectors / test points carry no circuit behaviour


def spice_net(kicad_name: str) -> str:
    name = normalise_net(kicad_name)
    return SPICE_NET.get(name, re.sub(r"[^A-Za-z0-9_]", "_", name))


def _pin_nets(nets: dict) -> dict[tuple[str, str], str]:
    return {(ref, pin): spice_net(net) for net, nodes in nets.items() for ref, pin, _ in nodes if not ref.startswith("#")}


_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "meg": 1e6, "g": 1e9}


def parse_si(text: str) -> float:
    m = re.fullmatch(r"([0-9.]+)\s*(meg|[fpnumkg])?", text.strip().lower())
    if not m:
        raise ValueError(f"cannot parse component value {text!r}")
    return float(m.group(1)) * (_SI[m.group(2)] if m.group(2) else 1.0)


def build_dut_deck(netlist_path: Path, pin_evidence: dict, model_include: str, *, vos_a: float = 0.0, vos_b: float = 0.0,
                   scale: dict[str, float] | None = None) -> str:
    """Flat deck of the circuit under test. Offset corners are injected as TB_ONLY series sources at each +IN."""
    comps, nets = read_netlist(netlist_path)
    pin_net = _pin_nets(nets)
    table = {n: s["function"] for n, s in pin_evidence["pin_table"].items()}
    func_pin = {f: str(n) for n, f in table.items()}
    node_function = pin_evidence["spice_model"]["node_function"]
    lines = ["* DUT deck generated from KiCad netlist (do not edit; regenerate)", f".include {model_include}"]
    for comp in sorted(comps, key=lambda c: c["ref"]):
        ref, value = comp["ref"], comp["value"]
        if ref.startswith(SKIP_PREFIXES):
            continue
        if ref[0] in "RC":
            if scale and ref in scale:     # tolerance corner / Monte Carlo sample: numeric value x factor (TB-only perturbation)
                value = f"{parse_si(value) * scale[ref]:.9g}"
            lines.append(f"{ref} {pin_net[(ref, '1')]} {pin_net[(ref, '2')]} {value}")
        elif ref[0] == "U":
            for channel, vos in (("A", vos_a), ("B", vos_b)):
                nodes = []
                for model_node in pin_evidence["spice_model"]["node_order"]:
                    function = node_function[model_node]
                    function = f"{function} {channel}" if function.split()[0] in ("+IN", "-IN", "OUT") else function
                    nodes.append(pin_net[(ref, func_pin[function])])
                if vos:   # TB-only: series offset source in front of the non-inverting input
                    inner = f"{nodes[0]}_vos{channel}"
                    lines.append(f"V_vos{channel} {inner} {nodes[0]} DC {vos}  ; TB_ONLY")
                    nodes[0] = inner
                lines.append(f"X{ref}{channel} {' '.join(nodes)} {pin_evidence['spice_model']['subckt']}")
        else:
            raise ValueError(f"no SPICE mapping for {ref}")
    return "\n".join(lines) + "\n"


def parse_deck_connectivity(deck: str, pin_evidence: dict) -> dict[tuple[str, str], str]:
    """Rebuild (ref, pin) -> node from the deck text alone (excluding TB_ONLY lines)."""
    table = {n: s["function"] for n, s in pin_evidence["pin_table"].items()}
    func_pin = {f: str(n) for n, f in table.items()}
    node_function = pin_evidence["spice_model"]["node_function"]
    result: dict[tuple[str, str], str] = {}
    # TB_ONLY series offset sources (V_vosA inner outer): resolve injected nodes back to the real net
    inject = {}
    for raw in deck.splitlines():
        if "TB_ONLY" in raw and raw.startswith("V_vos"):
            tok = raw.split(";")[0].split()
            inject[tok[1]] = tok[2]
    for raw in deck.splitlines():
        line = raw.split(";")[0].strip() if not raw.startswith("*") else ""
        if not line or "TB_ONLY" in raw or line.startswith("."):
            continue
        tok = line.split()
        name = tok[0]
        if name[0] in "RC":
            result[(name, "1")], result[(name, "2")] = tok[1], tok[2]
        elif name.startswith("X"):
            ref, channel = name[1:-1], name[-1]
            for model_node, node in zip(pin_evidence["spice_model"]["node_order"], tok[1:6]):
                node = inject.get(node, node)
                function = node_function[model_node]
                function = f"{function} {channel}" if function.split()[0] in ("+IN", "-IN", "OUT") else function
                result[(ref, func_pin[function])] = node
    return result


def check_deck_vs_netlist(deck: str, netlist_path: Path, pin_evidence: dict) -> tuple[str, str]:
    """Same pins on the same nets in the deck as in KiCad's export (ignoring connectors/test points and unused pins)."""
    _, nets = read_netlist(netlist_path)
    expected = {k: v for k, v in _pin_nets(nets).items() if not k[0].startswith(SKIP_PREFIXES)}
    got = parse_deck_connectivity(deck, pin_evidence)
    # U1 supply pins appear once per channel in the deck (same node); power-unit pins are covered by the channel instances
    problems = [f"{k}: deck={got.get(k)!r} kicad={v!r}" for k, v in sorted(expected.items()) if got.get(k) != v]
    problems += [f"{k}: deck-only connection to {v!r}" for k, v in got.items() if k not in expected]
    return ("failed", "; ".join(problems)) if problems else ("passed", f"{len(expected)} pin connections identical between deck and KiCad netlist")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    ev = yaml.safe_load((root / "evidence/pins/OPA2314AID.yaml").read_text())
    net = root / "runs/ref/dev/fe_aaf_001.net"
    deck = build_dut_deck(net, ev, "opa2314_lf.lib")
    print(deck)
    print(check_deck_vs_netlist(deck, net, ev))

"""Reader for KiCad's own `sch export netlist --format kicadsexpr` output (the connectivity source of truth).

Contract (observed on KiCad 10.0.8, see docs/data-contract.md):
  * net names: local labels are exported with a leading "/" (e.g. "/MID"); power nets are plain ("GND", "+5V")
  * each (comp ...) has (ref), (value), (footprint), (tstamps "<symbol uuid>") and (sheetpath (tstamps "/"))
  * each (node ...) has (ref), (pin), optional (pinfunction), (pintype)
  * unconnected pins appear in nets named "unconnected-(REF-PinX)"
"""
from __future__ import annotations

from pathlib import Path

from ae.sexpr import find, find_all, parse


def read_netlist(path: Path) -> tuple[list[dict], dict[str, list[tuple[str, str, str]]]]:
    tree = parse(path.read_text())
    comps = []
    for comp in find_all(find(tree, "components"), "comp"):
        comps.append({
            "ref": str(find(comp, "ref")[1]), "value": str(find(comp, "value")[1]),
            "footprint": str(find(comp, "footprint")[1]) if find(comp, "footprint") else "",
            "uuid": str(find(comp, "tstamps")[1]), "sheet": str(find(find(comp, "sheetpath"), "tstamps")[1]),
            "fields": {str(find(f, "name")[1]): (str(f[2]) if len(f) > 2 else "") for f in find_all(find(comp, "fields"), "field")} if find(comp, "fields") else {},
        })
    nets: dict[str, list[tuple[str, str, str]]] = {}
    for net in find_all(find(tree, "nets"), "net"):
        nodes = []
        for node in find_all(net, "node"):
            pinfn = find(node, "pinfunction")
            nodes.append((str(find(node, "ref")[1]), str(find(node, "pin")[1]), str(pinfn[1]) if pinfn else ""))
        nets[str(find(net, "name")[1])] = nodes
    return comps, nets


def normalise_net(name: str) -> str:
    return name[1:] if name.startswith("/") else name


def net_partition(nets: dict[str, list[tuple[str, str, str]]]) -> dict[str, frozenset[tuple[str, str]]]:
    """normalised net name -> set of (ref, pin); drops power-flag/virtual parts (refs starting with '#')."""
    return {normalise_net(n): frozenset((r, p) for r, p, _ in nodes if not r.startswith("#")) for n, nodes in nets.items()}


if __name__ == "__main__":
    import sys

    comps, nets = read_netlist(Path(sys.argv[1]))
    print(f"{len(comps)} components, {len(nets)} nets")

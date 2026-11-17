"""Run INSIDE the pinned image (uses the `pcbnew` SWIG module shipped with KiCad 10.0.8).

Subcommands (all take explicit paths; JSON config for `build`):
  build  CONFIG.json            netlist + placement -> .kicad_pcb (footprints, nets, outline, zones, rules)
  dsn    BOARD OUT.dsn          Specctra DSN export (input to Freerouting)
  ses    BOARD SES OUT_BOARD    import routed session, refill zones, save
  fill   BOARD OUT_BOARD        refill zones only
Standalone self-check:  python3 pcb_ops.py selftest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pcbnew

from ae.sexpr import find, find_all, parse

FP_LIB_DIR = Path("/usr/share/kicad/footprints")
mm = pcbnew.FromMM


def vec(x_mm: float, y_mm: float) -> "pcbnew.VECTOR2I":
    return pcbnew.VECTOR2I(mm(x_mm), mm(y_mm))


def read_netlist(path: Path) -> tuple[list[dict], dict[str, list[tuple[str, str, str]]]]:
    """Parse KiCad's own `sch export netlist --format kicadsexpr` output.

    Returns (components, nets). components: ref/value/footprint/uuid/sheet path.
    nets: net name -> [(ref, pin number, pinfunction)]. Net names are used verbatim
    ("/MID" for local labels, "GND" for power) because the PCB must carry the same strings.
    """
    tree = parse(path.read_text())
    comps = []
    for comp in find_all(find(tree, "components"), "comp"):
        comps.append(
            {
                "ref": str(find(comp, "ref")[1]),
                "value": str(find(comp, "value")[1]),
                "footprint": str(find(comp, "footprint")[1]),
                "uuid": str(find(comp, "tstamps")[1]),
                "sheet": str(find(find(comp, "sheetpath"), "tstamps")[1]),
            }
        )
    nets: dict[str, list[tuple[str, str, str]]] = {}
    for net in find_all(find(tree, "nets"), "net"):
        nodes = []
        for node in find_all(net, "node"):
            pinfn = find(node, "pinfunction")
            nodes.append((str(find(node, "ref")[1]), str(find(node, "pin")[1]), str(pinfn[1]) if pinfn else ""))
        nets[str(find(net, "name")[1])] = nodes
    return comps, nets


def build(config_path: Path) -> None:
    cfg = json.loads(config_path.read_text())
    comps, nets = read_netlist(Path(cfg["netlist"]))
    board = pcbnew.CreateEmptyBoard()
    board.SetCopperLayerCount(cfg.get("copper_layers", 2))
    net_items = {}
    for name in nets:
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        net_items[name] = item
    pin_net = {(ref, pin): name for name, nodes in nets.items() for ref, pin, _ in nodes}

    placement = cfg["placement"]
    for comp in comps:
        ref = comp["ref"]
        if ref not in placement:
            raise SystemExit(f"no placement for {ref}: placement must be explicit")
        lib, name = comp["footprint"].split(":")
        fp = pcbnew.FootprintLoad(str(FP_LIB_DIR / f"{lib}.pretty"), name)
        if fp is None:
            raise SystemExit(f"footprint {comp['footprint']} not found in {FP_LIB_DIR}")
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(ref)
        fp.SetValue(comp["value"])
        fp.SetPath(pcbnew.KIID_PATH(f"{comp['sheet'].rstrip('/')}/{comp['uuid']}"))
        x, y, rot = placement[ref]
        fp.SetPosition(vec(x, y))
        fp.SetOrientationDegrees(rot)
        for pad in fp.Pads():
            key = (ref, pad.GetNumber())
            if key in pin_net:
                pad.SetNet(net_items[pin_net[key]])
        board.Add(fp)

    x0, y0, x1, y1 = cfg["outline"]
    outline = pcbnew.PCB_SHAPE(board)
    outline.SetShape(pcbnew.SHAPE_T_RECT)
    outline.SetStart(vec(x0, y0))
    outline.SetEnd(vec(x1, y1))
    outline.SetLayer(pcbnew.Edge_Cuts)
    outline.SetWidth(mm(0.1))
    board.Add(outline)

    rules = cfg["rules"]
    settings = board.GetDesignSettings()
    netclass = settings.m_NetSettings.GetDefaultNetclass()
    netclass.SetTrackWidth(mm(rules["track_width_mm"]))
    netclass.SetClearance(mm(rules["clearance_mm"]))
    netclass.SetViaDiameter(mm(rules["via_diameter_mm"]))
    netclass.SetViaDrill(mm(rules["via_drill_mm"]))
    settings.m_MinClearance = mm(rules["clearance_mm"])
    settings.m_TrackMinWidth = mm(rules["min_track_mm"])
    settings.m_ViasMinSize = mm(rules["via_diameter_mm"])
    settings.m_MinThroughDrill = mm(rules["via_drill_mm"])

    for zone_cfg in cfg.get("zones", []):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(board.GetLayerID(zone_cfg["layer"]))
        zone.SetNet(net_items[zone_cfg["net"]])
        zone.SetMinThickness(mm(0.2))
        zone.SetLocalClearance(mm(rules["clearance_mm"]))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        inset = zone_cfg.get("inset_mm", 0.5)
        zone.Outline().NewOutline()
        for px, py in [(x0 + inset, y0 + inset), (x1 - inset, y0 + inset), (x1 - inset, y1 - inset), (x0 + inset, y1 - inset)]:
            zone.Outline().Append(mm(px), mm(py))
        board.Add(zone)

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(cfg["output"])
    print(f"built {cfg['output']}: {len(comps)} footprints, {len(nets)} nets, {len(board.Zones())} zones")


def export_dsn(board_path: Path, out: Path) -> None:
    board = pcbnew.LoadBoard(str(board_path))
    ok = pcbnew.ExportSpecctraDSN(board, str(out))
    print(f"ExportSpecctraDSN -> {out} ok={ok} size={out.stat().st_size if out.exists() else 0}")
    if not ok:
        raise SystemExit(1)


def import_ses(board_path: Path, ses: Path, out_board: Path) -> None:
    board = pcbnew.LoadBoard(str(board_path))
    ok = pcbnew.ImportSpecctraSES(board, str(ses))
    tracks = sum(1 for t in board.GetTracks())
    print(f"ImportSpecctraSES ok={ok} tracks_after_import={tracks}")
    if not ok:
        raise SystemExit(1)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(out_board))
    print(f"saved {out_board} (zones refilled: {len(board.Zones())})")


def fill_only(board_path: Path, out_board: Path) -> None:
    board = pcbnew.LoadBoard(str(board_path))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(out_board))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        build(Path(sys.argv[2]))
    elif cmd == "dsn":
        export_dsn(Path(sys.argv[2]), Path(sys.argv[3]))
    elif cmd == "ses":
        import_ses(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
    elif cmd == "fill":
        fill_only(Path(sys.argv[2]), Path(sys.argv[3]))
    elif cmd == "selftest":
        print("pcbnew", pcbnew.Version(), "dsn export:", hasattr(pcbnew, "ExportSpecctraDSN"))
    else:
        sys.exit(__doc__)

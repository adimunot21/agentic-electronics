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

from ae.netlist import read_netlist

FP_LIB_DIR = Path("/usr/share/kicad/footprints")
mm = pcbnew.FromMM


def vec(x_mm: float, y_mm: float) -> "pcbnew.VECTOR2I":
    return pcbnew.VECTOR2I(mm(x_mm), mm(y_mm))


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
        for field_name, field_value in comp.get("fields", {}).items():     # carry custom schematic fields (MPN...) so parity sees them
            if field_name not in ("Footprint", "Description", "Reference", "Value") and field_value:
                fp.SetField(field_name, field_value)
                fp.GetField(field_name).SetVisible(False)
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

    print(f"reference texts placed: {place_reference_texts(board)}")
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(cfg["output"])
    print(f"built {cfg['output']}: {len(comps)} footprints, {len(nets)} nets, {len(board.Zones())} zones")


def place_reference_texts(board) -> int:
    """Move each reference designator (silk) to the first free spot above/below/right/left of its footprint.

    Free = text box does not touch any pad (inflated) or an already placed text box. Pure heuristic for legibility;
    DRC (silk_over_copper / silk_overlap) is still the judge. Returns how many references had to move.
    """
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            pads.append((bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()))
    margin = mm(0.25)
    placed: list[tuple[int, int, int, int]] = []
    moved = 0
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        ref = fp.Reference()
        text_w, text_h = mm(0.95 * max(2, len(fp.GetReference()))), mm(1.1)
        body = fp.GetBoundingBox(False)      # footprint body incl. pads, excl. text
        cx = (body.GetLeft() + body.GetRight()) // 2
        cy = (body.GetTop() + body.GetBottom()) // 2
        candidates = [(cx, body.GetTop() - mm(0.9)), (cx, body.GetBottom() + mm(0.9)),
                      (body.GetRight() + text_w // 2 + mm(0.4), cy), (body.GetLeft() - text_w // 2 - mm(0.4), cy),
                      (cx, body.GetTop() - mm(2.2)), (cx, body.GetBottom() + mm(2.2))]
        chosen = None
        for x, y in candidates:
            box = (x - text_w // 2, y - text_h // 2, x + text_w // 2, y + text_h // 2)
            inside = box[0] > mm(0.6) and box[1] > mm(0.6)
            hit = any(not (box[2] + margin < p[0] or box[0] - margin > p[2] or box[3] + margin < p[1] or box[1] - margin > p[3]) for p in pads) \
                or any(not (box[2] < q[0] or box[0] > q[2] or box[3] < q[1] or box[1] > q[3]) for q in placed)
            if inside and not hit:
                chosen = (x, y, box)
                break
        if chosen is None:
            chosen = (*candidates[0], (candidates[0][0] - text_w // 2, candidates[0][1] - text_h // 2, candidates[0][0] + text_w // 2, candidates[0][1] + text_h // 2))
        ref.SetPosition(pcbnew.VECTOR2I(int(chosen[0]), int(chosen[1])))
        ref.SetTextAngleDegrees(0)
        placed.append(chosen[2])
        moved += 1
    return moved


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

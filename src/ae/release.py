"""Release outputs (BOM, Gerbers, drill, placement) + the BOM/manufacturing requirement checks.

Executed means: files produced by kicad-cli in the pinned image and sanity-checked below. NOT established here:
that a fab accepts the files, that the assembled board works, or that the MPNs are orderable.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ae.sexpr import find, find_all, parse
from ae.toolchain import StageRecord, run_in_image

GERBER_LAYERS = "F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,F.Paste,Edge.Cuts"


def export_all(work: Path, stem: str) -> list[StageRecord]:
    steps = [
        ("bom", ["kicad-cli", "sch", "export", "bom", "--fields", "Reference,Value,Footprint,MPN,MPN_Status,Manufacturer", "--labels",
                 "Refs,Value,Footprint,MPN,MPN_Status,Manufacturer", "--group-by", "Value,Footprint,MPN", "--ref-range-delimiter", "", "-o", "release/bom.csv", f"{stem}.kicad_sch"]),
        ("gerbers", ["kicad-cli", "pcb", "export", "gerbers", "--layers", GERBER_LAYERS, "--no-protel-ext", "-o", "release/gerbers/", f"{stem}.kicad_pcb"]),
        ("drill", ["kicad-cli", "pcb", "export", "drill", "--format", "excellon", "--drill-origin", "absolute", "-o", "release/gerbers/", f"{stem}.kicad_pcb"]),
        ("pos", ["kicad-cli", "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "-o", "release/cpl.csv", f"{stem}.kicad_pcb"]),
    ]
    (work / "release" / "gerbers").mkdir(parents=True, exist_ok=True)
    records = []
    for name, cmd in steps:
        rec = run_in_image(f"release_{name}", cmd, work)
        rec.outcome = "passed" if rec.exit_code == 0 else "error"
        records.append(rec)
    return records


def check_bom(work: Path) -> tuple[str, str]:
    path = work / "release" / "bom.csv"
    if not path.exists():
        return "blocked", "BOM not generated"
    rows = list(csv.DictReader(path.open()))
    no_mpn = [r["Refs"] for r in rows if not r["MPN"].strip() or r["MPN"].strip() in ("unselected",) or r["MPN"].lower().startswith("generic")]
    unverified = [r["Refs"] for r in rows if r["MPN_Status"] != "verified"]
    detail = f"{len(rows)} BOM lines; {len(no_mpn)} without a selected MPN {no_mpn}; {len(unverified)} with unverified MPN evidence"
    if no_mpn:
        return "failed", detail
    if unverified:
        return "blocked", detail + " (MPNs are decoded candidates, not confirmed against manufacturer/distributor data)"
    return "passed", detail


def board_extents_mm(pcb: Path) -> tuple[float, float]:
    tree = parse(pcb.read_text())
    xs, ys = [], []
    for shape in find_all(tree, "gr_rect"):
        if "Edge.Cuts" in [str(a) for a in (find(shape, "layer") or [])]:
            s, e = find(shape, "start"), find(shape, "end")
            xs += [float(s[1]), float(e[1])]; ys += [float(s[2]), float(e[2])]
    return (max(xs) - min(xs), max(ys) - min(ys)) if xs else (0.0, 0.0)


def check_mfg(work: Path, stem: str, drc_counts: dict | None) -> tuple[str, str]:
    g = work / "release" / "gerbers"
    files = sorted(p.name for p in g.glob("*") if p.is_file() and p.stat().st_size > 0)
    w, h = board_extents_mm(work / f"{stem}.kicad_pcb")
    problems = []
    if not any(n.endswith("Edge_Cuts.gbr") or "Edge" in n for n in files):
        problems.append("no Edge.Cuts gerber")
    if not any(n.lower().endswith(".drl") for n in files):
        problems.append("no drill file")
    if not (0 < w <= 40.0 and 0 < h <= 30.0):
        problems.append(f"board {w:.1f}x{h:.1f} mm exceeds 40x30")
    if drc_counts is None or sum(drc_counts.values()) != 0:
        problems.append(f"DRC not clean: {drc_counts}")
    if not (work / "release" / "cpl.csv").exists():
        problems.append("no placement file")
    detail = f"board {w:.1f} x {h:.1f} mm; {len(files)} non-empty fabrication files; project rules min track/space 0.15, drill 0.3 (designs/fe-aaf-001/fe_aaf_001.kicad_pro)"
    return ("failed", "; ".join(problems) + " | " + detail) if problems else ("passed", detail)

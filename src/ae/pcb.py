"""Board stages: build (pcbnew, in image) -> DSN export -> Freerouting -> SES import + zone fill -> DRC with parity."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from ae.toolchain import StageRecord, run_in_image, run_router

STEM = "fe_aaf_001"


def build_board(work: Path, placement_file: Path) -> StageRecord:
    spec = yaml.safe_load(placement_file.read_text())
    shutil.copy(placement_file.parent / f"{STEM}.kicad_pro", work / f"{STEM}.kicad_pro")      # design rules live in the project file
    cfg = {"netlist": f"/work/{STEM}.net", "output": f"/work/{STEM}.kicad_pcb", "placement": spec["placement"], "outline": spec["outline"],
           "rules": spec["rules"], "zones": [{"net": "GND", "layer": "B.Cu"}], "copper_layers": 2}
    (work / "board.json").write_text(json.dumps(cfg, indent=1))
    rec = run_in_image("build_board", ["python3", "/opt/ae/pcb_ops.py", "build", "board.json"], work)
    rec.outcome = "passed" if rec.exit_code == 0 else "error"
    return rec


def drc(work: Path, pcb: str, out: str) -> tuple[StageRecord, dict]:
    rec = run_in_image("drc", ["kicad-cli", "pcb", "drc", "--schematic-parity", "--format", "json", "--severity-all", "--all-track-errors",
                               "--exit-code-violations", "-o", out, pcb], work)
    path = work / out
    report = json.loads(path.read_text()) if path.exists() else None
    if report is None:
        rec.outcome = "error"
        return rec, {}
    counts = {k: len(report.get(k, [])) for k in ("violations", "unconnected_items", "schematic_parity")}
    rec.outcome = "passed" if sum(counts.values()) == 0 else "failed"
    return rec, {"counts": counts, "report": report}


def route(work: Path, board_in: str, board_out: str) -> list[StageRecord]:
    stages = []
    r = run_in_image("export_dsn", ["python3", "/opt/ae/pcb_ops.py", "dsn", board_in, f"{STEM}.dsn"], work)
    r.outcome = "passed" if r.exit_code == 0 else "error"
    stages.append(r)
    if r.outcome != "passed":
        return stages
    rt = run_router(f"{STEM}.dsn", f"{STEM}.ses", work)
    rt.outcome = "passed" if rt.exit_code == 0 and (work / f"{STEM}.ses").exists() else "error"
    stages.append(rt)
    if rt.outcome != "passed":
        return stages
    s = run_in_image("import_ses_fill", ["python3", "/opt/ae/pcb_ops.py", "ses", board_in, f"{STEM}.ses", board_out], work)
    s.outcome = "passed" if s.exit_code == 0 else "error"
    stages.append(s)
    return stages

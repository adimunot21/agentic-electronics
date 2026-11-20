"""Prove netlist -> board -> DSN -> Freerouting -> SES -> zone fill -> DRC/parity in the pinned image.

Run:  uv run python -m ae.probes.tiny_chain
Prints one line per stage (exit code, wall time, peak RSS) and writes runs/probes/tiny/stages.json.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from ae.probes import tiny_design
from ae.toolchain import REPO_ROOT, StageRecord, run_in_image, run_router, write_records

WORK = REPO_ROOT / "runs" / "probes" / "tiny"
RULES = {"track_width_mm": 0.25, "clearance_mm": 0.2, "via_diameter_mm": 0.6, "via_drill_mm": 0.3, "min_track_mm": 0.15}
PLACEMENT = {"J1": [5.0, 5.0, 0], "R1": [13.0, 6.0, 0], "R2": [19.0, 12.0, 90], "J2": [26.0, 5.0, 0]}
OUTLINE = [0.0, 0.0, 32.0, 20.0]


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    stages: list[StageRecord] = []

    def stage(name: str, cmd: list[str], expect_exit: tuple[int, ...] = (0,)) -> StageRecord:
        rec = run_in_image(name, cmd, WORK)
        rec.outcome = "passed" if rec.exit_code in expect_exit else "failed"
        stages.append(rec)
        print(f"{name:<18} exit={rec.exit_code:<3} {rec.wall_s:>7.2f}s  rss={rec.peak_rss_mb}MB  [{rec.outcome}]")
        return rec

    tiny_design.build("tiny").write(WORK / "tiny.kicad_sch")
    stage("erc", ["kicad-cli", "sch", "erc", "--format", "json", "--severity-all", "-o", "erc.json", "tiny.kicad_sch"])
    stage("netlist", ["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr", "-o", "tiny.net", "tiny.kicad_sch"])
    (WORK / "board.json").write_text(json.dumps({
        "netlist": "/work/tiny.net", "output": "/work/tiny.kicad_pcb", "placement": PLACEMENT, "outline": OUTLINE,
        "rules": RULES, "zones": [{"net": "GND", "layer": "B.Cu"}], "copper_layers": 2}))
    stage("build_board", ["python3", "/opt/ae/pcb_ops.py", "build", "board.json"])
    stage("drc_unrouted", ["kicad-cli", "pcb", "drc", "--schematic-parity", "--format", "json", "--severity-all",
                           "--exit-code-violations", "-o", "drc_unrouted.json", "tiny.kicad_pcb"], expect_exit=(5,))
    stage("export_dsn", ["python3", "/opt/ae/pcb_ops.py", "dsn", "tiny.kicad_pcb", "tiny.dsn"])
    route = run_router("tiny.dsn", "tiny.ses", WORK)
    route.outcome = "passed" if route.exit_code == 0 and (WORK / "tiny.ses").exists() else "failed"
    stages.append(route)
    print(f"{'freerouting':<18} exit={route.exit_code:<3} {route.wall_s:>7.2f}s  rss={route.peak_rss_mb}MB  [{route.outcome}]")
    stage("import_ses_fill", ["python3", "/opt/ae/pcb_ops.py", "ses", "tiny.kicad_pcb", "tiny.ses", "tiny_routed.kicad_pcb"])
    # DRC/parity needs board and schematic to share a stem: publish the routed board under the project name.
    (WORK / "tiny.kicad_pcb").unlink(missing_ok=True)       # created earlier by a uid-1000 container: on a CI runner (uid 1001) the host may not overwrite it, but may replace it (the directory is open)
    shutil.copy(WORK / "tiny_routed.kicad_pcb", WORK / "tiny.kicad_pcb")
    stage("drc_routed", ["kicad-cli", "pcb", "drc", "--schematic-parity", "--format", "json", "--severity-all",
                         "--exit-code-violations", "-o", "drc_routed.json", "tiny.kicad_pcb"])
    write_records(stages, WORK / "stages.json")
    return 0 if all(s.outcome == "passed" for s in stages) else 1


if __name__ == "__main__":
    sys.exit(main())

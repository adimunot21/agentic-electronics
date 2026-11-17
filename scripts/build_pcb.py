"""build -> DRC(unrouted) -> route -> DRC(routed); prints a summary. Usage: build_pcb.py [rundir]"""
import shutil, sys
from pathlib import Path
from ae import pcb
rd = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/ref/dev")
for old in list(rd.glob("fe_aaf_001*.kicad_pcb")) + list(rd.glob("fe_aaf_001*.kicad_prl")): old.unlink()
st = [pcb.build_board(rd, Path("designs/fe-aaf-001/placement.yaml"))]
print(st[0].outcome, st[0].stdout.strip().splitlines()[-1:] , st[0].stderr.strip()[-300:])
if st[0].outcome == "passed":
    shutil.copy(rd / "fe_aaf_001.kicad_pcb", rd / "snapshot_unrouted.kicad_pcb")
    r, d = pcb.drc(rd, "fe_aaf_001.kicad_pcb", "drc_unrouted.json"); print("DRC unrouted:", d.get("counts"))
    st += pcb.route(rd, "fe_aaf_001.kicad_pcb", "fe_aaf_001.kicad_pcb")
    for s in st[1:]: print(f"{s.name:<16}{s.outcome:<8}{s.wall_s:>7.2f}s rss={s.peak_rss_mb}MB")
    router = [s for s in st if s.name == "freerouting"]
    if router: print([l for l in router[0].stdout.splitlines() + router[0].stderr.splitlines() if "unrouted" in l or "finished" in l or "violations" in l][-4:])
    if all(s.outcome == "passed" for s in st):
        r, d = pcb.drc(rd, "fe_aaf_001.kicad_pcb", "drc_routed.json"); print("DRC routed:", d.get("counts"))
        for sect in ("violations", "unconnected_items", "schematic_parity"):
            for v in d["report"][sect][:14]: print(f"  [{sect}] {v['severity']} {v['type']}: {v['description']} | {[i['description'][:48] for i in v['items']][:2]}")

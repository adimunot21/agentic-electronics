"""Run the full ngspice suite on a generated schematic netlist and print the requirement table. Usage: run_sims.py [rundir]"""
import json, sys, time
from pathlib import Path
from ae.sim import SimRun, evaluate_all
from ae.toolchain import write_records
rundir = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/ref/dev")
run = SimRun(work=rundir / "work", netlist=rundir / "fe_aaf_001.net")
t0 = time.monotonic()
ms = evaluate_all(run)
print(f"{len(run.stages)} ngspice runs in {time.monotonic()-t0:.1f}s")
for m in ms:
    v = "-" if m.value is None else f"{m.value:.4g}"
    mg = "" if m.margin is None else f" margin={m.margin:.3g}"
    print(f"{m.req:<13}{m.outcome:<11}{m.metric:<38}{v:>10}  limit {m.limit}{mg}\n{'':13}{m.detail}")
write_records(run.stages, rundir / "sim_stages.json")
(rundir / "sim_results.json").write_text(json.dumps([m.__dict__ for m in ms], indent=2))

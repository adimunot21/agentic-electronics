"""Robustness of REQ-SETTLE for a candidate (RISO, CF): part tolerance corners x ADC input-capacitance/Ron assumptions."""
import itertools, sys
from pathlib import Path
from ae.sim import SimRun, settling_run
riso, cf = float(sys.argv[1]), float(sys.argv[2])
base = Path("runs/ref/dev")
worst = 0.0
print(f"candidate RISO={riso} ohm, CF={cf} pF")
print(f"{'RISO tol':>9} {'CF tol':>7} {'Cs pF':>6} {'Ron':>5} {'|err| mV':>9}")
for rt, ct, cs, ron in itertools.product((-0.01, 0.0, 0.01), (-0.05, 0.0, 0.05), (5.0, 10.0, 15.0), (200.0,)):
    if (rt, ct) not in [(0, 0)] and abs(rt) + abs(ct) == 0.0: continue
    corner = (rt != 0 and ct != 0) or (rt == 0 and ct == 0)       # corners + nominal only (not edge midpoints)
    if not corner: continue
    run = SimRun(work=base / "work_robust", netlist=base / "fe_aaf_001.net", deck_kw={"scale": {"CF": cf / 1000 * (1 + ct), "RISO": riso / 100 * (1 + rt)}})
    run.prepare()
    err, _ = settling_run(run, cs_pf=cs, ron_ohm=ron)
    e = abs(err) if err is not None else float("nan")
    worst = max(worst, e)
    print(f"{rt*100:>8.0f}% {ct*100:>6.0f}% {cs:>6.0f} {ron:>5.0f} {e:>9.3f}")
print(f"WORST |err| = {worst:.3f} mV (limit 0.403)")

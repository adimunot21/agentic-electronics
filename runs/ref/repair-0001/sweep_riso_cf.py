"""Design-space probe for REQ-SETTLE: vary RISO and CF jointly (test-bench scaling; schematic untouched)."""
from pathlib import Path
from ae.sim import SimRun, settling_run
base = Path("runs/ref/dev")
cfs = (330, 470, 1000, 2200)
print(f"{'RISO':>6} " + " ".join(f"{c:>7}pF" for c in cfs) + "   (|acquisition error| mV; limit 0.403)")
for riso in (100, 220, 330, 470, 680, 1000):
    row = []
    for cf in cfs:
        run = SimRun(work=base / "work_sweep", netlist=base / "fe_aaf_001.net", deck_kw={"scale": {"CF": cf / 1000.0, "RISO": riso / 100.0}})
        run.prepare()
        err, _ = settling_run(run)
        row.append("   blocked" if err is None else f"{abs(err):>9.3f}")
    print(f"{riso:>5}R " + " ".join(row))

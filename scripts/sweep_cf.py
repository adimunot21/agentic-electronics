"""Design-space probe for the REQ-SETTLE failure: vary CF (test-bench scaling only, schematic untouched)."""
import numpy as np
from pathlib import Path
from ae.sim import SimRun, ac_run, load_ideal, settling_run
base = Path("runs/ref/dev")
ideal = load_ideal()
print(f"{'CF':>7} {'settle err mV':>14} {'stopband dB':>12} {'peaking dB':>11} {'flat dB':>8}")
for cf_pf in (1000, 470, 330, 220, 150, 100):
    run = SimRun(work=base / f"work_cf{cf_pf}", netlist=base / "fe_aaf_001.net", deck_kw={"scale": {"C5": cf_pf / 1000.0}})
    run.prepare()
    err, _ = settling_run(run)
    ac = ac_run(run, ideal, 100.0, "rs100")
    print(f"{cf_pf:>5}pF {abs(err):>14.4f} {ac['stopband_abs_gain_dB_max_19k_to_1M']:>12.2f} {ac['peaking_above_ideal_dB_max']:>11.2f} {ac['passband_flatness_dB_max_abs']:>8.3f}")

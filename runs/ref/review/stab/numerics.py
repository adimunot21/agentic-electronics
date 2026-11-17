"""Is the -128..-172 dB stopband content a numerical artefact or a real small-signal response? Vary solver options and compare."""
from pathlib import Path
import numpy as np
from ae import sim_metrics as sm
from ae.sim import SimRun, header
base = Path("runs/ref/baseline"); out = Path("runs/ref/review/stab")
pts = [1e5, 2.66e5, 5e5, 1e6, 3e6, 1e7]
res = {}
for tag, opt in {"default": "", "gmin1e-15": ".options gmin=1e-15", "gmin1e-9": ".options gmin=1e-9", "tight": ".options reltol=1e-6 abstol=1e-15 vntol=1e-12 gmin=1e-15"}.items():
    run = SimRun(work=out / "work_num", netlist=base / "fe_aaf_001.net"); run.prepare()
    tb = header(rs=100.0, src="DC 0 AC 1") + opt + "\n.control\nop\nac dec 40 10 10Meg\nwrdata ac_num.csv v(src) v(aout)\n.endc\n.end\n"
    rec = run.ng(f"ac_{tag}", tb.replace("ac_num.csv", f"ac_{tag}.csv"))
    f, (vs, vo) = sm.read_wrdata(run.work / f"ac_{tag}.csv", 2, True)
    res[tag] = [round(float(np.interp(np.log10(p), np.log10(f), 20 * np.log10(np.abs(vo / vs)))), 1) for p in pts]
print("freq Hz      ", pts)
for k, v in res.items(): print(f"{k:<10}", v)
# S2 curve vs baseline vs ideal around the flagged region
d0 = np.genfromtxt(out / "S0_baseline_curve.csv", delimiter=",", names=True)
print("\nS0 baseline sim vs ideal (dB):")
for p in (1e3, 1e4, 3e4, 1e5, 3e5, 1e6): 
    i = int(np.argmin(abs(d0['f_Hz'] - p))); print(f"  {d0['f_Hz'][i]:>9.0f} Hz sim {d0['sim_dB'][i]:>8.1f} ideal {d0['ideal_dB'][i]:>8.1f}")
f, (vs, va, vo) = sm.read_wrdata(out / "work_S2/ac.csv", 3, True); g = 20 * np.log10(abs(vo / vs))
print("S2 (R6=0.0475 ohm, C5=330 nF) gain dB:", {int(p): round(float(np.interp(np.log10(p), np.log10(f), g)), 1) for p in (1e3, 1e4, 2e4, 3e4, 5.6e4, 1e5, 2e5, 1e6)})

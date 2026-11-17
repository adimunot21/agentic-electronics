"""Timestep sensitivity of the REQ-SETTLE measurement on the FINAL design (R6=475, C5=330p): suite setting (tmax 1 us) vs fine (5 ns)."""
from pathlib import Path
import numpy as np
from ae import sim_metrics as sm
from ae.sim import SimRun, header, settling_run
base = Path("runs/ref/baseline"); run = SimRun(work=Path("runs/ref/review/work_settle"), netlist=base / "fe_aaf_001.net"); run.prepare()
e, note = settling_run(run); print("suite measurement (tmax 1us): %.4f mV | %s" % (e, note))
t, (v,) = sm.read_wrdata(run.work / "ref.csv", 1, False)
w = (t >= 3e-3) & (t <= 4e-3); tp = float(t[w][np.argmax(v[w])]); vmin = float(np.min(v[w]))
for tmax in ("1u", "100n"):   # 5n was attempted and BLOCKED by the 60 s limit (needs ~700k steps from t=0)
    tb = (header(src="DC 0 SIN(0 1.0 1000)") + ".model SWM SW(Ron=200 Roff=1G Vt=0.5 Vh=0.05)\nSsamp AOUT cs ctl 0 SWM\nCs cs 0 10p\n"
          f"Vctl ctl 0 PULSE(0 1 {tp:.9e} 1n 1n 1u 1)\n.ic v(cs)={vmin:.6f}\n.control\ntran {tmax} {tp+3e-6:.9e} {tp-1e-6:.9e} {tmax}\nwrdata d_{tmax}.csv v(cs) v(aout)\n.endc\n.end\n")
    run.ng(f"d_{tmax}", tb)
    raw = np.loadtxt(run.work / f"d_{tmax}.csv"); tt, cs, ao = raw[:, 0], raw[:, 1], raw[:, 3]
    ref_at = np.interp(tp + 1e-6, t, v)
    print(f"tmax={tmax:>5}: |V_cs(t+1us) - undisturbed AOUT| = {abs(np.interp(tp+1e-6, tt, cs) - ref_at)*1e3:.4f} mV  ({len(tt)} points); worst |AOUT deviation| during window = {np.max(np.abs(np.interp(np.linspace(tp, tp+2e-6, 400), tt, ao) - np.interp(np.linspace(tp, tp+2e-6, 400), t, v)))*1e3:.1f} mV")
raw = np.loadtxt(run.work / "d_100n.csv"); tt, cs, ao = raw[:, 0], raw[:, 1], raw[:, 3]
print("final design waveform, deviation of AOUT from undisturbed (mV) / V_cs error (mV):")
for dt in (0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5):
    x = tp + dt * 1e-6; r = np.interp(x, t, v); print(f"  +{dt:>4.2f} us  AOUT {np.interp(x,tt,ao)-r:+9.4f} V   Vcs {np.interp(x,tt,cs)-r:+9.4f} V")

"""One-off review evidence for REQ-STAB: ORIGINAL (rev 2, no floor) vs REVISED (rev 3, ideal floored at -90 dB) on real-model AC sweeps."""
import json, sys
from pathlib import Path
import numpy as np
from ae import sim_metrics as sm
from ae.sim import SimRun, header, load_ideal
base = Path("runs/ref/baseline"); out = Path("runs/ref/review/stab"); ideal = load_ideal()
cases = {"S0_baseline": {}, "S1_R6=0.475ohm_C5=99nF": {"R6": 0.001, "C5": 300.0}, "S2_R6=0.0475ohm_C5=330nF": {"R6": 0.0001, "C5": 1000.0},
         "S3_R6=0.475ohm_C5=3.3nF": {"R6": 0.001, "C5": 10.0}, "S4_R6=4.75ohm_C5=10nF": {"R6": 0.01, "C5": 30.0}}
rows = {}
for name, scale in cases.items():
    run = SimRun(work=out / f"work_{name[:2]}", netlist=base / "fe_aaf_001.net", deck_kw={"scale": scale} if scale else {}); run.prepare()
    tb = header(rs=100.0, src="DC 0 AC 1") + ".control\nop\nac dec 40 10 10Meg\nwrdata ac.csv v(src) v(ain) v(aout)\n.endc\n.end\n"
    rec = run.ng("ac", tb)
    f, (vs, va, vo) = sm.read_wrdata(run.work / "ac.csv", 3, True)
    g = 20 * np.log10(np.abs(vo / vs)); idl = 20 * np.log10(np.abs(ideal(f, 100.0)))
    orig = g - idl; rev = g - np.maximum(idl, -90.0)
    io, ir = int(np.argmax(orig)), int(np.argmax(rev))
    hf = f >= 1e6
    rows[name] = {"run_outcome": rec.outcome, "ORIGINAL_max_excess_dB": round(float(orig[io]), 2), "orig_at_Hz": round(float(f[io])), "orig_sim_dB": round(float(g[io]), 1), "orig_ideal_dB": round(float(idl[io]), 1),
                  "REVISED_max_excess_dB": round(float(rev[ir]), 2), "rev_at_Hz": round(float(f[ir])), "rev_sim_dB": round(float(g[ir]), 1),
                  "max_abs_gain_dB_1M_to_10M": round(float(g[hf].max()), 1), "at_Hz": round(float(f[hf][np.argmax(g[hf])]))}
    if name == "S0_baseline":
        rows[name]["freqs_where_ORIGINAL_exceeds_3dB"] = [round(float(f[0] if False else x)) for x in f[orig > 3][[0, -1]]]
        rows[name]["sim_dB_range_where_ORIGINAL_exceeds_3dB"] = [round(float(g[orig > 3].min()), 1), round(float(g[orig > 3].max()), 1)]
        rows[name]["lowest_f_with_sim_below_-150dB"] = round(float(f[g < -150][0])) if (g < -150).any() else None
        np.savetxt(out / "S0_baseline_curve.csv", np.c_[f, g, idl], delimiter=",", header="f_Hz,sim_dB,ideal_dB", comments="")
for k, v in rows.items():
    print(k, json.dumps(v))
(out / "stab_cases.json").write_text(json.dumps(rows, indent=1))

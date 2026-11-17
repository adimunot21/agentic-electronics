"""How much effective-capacitance LOSS can each role tolerate? (X7R loses capacitance under DC bias; the Kemet curves were NOT obtained, so this brackets the need.)
Informational: scales one capacitor, everything else nominal (C4 also at the worst-flatness corner). No new limits."""
import json
from pathlib import Path
import numpy as np
from ae import sim_metrics as sm
from ae.sim import SimRun, ac_run, header, load_ideal, noise_run
NET = Path("runs/ref/baseline/fe_aaf_001.net"); OUT = Path("runs/ref/review/capbias"); ideal = load_ideal(NET)
corner = json.load(open("runs/ref/baseline/corners_summary.json"))["worst_flat"]["corner"]
SPAN = {"R1": (.99, 1.01), "R2": (.99, 1.01), "R3": (.99, 1.01), "R4": (.99, 1.01), "R5": (.99, 1.01), "C1": (.95, 1.05), "C2": (.95, 1.05), "C3": (.95, 1.05), "C4": (.6, 1.2), "Rs": (0.01, 100.0)}
worst = {k: SPAN[k][0 if corner[k] == "lo" else 1] for k in SPAN if k not in ("C4", "Rs")}
res = {"C4": [], "C6": []}
print("C4 (2.2 uF X7R 16 V coupling) loss vs flatness [limit 0.5 dB] and Zin [>=10 kohm], Rs=100:")
print(f"{'C4 eff / nominal':>17}{'flat nominal':>14}{'flat @worst LP corner':>23}{'Zin min kohm':>14}")
for s in (1.2, 1.0, 0.6, 0.3, 0.15, 0.10, 0.06, 0.04, 0.02):
    row = {"scale": s}
    for label, kw in (("nom", {"C4": s}), ("worst", {**worst, "C4": s})):
        run = SimRun(work=OUT / f"w_c4_{label}", netlist=NET, deck_kw={"scale": kw}); run.prepare()
        m = ac_run(run, ideal, 100.0, "r")
        row[label] = m["passband_flatness_dB_max_abs"]; row["zin"] = m["zin_min_ohm"]
    res["C4"].append(row); print(f"{s:>17.2f}{row['nom']:>14.3f}{row['worst']:>23.3f}{row['zin']/1e3:>14.1f}")
print("\nC6 (10 uF VMID decoupling) loss vs integrated noise [limit 0.2 mV rms] and VREF-to-AOUT coupling (informational: no VREF-ripple requirement exists):")
print(f"{'C6 eff / nominal':>17}{'noise mV rms':>14}{'VREF->AOUT dB @50Hz':>21}{'@1kHz':>9}")
for s in (1.0, 0.5, 0.2, 0.1, 0.02, 0.001):
    run = SimRun(work=OUT / "w_c6", netlist=NET, deck_kw={"scale": {"C6": s}}); run.prepare()
    n = noise_run(run)
    tb = header(src="DC 0") .replace("Vref VREF 0 DC 3.3", "Vref VREF 0 DC 3.3 AC 1") + ".control\nop\nac dec 20 10 10k\nwrdata vr.csv v(aout)\n.endc\n.end\n"
    run.ng("vref_ac", tb)
    f, (vo,) = sm.read_wrdata(run.work / "vr.csv", 1, True)
    g = lambda x: float(20 * np.log10(abs(np.interp(np.log10(x), np.log10(f), np.abs(vo)))))
    row = {"scale": s, "noise_mV": n * 1e3, "vref_to_aout_dB_50Hz": g(50), "vref_to_aout_dB_1k": g(1000)}
    res["C6"].append(row); print(f"{s:>17.3f}{n*1e3:>14.4f}{row['vref_to_aout_dB_50Hz']:>21.1f}{row['vref_to_aout_dB_1k']:>9.1f}")
(OUT / "capbias_results.json").write_text(json.dumps(res, indent=1, default=float))

"""All 1024 tolerance-box corners with the REAL op-amp model (ngspice AC), on the FINAL design netlist. Optional argv[1]=netlist, argv[2]=out dir."""
import itertools, json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, "runs/ref/review/corner")
from corner_search import names, span
from ae import sim_metrics as sm
from ae.sim import SimRun, header, load_ideal
from ae.toolchain import run_in_image
net = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/ref/baseline/fe_aaf_001.net"); out = Path(sys.argv[2] if len(sys.argv) > 2 else "runs/ref/review/corner/ng_baseline")
work = out / "work"; sim = SimRun(work=work, netlist=net); sim.prepare()
corners = list(itertools.product((0, 1), repeat=len(names)))
for i, combo in enumerate(corners):
    v = {n: span[n][c] for n, c in zip(names, combo)}
    scale = {k: v[k] for k in ("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3")}; scale["C4"] = v["C4"]
    (work / f"k{i}_dut.cir").write_text(sim.deck(scale=scale))
    (work / f"k{i}.cir").write_text(header(rs=v["Rs"], src="DC 0 AC 1").replace("@DUT@", f"k{i}_dut.cir") + f".control\nop\nac dec 20 10 10Meg\nwrdata k{i}.csv v(src) v(ain) v(aout)\n.endc\n.end\n")
rec = run_in_image("corners", ["bash", "-c", f"seq 0 {len(corners)-1} | xargs -P 10 -I{{}} sh -c 'timeout 60 ngspice -b -o k{{}}.log k{{}}.cir >/dev/null 2>&1; true'"], work, timeout_s=1800)
ideal = load_ideal(); rows = []
for i, combo in enumerate(corners):
    v = {n: span[n][c] for n, c in zip(names, combo)}
    p = work / f"k{i}.csv"
    if not p.exists() or "Transient op" in (work / f"k{i}.log").read_text(): rows.append({"i": i, "blocked": True}); continue
    f, (vs, va, vo) = sm.read_wrdata(p, 3, True)
    m = sm.ac_metrics(f, vs, va, vo, rs_ohm=v["Rs"], ideal_mag=np.abs(ideal(f, v["Rs"])))
    rows.append({"i": i, "corner": {n: ("lo" if c == 0 else "hi") for n, c in zip(names, combo)}, "flat_dB": m["passband_flatness_dB_max_abs"], "gain400": m["gain_400hz_V_per_V"], "stop_dB": m["stopband_abs_gain_dB_max_19k_to_1M"], "zin": m["zin_min_ohm"]})
ok = [r for r in rows if "flat_dB" in r]; ok.sort(key=lambda r: -r["flat_dB"])
summ = {"corners": len(corners), "blocked": len(rows) - len(ok), "flat_fail_gt_0.5": sum(r["flat_dB"] > 0.5 for r in ok), "flat_worst": ok[0]["flat_dB"], "flat_worst_corner": ok[0]["corner"],
        "gain_range": [min(r["gain400"] for r in ok), max(r["gain400"] for r in ok)], "gain_fail_outside_1.235_1.365": sum(not (1.235 <= r["gain400"] <= 1.365) for r in ok),
        "stop_worst_dB": max(r["stop_dB"] for r in ok), "stop_fail_gt_-55.8": sum(r["stop_dB"] > -55.8 for r in ok), "zin_min": min(r["zin"] for r in ok), "wall_s": round(rec.wall_s, 1)}
(out / "corners.json").write_text(json.dumps({"summary": summ, "top": ok[:10]}, indent=1)); print(json.dumps(summ, indent=1))

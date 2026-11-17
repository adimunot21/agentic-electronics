"""Full-precision gain / alias / flatness for one design, nominal parts and ALL 1024 tolerance corners, identical conditions.
usage: before_after.py LABEL NETLIST OUTDIR"""
import json, sys
from pathlib import Path
import numpy as np
from ae.mc import run_corners
from ae.sim import SimRun, ac_run, load_ideal
label, net, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
GAIN_LO, GAIN_HI, ALIAS, FLAT = 1.235, 1.365, -55.8, 0.5
res = {"label": label, "netlist": str(net), "limits": {"gain_400Hz_V_per_V": [GAIN_LO, GAIN_HI], "alias_dB_abs_at_19kHz_and_above": ALIAS, "flatness_dB": FLAT}, "nominal": {}}
sim = SimRun(work=out / "work_nom", netlist=net); sim.prepare(); ideal = load_ideal(net)
for rs, tag in ((100.0, "Rs=100"), (0.01, "Rs=0.01")):
    m = ac_run(sim, ideal, rs, tag.replace("=", ""))
    g = m["gain_400hz_V_per_V"]
    res["nominal"][tag] = {"gain_400Hz": g, "gain_margin_lo": g - GAIN_LO, "gain_margin_hi": GAIN_HI - g, "alias_19kHz_exact_dB": m["gain_at_exactly_19kHz_dB"], "alias_worst_19k_to_1M_dB": m["stopband_abs_gain_dB_max_19k_to_1M"],
                          "alias_margin_dB": ALIAS - m["stopband_abs_gain_dB_max_19k_to_1M"], "flatness_dB": m["passband_flatness_dB_max_abs"], "flatness_margin_dB": FLAT - m["passband_flatness_dB_max_abs"]}
cr = run_corners(out, net); rows = cr["rows"]
gmin = min(rows, key=lambda r: r["gain_400hz_V_per_V"]); gmax = max(rows, key=lambda r: r["gain_400hz_V_per_V"])
aw = max(rows, key=lambda r: r["stopband_abs_gain_dB_max_19k_to_1M"]); fw = max(rows, key=lambda r: r["passband_flatness_dB_max_abs"])
res["corners"] = {"n": cr["n"], "completed": cr["completed"], "gain_min": gmin["gain_400hz_V_per_V"], "gain_min_margin": gmin["gain_400hz_V_per_V"] - GAIN_LO, "gain_max": gmax["gain_400hz_V_per_V"], "gain_max_margin": GAIN_HI - gmax["gain_400hz_V_per_V"],
                  "alias_worst_dB": aw["stopband_abs_gain_dB_max_19k_to_1M"], "alias_margin_dB": ALIAS - aw["stopband_abs_gain_dB_max_19k_to_1M"], "alias_worst_corner": aw["corner"],
                  "flat_worst_dB": fw["passband_flatness_dB_max_abs"], "flat_margin_dB": FLAT - fw["passband_flatness_dB_max_abs"], "flat_fail_count": cr["flat_fail"], "gain_fail_count": cr["gain_fail"], "alias_fail_count": cr["stop_fail"], "flat_worst_corner": fw["corner"]}
(out / "before_after.json").write_text(json.dumps(res, indent=1, default=float)); print(json.dumps(res["nominal"], indent=1)); print(json.dumps({k: v for k, v in res["corners"].items() if "corner" not in k}, indent=1))

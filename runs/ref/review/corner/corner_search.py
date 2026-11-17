"""Worst-case TOLERANCE CORNERS for REQ-FLAT / REQ-GAIN / REQ-ALIAS inside the contract's specified tolerances.
Vertices of the box: R1-R5 +-1 %, C1/C2/C3 +-5 %, C4 x[0.6,1.2], Rs {0,100} -> 2^10 = 1024 corners; ideal op-amps first (fast), then ngspice real-model verification of the worst."""
import importlib.util, itertools, json
from pathlib import Path
import numpy as np
spec = importlib.util.spec_from_file_location("df", "analysis/derive_filter.py"); df = importlib.util.module_from_spec(spec); spec.loader.exec_module(df)
P = json.load(open("analysis/filter_design.json"))["params"]
f_band = np.geomspace(50, 1000, 120); f_stop = np.geomspace(19e3, 1e6, 60)
names = ["R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3", "C4", "Rs"]
span = {"R1": (.99, 1.01), "R2": (.99, 1.01), "R3": (.99, 1.01), "R4": (.99, 1.01), "R5": (.99, 1.01), "C1": (.95, 1.05), "C2": (.95, 1.05), "C3": (.95, 1.05), "C4": (.6, 1.2), "Rs": (0.01, 100.0)}
def evaluate(vals, p0=P):
    q = dict(p0)
    for k in ("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3"): q[k] = p0[k] * vals[k]
    cin, rs = vals["C4"], vals["Rs"]
    g400 = abs(df.ideal_total_response([400.0], q, rs=rs, cin_scale=cin)[0])
    flat = 20 * np.log10(np.abs(df.ideal_total_response(f_band, q, rs=rs, cin_scale=cin)) / g400)
    stop = 20 * np.log10(np.abs(df.ideal_total_response(f_stop, q, rs=rs, cin_scale=cin)).max())
    return float(np.max(np.abs(flat))), float(g400), float(stop), float(flat[np.argmax(np.abs(flat))]), float(f_band[np.argmax(np.abs(flat))])
if __name__ == "__main__":
    rows = []
    for combo in itertools.product((0, 1), repeat=len(names)):
        vals = {n: span[n][c] for n, c in zip(names, combo)}
        fl, g, st, sgn, fa = evaluate(vals)
        rows.append({"corner": {n: ("lo" if c == 0 else "hi") for n, c in zip(names, combo)}, "vals": vals, "flat_dB": fl, "gain400": g, "stop_dB": st, "worst_at_Hz": fa, "signed_dB": sgn})
    rows.sort(key=lambda r: -r["flat_dB"])
    nominal = evaluate({n: 1.0 if n != "Rs" else 100.0 for n in names})
    print("nominal (Rs=100): flat %.3f dB gain %.4f stop %.1f dB" % (nominal[0], nominal[1], nominal[2]))
    print("corners failing flatness > 0.5 dB:", sum(r["flat_dB"] > 0.5 for r in rows), "of", len(rows), "| gain range [%.4f, %.4f] | worst stopband %.1f dB" % (min(r["gain400"] for r in rows), max(r["gain400"] for r in rows), max(r["stop_dB"] for r in rows)))
    for r in rows[:6]: print("  flat %.3f dB at %4.0f Hz (%+.3f) gain %.4f stop %.1f |" % (r["flat_dB"], r["worst_at_Hz"], r["signed_dB"], r["gain400"], r["stop_dB"]), " ".join(f"{k}:{v}" for k, v in r["corner"].items()))
    json.dump(rows[:20], open("runs/ref/review/corner/ideal_top20.json", "w"), indent=1)

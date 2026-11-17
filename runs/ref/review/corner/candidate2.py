import itertools, json, sys
import numpy as np
sys.path.insert(0, "runs/ref/review/corner")
from corner_search import df, P
from fix_scan import corners_worst
K = 1.08; fc = 1601.6 * K; freqs = np.geomspace(100, 30000, 120); tgt = np.abs(df.target_response(freqs, fc, 1.30))
nb = {r: df.nearest_e96_neighbours(P[r] / K, 2) for r in ("R1", "R2", "R3", "R4")}
cands = []
for r1, r2, r3, r4 in itertools.product(nb["R1"], nb["R2"], nb["R3"], nb["R4"]):
    q = dict(P); q.update(R1=r1, R2=r2, R3=r3, R4=r4, R5=r4)
    e = float(np.max(np.abs(20 * np.log10(np.abs(df.ideal_total_response(freqs, q, rs=0.0)) / tgt))))
    if e < 0.2: cands.append((e, r1, r2, r3, r4))
cands.sort(); print(len(cands), "candidates with nominal error < 0.2 dB; evaluating all 1024 corners for the best 12")
res = []
for e, r1, r2, r3, r4 in cands[:12]:
    q = dict(P); q.update(R1=r1, R2=r2, R3=r3, R4=r4, R5=r4)
    fl, g0, g1, st = corners_worst(q)
    mg = {"flat": (0.5 - fl) / 0.5, "gain_hi": (1.365 - g1) / 1.3, "gain_lo": (g0 - 1.235) / 1.3, "stop": (-55.8 - st) / 10}
    res.append((min(mg.values()), e, r1, r2, r3, r4, fl, g0, g1, st))
    print(f"  nom {e:.3f} dB  R1={r1:.4g} R2={r2:.4g} R3={r3:.4g} R4=R5={r4:.4g} | worst flat {fl:.3f} gain {g0:.4f}..{g1:.4f} stop {st:.1f} | min-margin {min(mg.values()):.4f}")
res.sort(reverse=True); b = res[0]
json.dump({"k": K, "R1": b[2], "R2": b[3], "R3": b[4], "R4": b[5], "R5": b[5], "nominal_err_dB": b[1], "ideal_corners": {"flat": b[6], "gain_min": b[7], "gain_max": b[8], "stop": b[9]}}, open("runs/ref/review/corner/candidate.json", "w"), indent=1)
print("CHOSEN:", {k: b[i] for k, i in (("R1", 2), ("R2", 3), ("R3", 4), ("R4=R5", 5))})

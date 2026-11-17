"""Does another E96 selection give more gain-window margin while keeping flatness/alias? Ideal-op-amp model (estimate only), all 1024 vertices."""
import importlib.util, itertools, json, sys
import numpy as np
sys.path.insert(0, "runs/ref/review/corner"); sys.path.insert(0, "runs/ref/review/gainmargin")
from corner_search import df, names, span
from decompose import P, g400, SPAN
from fix_scan import corners_worst
f_nom = np.geomspace(100, 30000, 100)
cands = {}
for K in (1.06, 1.08, 1.10):
    fc = 1601.6 * K; tgt = np.abs(df.target_response(f_nom, fc, 1.30))
    nb = {r: df.nearest_e96_neighbours(P[r] * 1.08 / K * (1 / 1.08) if False else P["R1" if r=="R1" else r] , 3) for r in ("R1", "R2", "R3", "R4")}
    base = {"R1": 64900.0, "R2": 84500.0, "R3": 6490.0, "R4": 17800.0}      # original derivation values, scaled by 1/K
    nb = {r: df.nearest_e96_neighbours(base[r] / K, 3) for r in base}
    for r1, r2, r3, r4 in itertools.product(nb["R1"], nb["R2"], nb["R3"], nb["R4"]):
        q = dict(P); q.update(R1=r1, R2=r2, R3=r3, R4=r4, R5=r4)
        resp = np.abs(df.ideal_total_response(f_nom, q, rs=0.0)); e = float(np.max(np.abs(20 * np.log10(resp / tgt))))
        g0 = abs(df.ideal_total_response([400.0], q, rs=100.0)[0])
        if e < 0.25 and 1.292 < g0 < 1.306: cands[(r1, r2, r3, r4)] = (e, g0, K)
print(len(cands), "candidates (nominal fit < 0.25 dB, nominal gain 1.292..1.306)")
res = []
for (r1, r2, r3, r4), (e, g0, K) in cands.items():
    q = dict(P); q.update(R1=r1, R2=r2, R3=r3, R4=r4, R5=r4)
    gs = [abs(df.ideal_total_response([400.0], {**q, **{k: q[k] * v for k, v in zip(("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3"), combo[:8])}}, rs=combo[9], cin_scale=combo[8])[0])
          for combo in itertools.product(*[span[n] for n in names])]
    res.append((min(min(gs) - 1.235, 1.365 - max(gs)), r1, r2, r3, r4, g0, K, min(gs), max(gs)))
res.sort(reverse=True)
print("top by min gain margin (ideal model):")
for r in res[:8]: print(f"  gain-margin {r[0]:+.5f}  R1={r[1]:.5g} R2={r[2]:.5g} R3={r[3]:.5g} R4=R5={r[4]:.5g}  nominal {r[5]:.5f}  corners {r[7]:.5f}..{r[8]:.5f}  (k={r[6]})")
print("\nfull flatness/alias corners for the best 4 + current adopted:")
for r in res[:4] + [(None, 59000.0, 76800.0, 6190.0, 16500.0, 0, 1.08, 0, 0)]:
    q = dict(P); q.update(R1=r[1], R2=r[2], R3=r[3], R4=r[4], R5=r[4]); fl, g0, g1, st = corners_worst(q)
    print(f"  R1={r[1]:.5g} R2={r[2]:.5g} R3={r[3]:.5g} R4=R5={r[4]:.5g}: worst flat {fl:.3f} (margin {0.5-fl:+.3f}) gain {g0:.5f}..{g1:.5f} (margins {g0-1.235:+.5f}/{1.365-g1:+.5f}) stop {st:.2f} (margin {-55.8-st:+.2f})")

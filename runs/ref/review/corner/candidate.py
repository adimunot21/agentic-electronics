"""Snap the k=1.08 correction to E96 and verify: nominal error vs target, ideal 1024 corners, then ngspice (real model) 1024 corners. Limits unchanged."""
import itertools, json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, "runs/ref/review/corner")
from corner_search import df, P, names, span, evaluate
from fix_scan import corners_worst
K = float(sys.argv[1]) if len(sys.argv) > 1 else 1.08
fc = 1601.6 * K; freqs = np.geomspace(100, 30000, 120)
tgt = np.abs(df.target_response(freqs, fc, 1.30))
best = []
nb = {r: df.nearest_e96_neighbours(P[r] / K, 2) for r in ("R1", "R2", "R3", "R4")}
for r1, r2, r3, r4 in itertools.product(nb["R1"], nb["R2"], nb["R3"], nb["R4"]):
    q = dict(P); q.update(R1=r1, R2=r2, R3=r3, R4=r4, R5=r4)
    resp = np.abs(df.ideal_total_response(freqs, q, rs=0.0))
    best.append((float(np.max(np.abs(20 * np.log10(resp / tgt)))), r1, r2, r3, r4))
best.sort()
print("top E96 candidates (max |error| dB vs ideal Butterworth at fc=%.0f Hz, gain 1.30):" % fc)
for b in best[:4]: print("  %.3f dB  R1=%.4g R2=%.4g R3=%.4g R4=R5=%.4g   (R2/R1=%.4f)" % (b[0], *b[1:], b[2] / b[1]))
_, r1, r2, r3, r4 = best[0]
q = dict(P); q.update(R1=r1, R2=r2, R3=r3, R4=r4, R5=r4)
fl, g0, g1, st = corners_worst(q)
print("ideal 1024 corners, chosen set: worst flat %.3f dB | gain %.4f..%.4f | worst stop %.1f dB" % (fl, g0, g1, st))
json.dump({"k": K, "R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r4, "ideal_worst": [fl, g0, g1, st]}, open("runs/ref/review/corner/candidate.json", "w"), indent=1)

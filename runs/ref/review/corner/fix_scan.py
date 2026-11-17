"""Smallest correction scan: scale the five filter resistors by 1/k (cutoff x k, gain ratios kept), evaluate ALL 1024 corners on the ideal model.
Constraints (limits unchanged): flatness <= 0.5 dB, gain 1.235..1.365, alias <= -55.8 dB (the last two use the ideal model here; ngspice verification follows)."""
import itertools, json, sys
import numpy as np
sys.path.insert(0, "runs/ref/review/corner")
from corner_search import df, P, names, span, evaluate
def corners_worst(p0):
    fl, g, st = [], [], []
    for combo in itertools.product((0, 1), repeat=len(names)):
        vals = {n: span[n][c] for n, c in zip(names, combo)}
        a, b, c, *_ = evaluate(vals, p0); fl.append(a); g.append(b); st.append(c)
    return max(fl), min(g), max(g), max(st)
if __name__ == "__main__":
    print(f"{'k':>6} {'fc Hz':>7} {'worst flat dB':>14} {'gain min..max':>16} {'worst stop dB':>14}")
    for k in (1.00, 1.03, 1.05, 1.06, 1.07, 1.08, 1.09, 1.10, 1.12):
        q = dict(P)
        for r in ("R1", "R2", "R3", "R4", "R5"): q[r] = P[r] / k
        fl, g0, g1, st = corners_worst(q)
        print(f"{k:>6.2f} {1601.6*k:>7.0f} {fl:>14.3f} {g0:>8.4f}..{g1:<7.4f} {st:>14.1f}   {'OK' if fl<=0.5 and st<=-55.8 and 1.235<=g0 and g1<=1.365 else ''}")

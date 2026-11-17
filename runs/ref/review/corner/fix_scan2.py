"""Compare correction options by worst-case flatness / alias over all 1024 corners (ideal model): (a) stage-2 pole only, (b) stage-1 only, (c) all five resistors."""
import sys
sys.path.insert(0, "runs/ref/review/corner")
from fix_scan import corners_worst, P
opts = {"(a) R4=R5 only (stage-2 pole)": ("R4", "R5"), "(b) R1,R2,R3 (stage 1)": ("R1", "R2", "R3"), "(c) all five R": ("R1", "R2", "R3", "R4", "R5")}
for name, rs in opts.items():
    print(name)
    for k in (1.04, 1.08, 1.12, 1.18, 1.25):
        q = dict(P)
        for r in rs: q[r] = P[r] / k
        fl, g0, g1, st = corners_worst(q)
        print(f"   k={k:<5} worst flat {fl:.3f}  gain {g0:.4f}..{g1:.4f}  stop {st:.1f} {'OK' if fl<=0.5 and st<=-55.8 and 1.235<=g0 and g1<=1.365 else ''}")

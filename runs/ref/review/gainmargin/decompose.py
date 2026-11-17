"""Where does the 400 Hz gain-corner spread of the ADOPTED design come from? Ideal-op-amp model, nominal Rs=100; vertex subsets of the contract tolerance box."""
import importlib.util, itertools, sys
import numpy as np
from pathlib import Path
from ae.netlist import read_netlist
from ae.spice import parse_si
spec = importlib.util.spec_from_file_location("df", "analysis/derive_filter.py"); df = importlib.util.module_from_spec(spec); spec.loader.exec_module(df)
V = {c["ref"]: parse_si(c["value"]) for c in read_netlist(Path("runs/ref/baseline/fe_aaf_001.net"))[0] if c["ref"][0] in "RC"}
P = {"R1": V["R1"], "R2": V["R2"], "R3": V["R3"], "R4": V["R4"], "R5": V["R5"], "C1": V["C1"], "C2": V["C2"], "C3": V["C3"], "Cin": V["C4"]}
SPAN = {"R1": (.99, 1.01), "R2": (.99, 1.01), "R3": (.99, 1.01), "R4": (.99, 1.01), "R5": (.99, 1.01), "C1": (.95, 1.05), "C2": (.95, 1.05), "C3": (.95, 1.05), "C4": (.6, 1.2), "Rs": (0.01, 100.0)}
def g400(vals):
    q = dict(P)
    for k in ("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3"): q[k] = P[k] * vals.get(k, 1.0)
    return abs(df.ideal_total_response([400.0], q, rs=vals.get("Rs", 100.0), cin_scale=vals.get("C4", 1.0))[0])
nom = g400({}); print(f"ideal nominal gain @400 Hz (Rs=100): {nom:.6f}")
subsets = {"ratios only: R1,R2,R4,R5": ("R1", "R2", "R4", "R5"), "+ R3": ("R1", "R2", "R3", "R4", "R5"), "caps only: C1,C2,C3": ("C1", "C2", "C3"), "Rs and C4 only": ("C4", "Rs"),
           "all resistors": ("R1", "R2", "R3", "R4", "R5"), "everything (full box)": tuple(SPAN)}
print(f"{'subset':<28}{'min':>10}{'max':>10}{'min dev %':>11}{'max dev %':>11}")
for name, keys in subsets.items():
    vals_all = [g400(dict(zip(keys, combo))) for combo in itertools.product(*[SPAN[k] for k in keys])]
    print(f"{name:<28}{min(vals_all):>10.5f}{max(vals_all):>10.5f}{100*(min(vals_all)/nom-1):>11.3f}{100*(max(vals_all)/nom-1):>11.3f}")
print(f"analytic bound for two +-1 % ratios R2/R1 and R5/R4: (1.01/0.99)^2-1 = +{100*((1.01/.99)**2-1):.3f} % / (0.99/1.01)^2-1 = {100*((.99/1.01)**2-1):.3f} %")
w_lo, w_hi = 1.235, 1.365
print(f"window 1.235..1.365 = width {w_hi-w_lo:.3f}; full-box spread width = {max(g400(dict(zip(SPAN,c))) for c in itertools.product(*SPAN.values())) - min(g400(dict(zip(SPAN,c))) for c in itertools.product(*SPAN.values())):.5f}")

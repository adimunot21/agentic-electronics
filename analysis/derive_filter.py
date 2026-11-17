"""Derive the FE-AAF-001 filter from requirements/fe-aaf-001.yaml (NOT assumed).

Chain of reasoning, each step printed with numbers:
  1. contract numbers -> required absolute stopband attenuation, passband droop budget
  2. for each family/order: attenuation at the alias frequency when the passband droop budget is exactly used
  3. minimum order per family that meets the requirement with design margin
  4. realisation: order 3 = MFB 2nd-order (Q=1, gain) + active 1st-order; transfer function derived symbolically
  5. component values on real series (E12 C0G caps, E96 resistors) by local search, evaluated on the exact response
  6. tolerance Monte Carlo on the IDEAL op-amp model (real op-amp effects are added by ngspice later)
Writes analysis/filter_design.json. Run: uv run python analysis/derive_filter.py
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import sympy as sp
import yaml
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]

CONFIG = {
    "design_margin_db": 3.0,          # attenuation margin kept for op-amp HF behaviour + tolerances (stopband gets worse with real parts)
    "lp_droop_budget_db": 0.25,       # half of the +/-0.5 dB flatness budget; the rest goes to the input HP corner + tolerance
    "target_gain": 1.30,              # REQ-GAIN nominal, see rationale in the contract
    "stage1_gain": 1.30,              # all gain in the MFB stage (earlier gain -> later-stage noise matters less)
    "stage1_q": 1.0,                  # Q of the complex pole pair of a 3rd-order Butterworth (poles -0.5 +/- j0.866)
    "min_input_resistance_ohm": 10_000,   # REQ-ZIN
    "max_resistance_ohm": 100_000,    # keeps resistor thermal noise and op-amp Ib*R errors small
    "coupling_cap_f": 2.2e-6,         # AC coupling; HP corner = 1/(2 pi R1 Cin) ~ 7 Hz
    "e12_c0g_caps_f": [1.0e-9, 1.2e-9, 1.5e-9, 1.8e-9, 2.2e-9, 2.7e-9, 3.3e-9, 3.9e-9, 4.7e-9, 5.6e-9, 6.8e-9, 8.2e-9,
                       1.0e-8, 1.2e-8, 1.5e-8, 1.8e-8, 2.2e-8],   # C0G 0603 availability tops out near 22 nF
    "search_neighbours": 3,           # +/- E96 steps explored around each ideal resistor value
    "monte_carlo_seed_offset": 0,     # seed itself comes from the contract
}

E96 = [100,102,105,107,110,113,115,118,121,124,127,130,133,137,140,143,147,150,154,158,162,165,169,174,178,182,187,191,196,
       200,205,210,215,221,226,232,237,243,249,255,261,267,274,280,287,294,301,309,316,324,332,340,348,357,365,374,383,392,
       402,412,422,432,442,453,464,475,487,499,511,523,536,549,562,576,590,604,619,634,649,665,681,698,715,732,750,768,787,
       806,825,845,866,887,909,931,953,976]
assert len(E96) == 96


def e96_values(lo: float = 100.0, hi: float = 1.0e6) -> np.ndarray:
    decades = [10.0 ** k for k in range(-1, 6)]
    vals = sorted({round(m * d / 100.0 * 100, 6) / 100 * 100 / 100 for d in decades for m in E96})
    return np.array([v for v in vals if lo <= v <= hi])


def load_contract() -> dict:
    return yaml.safe_load((ROOT / "requirements" / "fe-aaf-001.yaml").read_text())


# ---------------------------------------------------------------- step 1-3: order selection
def family_response(family: str, order: int, droop_db: float, f_edge: float, f_probe: float) -> float:
    """Attenuation (dB, positive) at f_probe when the passband droop at f_edge equals droop_db."""
    if family == "chebyshev1":   # ripple = droop budget, passband edge = f_edge
        b, a = signal.cheby1(order, droop_db, 2 * np.pi * f_edge, analog=True)
    else:
        make = {"butterworth": lambda wn: signal.butter(order, wn, analog=True),
                "bessel": lambda wn: signal.bessel(order, wn, analog=True, norm="mag")}[family]
        lo, hi = 2 * np.pi * f_edge * 0.1, 2 * np.pi * f_edge * 100
        for _ in range(80):      # bisect the scale: droop at f_edge == budget
            mid = np.sqrt(lo * hi)
            b, a = make(mid)
            droop = -20 * np.log10(abs(signal.freqs(b, a, [2 * np.pi * f_edge])[1][0]))
            lo, hi = (mid, hi) if droop > droop_db else (lo, mid)
        b, a = make(np.sqrt(lo * hi))
    return -20 * np.log10(abs(signal.freqs(b, a, [2 * np.pi * f_probe])[1][0]))


def order_table(contract: dict) -> dict:
    lsb = 3.3 / 2 ** contract["adc_model"]["resolution_bits"]
    abs_req_db = 20 * np.log10(lsb / contract["signal_source"]["interferer"]["amplitude_peak_V_at_input"])
    f_hi = contract["signal_source"]["band_hz"]["hi"]
    f_alias = contract["adc_model"]["sample_rate_hz"] - f_hi
    g0_db = 20 * np.log10(CONFIG["target_gain"])
    rel_req_db = -(abs_req_db + g0_db)       # attenuation needed *relative to passband gain*
    print(f"LSB = {lsb*1e3:.4f} mV | interferer 0.5 Vpk -> need absolute gain <= {abs_req_db:.2f} dB at >= {f_alias:.0f} Hz")
    print(f"passband gain {g0_db:+.2f} dB => filter must attenuate >= {rel_req_db:.2f} dB (+{CONFIG['design_margin_db']} dB margin) at {f_alias:.0f} Hz\n")
    print(f"{'family':<12}{'order':>5}{'atten@alias dB':>16}  meets(with margin)")
    rows, min_order = [], {}
    for family in ("butterworth", "chebyshev1", "bessel"):
        for order in range(1, 6):
            att = family_response(family, order, CONFIG["lp_droop_budget_db"], f_hi, f_alias)
            ok = att >= rel_req_db + CONFIG["design_margin_db"]
            rows.append({"family": family, "order": order, "atten_db": round(att, 2), "meets": bool(ok)})
            print(f"{family:<12}{order:>5}{att:>16.2f}  {'YES' if ok else 'no'}")
            if ok and family not in min_order:
                min_order[family] = order
    return {"abs_required_db": abs_req_db, "relative_required_db": rel_req_db, "rows": rows, "min_order": min_order, "f_alias": f_alias, "f_hi": f_hi}


# ---------------------------------------------------------------- step 4: symbolic MFB derivation
def mfb_symbolic():
    """Inverting multiple-feedback LP stage (ideal op-amp). Z1 = general input impedance (R1 + Rs + 1/(s Cin))."""
    s, Z1, R2, R3, C1, C2, x, vo = sp.symbols("s Z1 R2 R3 C1 C2 x vo")
    # inverting node N is a virtual ground: current through R3 flows through C2 to the output.
    x_expr = -vo * s * C2 * R3
    kcl = sp.Eq((1 - x) / Z1, x * s * C1 + (x - vo) / R2 + x / R3)       # Vin = 1
    sol = sp.solve(kcl.subs(x, x_expr), vo)[0]
    return sp.simplify(sol), (s, Z1, R2, R3, C1, C2)


def mfb_closed_form(R1, R2, R3, C1, C2, s):
    """Closed form with Z1 = R1 (pure resistor) for cross-checking the symbolic result.

    C1: node X -> ground (the LARGE cap); C2: inverting node -> output (the feedback cap, small).
    NOTE the damping term is multiplied by C2. (An earlier draft of this function had C1 there;
    the symbolic KCL cross-check below caught it, and a hand re-derivation confirmed the symbolic result.)
    """
    return -(R2 / R1) / (1 + s * C2 * (R2 + R3 + R2 * R3 / R1) + s ** 2 * R2 * R3 * C1 * C2)


# ---------------------------------------------------------------- step 5: realisation
def ideal_total_response(freqs_hz, p: dict, rs: float = 0.0, cin_scale: float = 1.0):
    """Complex response of: Rs - Cin - MFB(R1,R2,R3,C1,C2) - active 1st-order (R4,R5,C3), ideal op-amps."""
    h_mfb, (s_sym, z1_sym, r2, r3, c1, c2) = _SYM
    s = 2j * np.pi * np.asarray(freqs_hz, dtype=float)
    z1 = p["R1"] + rs + 1 / (s * p["Cin"] * cin_scale)
    h1 = _H1(s, z1, p["R2"], p["R3"], p["C1"], p["C2"])
    h2 = -(p["R5"] / p["R4"]) / (1 + s * p["R5"] * p["C3"])
    return h1 * h2


_SYM = mfb_symbolic()
_H1 = sp.lambdify(_SYM[1], _SYM[0], "numpy")


def target_response(freqs_hz, fc: float, gain: float):
    """Ideal 3rd-order Butterworth at fc with the given DC gain (no HP, no tolerances)."""
    b, a = signal.butter(3, 2 * np.pi * fc, analog=True)
    return gain * signal.freqs(b, a, 2 * np.pi * np.asarray(freqs_hz, dtype=float))[1]


def design_mfb_ideal(fc: float, gain: float, q: float, c1: float, c2: float) -> tuple[float, float, float] | None:
    """Solve R1,R2,R3 so the MFB stage has gain K, f0=fc, Q. Returns None if c1/c2 too small (complex roots)."""
    w0 = 2 * np.pi * fc
    k = gain
    t = 1 / (q * w0 * c2)                       # a + b with a = K R1, b = (1+K) R3  (damping term is C2*(R2+R3+R2R3/R1))
    prod = (1 + k) / (w0 ** 2 * c1 * c2)        # a * b   (needs c1/c2 >= 4 Q^2 (1+K))
    disc = t * t - 4 * prod
    if disc < 0:
        return None
    a = (t + np.sqrt(disc)) / 2                 # take the larger root for the (K R1) term -> larger input resistance
    b = t - a
    return a / k, a, b / (1 + k)                # R1, R2, R3


def nearest_e96_neighbours(value: float, n: int) -> list[float]:
    grid = e96_values(10.0, 2.0e6)
    idx = int(np.argmin(np.abs(np.log(grid / value))))
    return [float(v) for v in grid[max(0, idx - n): idx + n + 1]]


def realise(fc: float) -> dict:
    freqs = np.geomspace(50, 30_000, 120)
    best = None
    for c1, c2 in itertools.product(CONFIG["e12_c0g_caps_f"], repeat=2):
        ideal = design_mfb_ideal(fc, CONFIG["stage1_gain"], CONFIG["stage1_q"], c1, c2)
        if ideal is None or ideal[0] < CONFIG["min_input_resistance_ohm"] or max(ideal) > CONFIG["max_resistance_ohm"]:
            continue
        r1i, r2i, r3i = ideal
        # stage 2: pole at fc, unity gain; feedback R5 from C3 choice
        for c3 in CONFIG["e12_c0g_caps_f"]:
            r5i = 1 / (2 * np.pi * fc * c3)
            if not (CONFIG["min_input_resistance_ohm"] <= r5i <= CONFIG["max_resistance_ohm"]):
                continue
            for r1, r2, r3, r5 in itertools.product(*(nearest_e96_neighbours(v, CONFIG["search_neighbours"]) for v in (r1i, r2i, r3i, r5i))):
                params = {"R1": r1, "R2": r2, "R3": r3, "R4": r5, "R5": r5, "C1": c1, "C2": c2, "C3": c3, "Cin": CONFIG["coupling_cap_f"]}
                resp = ideal_total_response(freqs, params, rs=0.0)
                # compare to ideal target but ignoring the sign of the double inversion (net non-inverting) and the HP corner (<=60 Hz)
                err_db = 20 * np.log10(np.abs(resp) / np.abs(target_response(freqs, fc, CONFIG["target_gain"])))
                mask = freqs >= 100
                cost = float(np.max(np.abs(err_db[mask])))
                if best is None or cost < best["cost_db"]:
                    best = {"params": params, "cost_db": cost}
    return best


def passband_metrics(params: dict, rs: float = 100.0, cin_scale: float = 1.0) -> dict:
    f_pass = np.geomspace(50, 1000, 80)
    ref = abs(ideal_total_response([400.0], params, rs, cin_scale)[0])
    flat = 20 * np.log10(np.abs(ideal_total_response(f_pass, params, rs, cin_scale)) / ref)
    f_stop = np.geomspace(19_000, 1.0e6, 80)
    stop_db = 20 * np.log10(np.abs(ideal_total_response(f_stop, params, rs, cin_scale)))
    zin = np.abs(params["R1"] + rs + 1 / (2j * np.pi * f_pass * params["Cin"] * cin_scale))
    return {"gain_400": ref, "flat_max_abs_db": float(np.max(np.abs(flat))), "stop_max_db": float(np.max(stop_db)), "zin_min": float(np.min(zin))}


# ---------------------------------------------------------------- step 6: tolerance Monte Carlo (ideal op-amps)
def monte_carlo(params: dict, contract: dict) -> dict:
    tol = contract["tolerance_model"]
    mc = tol["yield_criterion"]["monte_carlo"]
    rng = np.random.default_rng(mc["seed"] + CONFIG["monte_carlo_seed_offset"])
    req = {r["id"]: r["check"] for r in contract["requirements"] if r.get("check")}
    n = mc["samples"]
    passes = {"REQ-GAIN": 0, "REQ-FLAT": 0, "REQ-ALIAS": 0, "REQ-ZIN": 0}
    all_pass = 0
    ranges = {"gain": [], "flat": [], "stop": []}
    for _ in range(n):
        p = dict(params)
        for key in ("R1", "R2", "R3", "R4", "R5"):
            p[key] *= 1 + rng.uniform(-1, 1) * tol["resistors"]["tolerance_pct"] / 100
        for key in ("C1", "C2", "C3"):
            p[key] *= 1 + rng.uniform(-1, 1) * tol["filter_capacitors"]["tolerance_pct"] / 100
        cin_scale = rng.uniform(0.6, 1.2)                      # X7R tolerance + dc-bias loss (contract)
        rs = rng.uniform(0, 100)
        m = passband_metrics(p, rs, cin_scale)
        ok = {
            "REQ-GAIN": req["REQ-GAIN"]["min"] <= m["gain_400"] <= req["REQ-GAIN"]["max"],
            "REQ-FLAT": m["flat_max_abs_db"] <= req["REQ-FLAT"]["max"],
            "REQ-ALIAS": m["stop_max_db"] <= req["REQ-ALIAS"]["max"],
            "REQ-ZIN": m["zin_min"] >= req["REQ-ZIN"]["min"],
        }
        for k, v in ok.items():
            passes[k] += v
        all_pass += all(ok.values())
        ranges["gain"].append(m["gain_400"]); ranges["flat"].append(m["flat_max_abs_db"]); ranges["stop"].append(m["stop_max_db"])
    return {"n": n, "yield_all": all_pass / n, "yield_each": {k: v / n for k, v in passes.items()},
            "gain_min_max": [min(ranges["gain"]), max(ranges["gain"])], "flat_worst_db": max(ranges["flat"]), "stop_worst_db": max(ranges["stop"])}


def main() -> dict:
    contract = load_contract()
    print("=== 1-3. order selection ===")
    table = order_table(contract)
    print("\nminimum order meeting the requirement, per family:", table["min_order"])
    assert table["min_order"]["butterworth"] == 3, "analysis changed: re-derive topology"
    n = table["min_order"]["butterworth"]
    f_hi = table["f_hi"]
    fc = f_hi / (10 ** (CONFIG["lp_droop_budget_db"] / 10) - 1) ** (1 / (2 * n))
    print(f"\nchosen: Butterworth order {n} (flat passband, monotone, fewest sections); fc = {fc:.1f} Hz so droop at {f_hi} Hz = {CONFIG['lp_droop_budget_db']} dB")
    print("   normalised poles: -1 (1st-order section) and -0.5 +/- j0.866 (Q = 1.0 section)")

    print("\n=== 4. symbolic MFB check ===")
    h_sym, syms = _SYM
    s_, z1_, r2_, r3_, c1_, c2_ = syms
    probe = dict(zip(syms, (2j * np.pi * 1234.0, 12e3, 15e3, 10e3, 10e-9, 1e-9)))
    sym_val = complex(h_sym.subs(probe))
    cf_val = complex(mfb_closed_form(12e3, 15e3, 10e3, 10e-9, 1e-9, probe[s_]))
    print(f"   symbolic vs textbook closed form at 1234 Hz: {sym_val:.6f} vs {cf_val:.6f}")
    assert abs(sym_val - cf_val) < 1e-9 * abs(cf_val), "symbolic MFB derivation disagrees with textbook"

    print("\n=== 5. realisation on E12 C0G / E96 ===")
    best = realise(fc)
    params = best["params"]
    print("   " + ", ".join(f"{k}={v:.4g}" for k, v in params.items()))
    print(f"   max |error| vs ideal Butterworth (100 Hz-30 kHz): {best['cost_db']:.3f} dB")
    nominal = passband_metrics(params, rs=50.0)
    print(f"   nominal (Rs=50): gain@400={nominal['gain_400']:.4f}  flatness={nominal['flat_max_abs_db']:.3f} dB  stop(19k..1M) max={nominal['stop_max_db']:.2f} dB  Zin>={nominal['zin_min']:.0f} ohm")

    print("\n=== 6. tolerance Monte Carlo, IDEAL op-amps ===")
    mc = monte_carlo(params, contract)
    print(f"   N={mc['n']}: yield(all)={mc['yield_all']*100:.2f} %   per-requirement={ {k: round(v*100,2) for k,v in mc['yield_each'].items()} }")
    print(f"   gain@400 range [{mc['gain_min_max'][0]:.4f}, {mc['gain_min_max'][1]:.4f}], worst flatness {mc['flat_worst_db']:.3f} dB, worst stopband {mc['stop_worst_db']:.2f} dB (req <= -55.8)")
    out = {"fc_hz": fc, "order": n, "params": params, "nominal": nominal, "monte_carlo_ideal_opamp": mc, "order_table": table["rows"],
           "scope": "ideal op-amps; real op-amp GBW/Rout/offset effects are only seen in ngspice with a vendor model"}
    (ROOT / "analysis" / "filter_design.json").write_text(json.dumps(out, indent=2, default=float))
    return out


if __name__ == "__main__":
    main()

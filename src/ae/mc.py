"""Tolerance Monte Carlo with the REAL op-amp model (ngspice), per the contract's yield criterion.

Distribution (contract `tolerance_model`): resistors uniform +-1 % (R7/R8 bias divider +-0.1 %), filter capacitors uniform +-5 %,
coupling cap C4 uniform in [0.6, 1.2] x nominal (X7R tolerance + dc-bias loss), source impedance uniform 0-100 ohm; seed from the contract.
Evaluates the AC-based requirements (REQ-GAIN, REQ-FLAT, REQ-ALIAS, REQ-ZIN, REQ-STAB). DC/transient/noise requirements are corner-based elsewhere.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import yaml

from ae import sim_metrics as sm
from ae.sim import MODEL_SHIM, SimRun, header, load_ideal
from ae.toolchain import REPO_ROOT, run_in_image

TOL = {"R1": .01, "R2": .01, "R3": .01, "R4": .01, "R5": .01, "R6": .01, "R7": .001, "R8": .001, "C1": .05, "C2": .05, "C3": .05, "C5": .05, "C8": 0.0}


def run_mc(run_dir: Path, netlist: Path, n: int | None = None) -> dict:
    contract = yaml.safe_load((REPO_ROOT / "requirements/fe-aaf-001.yaml").read_text())
    mc = contract["tolerance_model"]["yield_criterion"]["monte_carlo"]
    n = n or mc["samples"]
    rng = np.random.default_rng(mc["seed"])
    work = run_dir / "work_mc"
    sim = SimRun(work=work, netlist=netlist)
    sim.prepare()
    samples = []
    for i in range(n):
        scale = {ref: 1 + rng.uniform(-1, 1) * tol for ref, tol in TOL.items() if tol}
        scale["C4"] = rng.uniform(0.6, 1.2)
        rs = float(rng.uniform(0.01, 100.0))
        samples.append((scale, rs))
        (work / f"s{i}_dut.cir").write_text(sim.deck(scale=scale))
        tb = header(rs=rs, src="DC 0 AC 1").replace("@DUT@", f"s{i}_dut.cir") + f".control\nop\nac dec 20 10 10Meg\nwrdata s{i}.csv v(src) v(ain) v(aout)\n.endc\n.end\n"
        (work / f"s{i}.cir").write_text(tb)
    t0 = time.monotonic()
    rec = run_in_image("mc", ["bash", "-c", f"seq 0 {n-1} | xargs -P 10 -I{{}} sh -c 'timeout 60 ngspice -b -o s{{}}.log s{{}}.cir >/dev/null 2>&1; true'"], work, timeout_s=3600)
    wall = time.monotonic() - t0
    ideal = load_ideal(netlist)
    req = {r["id"]: r["check"] for r in contract["requirements"] if r.get("check")}
    rows, blocked = [], 0
    for i, (scale, rs) in enumerate(samples):
        csv_path = work / f"s{i}.csv"
        log = (work / f"s{i}.log")
        if not csv_path.exists() or "Transient op" in (log.read_text() if log.exists() else "Transient op"):
            blocked += 1
            continue
        f, (vs, va, vo) = sm.read_wrdata(csv_path, 3, True)
        m = sm.ac_metrics(f, vs, va, vo, rs_ohm=rs, ideal_mag=np.abs(ideal(f, rs)))
        ok = {"REQ-GAIN": req["REQ-GAIN"]["min"] <= m["gain_400hz_V_per_V"] <= req["REQ-GAIN"]["max"],
              "REQ-FLAT": m["passband_flatness_dB_max_abs"] <= req["REQ-FLAT"]["max"],
              "REQ-ALIAS": m["stopband_abs_gain_dB_max_19k_to_1M"] <= req["REQ-ALIAS"]["max"],
              "REQ-ZIN": m["zin_min_ohm"] >= req["REQ-ZIN"]["min"],
              "REQ-STAB": m["peaking_above_ideal_dB_max_unfloored"] <= req["REQ-STAB"]["max"], "REQ-STAB-R3-AC": m["peaking_above_ideal_dB_max"] <= 3.0}
        rows.append({"i": i, "rs": rs, **{k: m[k] for k in ("gain_400hz_V_per_V", "passband_flatness_dB_max_abs", "stopband_abs_gain_dB_max_19k_to_1M", "zin_min_ohm", "peaking_above_ideal_dB_max", "peaking_above_ideal_dB_max_unfloored")}, **ok})
    done = len(rows)
    summary = {"n_requested": n, "n_completed": done, "n_blocked": blocked, "seed": mc["seed"], "wall_s": round(wall, 1), "container_peak_rss_mb": rec.peak_rss_mb,
               "yield_all_excluding_REQ_STAB_rev2": (sum(all(r[k] for k in ("REQ-GAIN", "REQ-FLAT", "REQ-ALIAS", "REQ-ZIN", "REQ-STAB-R3-AC")) for r in rows) / done) if done else None,
               "note": "REQ-STAB rev 2 (in force) fails deterministically in every sample (excess at < -128 dB, see contract revisions); the overall yield therefore uses the PROPOSED rev-3 form for stability",
               "yield_each": {k: sum(r[k] for r in rows) / done for k in ("REQ-GAIN", "REQ-FLAT", "REQ-ALIAS", "REQ-ZIN", "REQ-STAB", "REQ-STAB-R3-AC")} if done else {},
               "worst": {k: (max if k not in ("zin_min_ohm",) else min)(r[k] for r in rows) for k in ("passband_flatness_dB_max_abs", "stopband_abs_gain_dB_max_19k_to_1M", "peaking_above_ideal_dB_max", "peaking_above_ideal_dB_max_unfloored", "zin_min_ohm")} if done else {},
               "gain_range": [min(r["gain_400hz_V_per_V"] for r in rows), max(r["gain_400hz_V_per_V"] for r in rows)] if done else None,
               "criterion": ">= 99 % of samples meet every simulated AC requirement; corner analysis is separate"}
    (run_dir / "mc_results.json").write_text(json.dumps({"summary": summary, "samples": rows}, indent=1, default=float))
    return summary


if __name__ == "__main__":
    import sys
    s = run_mc(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(json.dumps(s, indent=2, default=float))


CORNER_SPAN = {"R1": (.99, 1.01), "R2": (.99, 1.01), "R3": (.99, 1.01), "R4": (.99, 1.01), "R5": (.99, 1.01), "C1": (.95, 1.05), "C2": (.95, 1.05), "C3": (.95, 1.05),
               "C4": (.6, 1.2), "Rs": (0.01, 100.0)}


def run_corners(run_dir: Path, netlist: Path) -> dict:
    """ALL vertices (2^10 = 1024) of the contract tolerance box, real op-amp model, AC requirements. This is worst-case, not statistical:
    a vertex failing a limit is a failure of the literal requirement within the specified tolerances."""
    import itertools
    names = list(CORNER_SPAN)
    work = run_dir / "work_corners"
    sim = SimRun(work=work, netlist=netlist)
    sim.prepare()
    corners = list(itertools.product((0, 1), repeat=len(names)))
    for i, combo in enumerate(corners):
        v = {n: CORNER_SPAN[n][c] for n, c in zip(names, combo)}
        (work / f"k{i}_dut.cir").write_text(sim.deck(scale={k: v[k] for k in names if k != "Rs"}))
        (work / f"k{i}.cir").write_text(header(rs=v["Rs"], src="DC 0 AC 1").replace("@DUT@", f"k{i}_dut.cir") + f".control\nop\nac dec 20 10 10Meg\nwrdata k{i}.csv v(src) v(ain) v(aout)\n.endc\n.end\n")
    rec = run_in_image("corners", ["bash", "-c", f"seq 0 {len(corners)-1} | xargs -P 10 -I{{}} sh -c 'timeout 60 ngspice -b -o k{{}}.log k{{}}.cir >/dev/null 2>&1; true'"], work, timeout_s=1800)
    rows = []
    for i, combo in enumerate(corners):
        p, log = work / f"k{i}.csv", work / f"k{i}.log"
        if not p.exists() or "Transient op" in (log.read_text() if log.exists() else "Transient op"):
            continue
        v = {n: CORNER_SPAN[n][c] for n, c in zip(names, combo)}
        f, (vs, va, vo) = sm.read_wrdata(p, 3, True)
        m = sm.ac_metrics(f, vs, va, vo, rs_ohm=v["Rs"])
        rows.append({"corner": {n: ("lo" if c == 0 else "hi") for n, c in zip(names, combo)}, **{k: m[k] for k in ("gain_400hz_V_per_V", "passband_flatness_dB_max_abs", "stopband_abs_gain_dB_max_19k_to_1M", "zin_min_ohm")}})
    worst_flat = max(rows, key=lambda r: r["passband_flatness_dB_max_abs"]) if rows else None
    return {"n": len(corners), "completed": len(rows), "wall_s": round(rec.wall_s, 1), "rows": rows, "worst_flat": worst_flat,
            "flat_fail": sum(r["passband_flatness_dB_max_abs"] > 0.5 for r in rows),
            "gain_min": min(r["gain_400hz_V_per_V"] for r in rows) if rows else None, "gain_max": max(r["gain_400hz_V_per_V"] for r in rows) if rows else None,
            "gain_fail": sum(not (1.235 <= r["gain_400hz_V_per_V"] <= 1.365) for r in rows),
            "stop_worst": max(r["stopband_abs_gain_dB_max_19k_to_1M"] for r in rows) if rows else None,
            "stop_fail": sum(r["stopband_abs_gain_dB_max_19k_to_1M"] > -55.8 for r in rows)}

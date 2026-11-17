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
    ideal = load_ideal()
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
              "REQ-STAB": m["peaking_above_ideal_dB_max"] <= req["REQ-STAB"]["max"]}
        rows.append({"i": i, "rs": rs, **{k: m[k] for k in ("gain_400hz_V_per_V", "passband_flatness_dB_max_abs", "stopband_abs_gain_dB_max_19k_to_1M", "zin_min_ohm", "peaking_above_ideal_dB_max")}, **ok})
    done = len(rows)
    summary = {"n_requested": n, "n_completed": done, "n_blocked": blocked, "seed": mc["seed"], "wall_s": round(wall, 1), "container_peak_rss_mb": rec.peak_rss_mb,
               "yield_all": (sum(all(r[k] for k in ("REQ-GAIN", "REQ-FLAT", "REQ-ALIAS", "REQ-ZIN", "REQ-STAB")) for r in rows) / done) if done else None,
               "yield_each": {k: sum(r[k] for r in rows) / done for k in ("REQ-GAIN", "REQ-FLAT", "REQ-ALIAS", "REQ-ZIN", "REQ-STAB")} if done else {},
               "worst": {k: (max if k not in ("zin_min_ohm",) else min)(r[k] for r in rows) for k in ("passband_flatness_dB_max_abs", "stopband_abs_gain_dB_max_19k_to_1M", "peaking_above_ideal_dB_max", "zin_min_ohm")} if done else {},
               "gain_range": [min(r["gain_400hz_V_per_V"] for r in rows), max(r["gain_400hz_V_per_V"] for r in rows)] if done else None,
               "criterion": ">= 99 % of samples meet every simulated AC requirement; corner analysis is separate"}
    (run_dir / "mc_results.json").write_text(json.dumps({"summary": summary, "samples": rows}, indent=1, default=float))
    return summary


if __name__ == "__main__":
    import sys
    s = run_mc(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(json.dumps(s, indent=2, default=float))

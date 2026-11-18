"""Agent-facing FAST checks (real adapters: KiCad netlist export + ngspice AC). Same trusted code the evaluator uses; the evaluator additionally runs the FULL pipeline.

bom_consistency: schematic generated from the candidate manifest -> KiCad netlist -> ae.bomcheck (value/package/tolerance/dielectric/rating vs MPN decode).
ac_response:    nominal AC run (Rs = 100 ohm) judged against the CONTRACT limits (gain window, flatness, alias at exactly 19 kHz).
Outcomes: passed | failed | blocked | error. Raw measurements are returned, never only a verdict.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ae import bomcheck
from ae.designs import fe_aaf_001 as design
from ae.sim import SimRun, ac_run, load_ideal
from ae.stages import kicad_netlist
from ae.toolchain import REPO_ROOT

CHECKS = ("bom_consistency", "ac_response")


def _limits() -> dict:
    req = {r["id"]: r for r in yaml.safe_load((REPO_ROOT / "requirements/fe-aaf-001.yaml").read_text())["requirements"]}
    return {"gain": (req["REQ-GAIN"]["check"]["min"], req["REQ-GAIN"]["check"]["max"]), "flat": req["REQ-FLAT"]["check"]["max"], "alias": req["REQ-ALIAS"]["check"]["max"]}


def run_fast(names: list[str], components_path: Path, workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "fe_aaf_001.kicad_sch").write_text(design.build(components_path=components_path).to_text())
    net = kicad_netlist(workdir, "fe_aaf_001.kicad_sch", "fe_aaf_001.net")
    if net.outcome != "passed":
        return {n: {"outcome": "error", "detail": f"netlist export failed (exit {net.exit_code}): {net.stderr[-200:]}"} for n in names}
    out = {}
    if "bom_consistency" in names:
        outcome, detail, lines = bomcheck.check_netlist(workdir / "fe_aaf_001.net")
        out["bom_consistency"] = {"outcome": outcome, "detail": detail,
                                  "mismatched_lines": [{"refs": l["refs"], "mpn": l["mpn"], "problems": l["problems"]} for l in lines if l["outcome"] == "failed"],
                                  "unresolved_lines": [l["refs"] for l in lines if l["outcome"] == "unresolved"]}
    if "ac_response" in names:
        lim = _limits()
        sim = SimRun(work=workdir / "sim", netlist=workdir / "fe_aaf_001.net")
        sim.prepare()
        m = ac_run(sim, load_ideal(workdir / "fe_aaf_001.net"), 100.0, "rs100")
        if m.get("blocked"):
            out["ac_response"] = {"outcome": "blocked", "detail": "AC run blocked (no DC operating point or timeout)"}
        else:
            g, f, a = m["gain_400hz_V_per_V"], m["passband_flatness_dB_max_abs"], m["stopband_abs_gain_dB_max_19k_to_1M"]
            ok = {"REQ-GAIN": lim["gain"][0] <= g <= lim["gain"][1], "REQ-FLAT": f <= lim["flat"], "REQ-ALIAS": a <= lim["alias"]}
            out["ac_response"] = {"outcome": "passed" if all(ok.values()) else "failed", "per_requirement": {k: ("passed" if v else "failed") for k, v in ok.items()},
                                  "measurements": {"gain_400Hz_V_per_V": g, "flatness_dB": f, "alias_at_19kHz_dB": a},
                                  "limits": {"gain_V_per_V": list(lim["gain"]), "flatness_dB_max": lim["flat"], "alias_dB_max": lim["alias"]}, "conditions": "nominal parts, Rs = 100 ohm, real TI model (shim), ngspice 44.2"}
    return out

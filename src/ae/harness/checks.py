"""Agent-facing FAST checks (real adapters: KiCad netlist export + ngspice AC). Same trusted code the evaluator uses; the evaluator additionally runs the FULL pipeline.

bom_consistency: schematic generated from the candidate manifest -> KiCad netlist -> ae.bomcheck (value/package/tolerance/dielectric/rating vs MPN decode).
ac_response:    nominal AC run (Rs = 100 ohm) judged against the CONTRACT limits (gain window, flatness, alias at exactly 19 kHz).
corner_preview: the evaluator's OWN worst-case sweep (ae.mc.run_corners: all 1024 vertices of the contract tolerance box, real model) on the candidate. ADVISORY: the independent evaluator still
                judges the design with the full pipeline and further rules (REQ-STAB, reserves, BOM, non-regression); this only shows the agent the same corner numbers the evaluator will compute.
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

CHECKS = ("bom_consistency", "ac_response", "corner_preview")


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
    if "corner_preview" in names:
        from ae.mc import run_corners                              # same trusted code the evaluator's pipeline runs
        lim = _limits()
        cr = run_corners(workdir / "corner_preview", workdir / "fe_aaf_001.net")
        if not cr["rows"] or cr["completed"] != cr["n"]:
            out["corner_preview"] = {"outcome": "blocked", "detail": f"only {cr['completed']} of {cr['n']} corners completed"}
        else:
            flat_worst = cr["worst_flat"]["passband_flatness_dB_max_abs"]
            ok = {"REQ-FLAT-CORNERS": cr["flat_fail"] == 0, "REQ-GAIN-CORNERS": cr["gain_fail"] == 0, "REQ-ALIAS-CORNERS": cr["stop_fail"] == 0}
            out["corner_preview"] = {"outcome": "within_contract_limits" if all(ok.values()) else "outside_contract_limits", "not_acceptance": "this does NOT test the required objective improvement over the baseline, the policy reserves, REQ-STAB or non-regression; only the independent evaluator accepts or rejects", "per_requirement": {k: ("passed" if v else "failed") for k, v in ok.items()},
                                     "measurements": {"alias_corners_worst_dB": cr["stop_worst"], "flatness_corners_worst_dB": flat_worst, "gain_corners_min_V_per_V": cr["gain_min"],
                                                      "gain_corners_max_V_per_V": cr["gain_max"], "corners_completed": cr["completed"], "corners_total": cr["n"]},
                                     "worst_flatness_corner": cr["worst_flat"]["corner"],
                                     "limits": {"gain_V_per_V": list(lim["gain"]), "flatness_dB_max": lim["flat"], "alias_dB_max": lim["alias"]},
                                     "conditions": "ALL tolerance corners, real TI model (shim); same code as the evaluator; ADVISORY: the evaluator also applies reserves, REQ-STAB and non-regression rules that this does not report"}
    return out

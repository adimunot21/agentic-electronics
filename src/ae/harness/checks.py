"""Agent-facing FAST checks (real adapters: KiCad netlist export + ngspice AC). Same trusted code the evaluator uses; the evaluator additionally runs the FULL pipeline.

bom_consistency: schematic generated from the candidate manifest -> KiCad netlist -> ae.bomcheck (value/package/tolerance/dielectric/rating vs MPN decode).
ac_response:    nominal AC run (Rs = 100 ohm) judged against the CONTRACT limits (gain window, flatness, alias at exactly 19 kHz).
corner_preview: the evaluator's OWN worst-case sweep (ae.mc.run_corners: all 1024 vertices of the contract tolerance box, real model) on the candidate. ADVISORY: the independent evaluator still
                judges the design with the full pipeline and further rules (REQ-STAB, reserves, BOM, non-regression); this only shows the agent the same corner numbers the evaluator will compute.
requirements_preview: the evaluator's own nominal requirement sweep (ae.sim.evaluate_all: gain, flatness, alias, input impedance, bias, quiescent current, headroom, settling, REQ-STAB, noise), ~35 s. ADVISORY like
                corner_preview; it exists so the agent can see the metrics the non-regression rules guard (REQ-STAB especially) BEFORE a 100 s evaluation.
Outcomes: passed | failed | blocked | error. Raw measurements are returned, never only a verdict.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ae import bomcheck
from ae.designs import fe_aaf_001 as design
from ae.harness.improvement import _margin
from ae.sim import SimRun, ac_run, load_ideal
from ae.stages import kicad_netlist
from ae.toolchain import REPO_ROOT

CHECKS = ("bom_consistency", "ac_response", "corner_preview", "requirements_preview", "preview", "edit_sanity")
PREVIEW_CHECKS = ("corner_preview", "requirements_preview", "preview")      # improvement tasks only, budgeted, advisory
PREVIEW_PARTS = {"corner_preview": ("corner_preview",), "requirements_preview": ("requirements_preview",), "preview": ("corner_preview", "requirements_preview")}


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
    if "requirements_preview" in names:
        from ae.sim import evaluate_all                            # same trusted code the evaluator's pipeline runs
        sim_rp = SimRun(work=workdir / "requirements_preview", netlist=workdir / "fe_aaf_001.net")
        ms = evaluate_all(sim_rp)
        out["requirements_preview"] = {"outcome": "info", "not_acceptance": "nominal parts only; no policy reserves, no baseline comparison, no objective, no corners; only the independent evaluator accepts or rejects",
                                       "requirements": {m.req: {"outcome": m.outcome, "value": m.value, "limit": m.limit, "margin": m.margin} for m in ms},
                                       "conditions": "nominal parts, real TI model (shim); same code as the evaluator (ae.sim.evaluate_all). REQ-STAB is rev 2 and FAILED on the baseline; compare its VALUE with the baseline value in the task brief"}
    return out


def assess_guards(policy_raw: dict, rules: dict, baseline_checks: dict, objective_check: str, corner: dict | None, requirements: dict | None) -> dict:
    """PROJECTION of the policy's rules onto preview numbers, so the agent sees which rule an evaluation would trip BEFORE spending it (combined `preview`).
    Pure. Mirrors ae.harness.improvement.decide_improvement for: objective improvement, corner hard constraints H1-H3, REQ-STAB non-regression and the generic margin rule on
    nominal metrics. It is a projection from nominal/corner previews, NOT the verdict (the evaluator also applies BOM/status/series/row rules and a full pipeline run).
    rules = {"margin_fraction", "abs_tol", "rel_tol", "min_improvement"}; baseline_checks = {check id: baseline record check dict}. Rows: {id, rule, baseline, candidate, limit, status}."""
    tol = lambda ref: rules["abs_tol"] + rules["rel_tol"] * abs(ref)
    rows: list[dict] = []

    def row(cid: str, rule: str, baseline, candidate, limit, ok: bool | None) -> None:
        rows.append({"id": cid, "rule": rule, "baseline": baseline, "candidate": candidate, "limit": limit, "status": "unknown" if ok is None else ("ok" if ok else "WOULD_BE_REJECTED")})

    if corner and corner.get("outcome") in ("within_contract_limits", "outside_contract_limits"):
        m = corner["measurements"]
        base_obj = baseline_checks[objective_check]["value"]
        gained = base_obj - m["alias_corners_worst_dB"]                                  # objective direction: minimize (more negative is better)
        row(objective_check, "objective_improvement", base_obj, m["alias_corners_worst_dB"], f"improve by >= {rules['min_improvement']} dB (now {gained:+.3f})", gained >= rules["min_improvement"] + tol(base_obj))
        for h in policy_raw.get("hard_constraints", []):
            if h["id"] == "H1":
                row(h["check"], "H1_hard_limit", None, m["flatness_corners_worst_dB"], f"<= {h['max']}", m["flatness_corners_worst_dB"] <= h["max"])
            elif h["id"] == "H2":
                row(h["check"], "H2_gain_min_corners", None, m["gain_corners_min_V_per_V"], f">= {h['min']}", m["gain_corners_min_V_per_V"] >= h["min"])
            elif h["id"] == "H3":
                row(h["check"], "H3_gain_max_corners", None, m["gain_corners_max_V_per_V"], f"<= {h['max']}", m["gain_corners_max_V_per_V"] <= h["max"])
    if requirements:
        handled = {objective_check, "REQ-ALIAS", "REQ-GAIN", "REQ-FLAT"}                 # traded metrics are governed by the hard constraints, not the margin rule
        for cid, c in requirements["requirements"].items():
            b = baseline_checks.get(cid)
            if b is None or cid in handled:
                continue
            if cid == "REQ-STAB":
                v, bv = c["value"], b["value"]
                row(cid, "REQ-STAB_nonregression", bv, v, f"<= baseline + {tol(bv):.3g}", v is not None and v <= bv + tol(bv))
                continue
            if b["outcome"] != "passed":
                continue
            mb, mc = _margin(b), _margin(c)               # same margin extraction as the evaluator (record margin, else parsed limit)
            if mb is None or mc is None:
                continue
            allowed = rules["margin_fraction"] * mb if mb > 0 else 0.0
            row(cid, "margin_nonregression", {"value": b.get("value"), "margin": mb}, {"value": c.get("value"), "margin": mc}, f"margin >= {mb - allowed - tol(mb):.4g}", mc >= mb - allowed - tol(mb))
    bad = [r for r in rows if r["status"] == "WOULD_BE_REJECTED"]
    return {"projection_only": True, "would_trip": [f"{r['id']}:{r['rule']}" for r in bad], "all_projected_rules_ok": not bad, "rows": rows}

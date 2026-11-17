"""One orchestrated, recorded run of the FE-AAF-001 reference design (manual reference, not agent output).

Order: generate schematic -> ERC -> netlist -> intent compare -> datasheet pin audit (on the EMBEDDED symbol) -> deck cross-check
-> ngspice requirement suite -> board build/route/DRC -> release outputs -> record.json.
`mutate_*` hooks apply a controlled defect to this run's own copy only (see designs/fe-aaf-001/defects.yaml).
Outcomes: passed | failed | blocked | not_tested | error. Nothing skipped is ever reported as passed.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from ae import bomcheck, pcb, pin_audit, release, spice
from ae.designs import fe_aaf_001 as design
from ae.evidence import collect_inputs
from ae.ownership import guarded_write
from ae.sim import SimRun, evaluate_all
from ae.stages import compare_netlist_to_intent, kicad_erc, kicad_netlist
from ae.toolchain import IMAGE, JRE_IMAGE, FREEROUTING_JAR, FREEROUTING_JAR_SHA256, REPO_ROOT, StageRecord, run_in_image

STEM = "fe_aaf_001"
U1_EXPECTED = {"+IN A": "VMID", "-IN A": "N_INV", "OUT A": "N_S1", "+IN B": "VMID", "-IN B": "N_INV2", "OUT B": "N_S2", "V+": "+5V", "V-": "GND"}


@dataclass
class Check:
    id: str
    outcome: str
    detail: str = ""
    value: float | None = None
    limit: str = ""
    margin: float | None = None


@dataclass
class RunRecord:
    run_id: str
    label: str
    checks: list[Check] = field(default_factory=list)
    stages: list[StageRecord] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def add(self, *a, **k) -> Check:
        c = Check(*a, **k)
        self.checks.append(c)
        return c


def tool_versions() -> dict:
    kicad = run_in_image("ver_kicad", ["kicad-cli", "version"], REPO_ROOT).stdout.strip()
    ng = run_in_image("ver_ng", ["bash", "-c", "ngspice --version | grep -m1 ngspice"], REPO_ROOT).stdout.strip()
    jre = subprocess.run(["docker", "run", "--rm", "--entrypoint", "java", JRE_IMAGE, "-version"], capture_output=True, text=True).stderr.splitlines()[0]
    return {"kicad_cli": kicad, "ngspice": ng, "java": jre, "kicad_image": IMAGE, "jre_image": JRE_IMAGE, "freerouting_jar_sha256": FREEROUTING_JAR_SHA256,
            "host_python": platform.python_version(), "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT).stdout.strip()}


def timed(rec: RunRecord, name: str):
    class _T:
        def __enter__(self):
            self.t = time.monotonic()
        def __exit__(self, *e):
            rec.timings[name] = round(time.monotonic() - self.t, 2)
    return _T()


def run_reference(run_dir: Path, *, label: str = "reference", nets: dict | None = None,
                  mutate_schematic: Callable[[str], str] | None = None, mutate_pcb_net: str | None = None, skip_sim: bool = False) -> RunRecord:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    rec = RunRecord(run_id=run_dir.name, label=label)
    t_all = time.monotonic()
    pins_ref = yaml.safe_load((REPO_ROOT / "evidence/pins/OPA2314AID.yaml").read_text())
    sch, net = f"{STEM}.kicad_sch", f"{STEM}.net"

    with timed(rec, "generate_schematic"):
        text = design.build(nets=nets).to_text()
        guarded_write(run_dir / sch, text, run_dir / ".generated.json")
        if mutate_schematic:                                   # manual-edit style defect on THIS copy only
            (run_dir / sch).write_text(mutate_schematic((run_dir / sch).read_text()))
    with timed(rec, "erc"):
        erc, rep = kicad_erc(run_dir, sch)
        rec.stages.append(erc)
        rec.add("ERC", erc.outcome, f"{len(rep['violations'])} violations" + (": " + "; ".join(v["description"] for v in rep["violations"][:3]) if rep["violations"] else ""))
    with timed(rec, "netlist"):
        n = kicad_netlist(run_dir, sch, net)
        rec.stages.append(n)
    if n.outcome != "passed":
        rec.add("NETLIST_INTENT", "blocked", "netlist export failed")
        return finish(rec, run_dir, t_all)
    outcome, detail = compare_netlist_to_intent(run_dir / net, design.intended_nets(nets))
    rec.add("NETLIST_INTENT", outcome, detail)

    with timed(rec, "pin_audit"):
        sch_text = (run_dir / sch).read_text()
        embedded = pin_audit.EmbeddedSymbols(sch_text)
        r = pin_audit.audit_symbol(pins_ref, embedded, "Amplifier_Operational:LM358")
        rec.add("PIN_SYMBOL", r.outcome, r.detail + " [embedded symbol in schematic]")
        for r in pin_audit.audit_footprint(pins_ref, REPO_ROOT / "vendor/kicad-libs-10.0.8/footprints/Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"):
            rec.add("PIN_FOOTPRINT" if r.check != "footprint_lead_overlap" else "PIN_FOOTPRINT", r.outcome, f"{r.check}: {r.detail}")
        r = pin_audit.audit_spice_subckt(pins_ref, REPO_ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib")
        rec.add("PIN_SPICE_HEADER", r.outcome, r.detail)
        r = pin_audit.audit_wiring(pins_ref, run_dir / net, "U1", U1_EXPECTED)
        rec.add("PIN_WIRING", r.outcome, r.detail)

    with timed(rec, "bom_consistency"):
        o, d, _lines = bomcheck.check_netlist(run_dir / net)
        rec.add("BOM_CONSISTENCY", o, d)

    with timed(rec, "deck_check"):
        deck = spice.build_dut_deck(run_dir / net, pins_ref, "opa2314_ngspice_shim.lib")
        o, d = spice.check_deck_vs_netlist(deck, run_dir / net, pins_ref)
        rec.add("DECK_VS_NETLIST", o, d)

    if skip_sim:
        for rid in ("REQ-GAIN", "REQ-FLAT", "REQ-ALIAS", "REQ-ZIN", "REQ-BIAS", "REQ-IQ", "REQ-HEADROOM", "REQ-SETTLE", "REQ-STAB", "REQ-NOISE"):
            rec.add(rid, "not_tested", "simulation skipped by caller")
    else:
        with timed(rec, "simulation"):
            sim = SimRun(work=run_dir / "work", netlist=run_dir / net)
            for m in evaluate_all(sim):
                rec.add(m.req, m.outcome, m.detail, m.value, m.limit, m.margin)
            rec.stages += sim.stages

    rec.add("MODEL_STARTUP", "not_tested", "Power-up behaviour of the SHIMMED TI model is unvalidated: the unmodified model has no DC operating point in ngspice 44.2 (transient fallback gave wrong bias); all results start from the shim's .op, no supply-ramp transient was run or compared.")
    rec.add("MODEL_SATURATION", "not_tested", "Output saturation / rail-limit behaviour of the shimmed model is unvalidated (headroom is only checked inside 0.1-3.2 V, never driven into the rails; model output swing vs datasheet not compared).")
    rec.add("MODEL_OVERLOAD_RECOVERY", "not_tested", "The overload-recovery clamp instance X_SW_OR is patched OUT of the model: recovery from overdrive is not modelled. Linear agreement (identical follower -3 dB/peaking/noise vs the original) does NOT validate startup, saturation or overload behaviour.")
    with timed(rec, "corners"):
        from ae.mc import run_corners
        cr = run_corners(run_dir, run_dir / net)
        if cr["completed"] != cr["n"]:
            for cid in ("REQ-FLAT-CORNERS", "REQ-GAIN-CORNERS", "REQ-ALIAS-CORNERS"):
                rec.add(cid, "blocked", f"only {cr['completed']} of {cr['n']} corners completed")
        else:
            wf = cr["worst_flat"]
            rec.add("REQ-FLAT-CORNERS", "failed" if cr["flat_fail"] else "passed", f"WORST-CASE (all {cr['n']} vertices of the contract tolerance box, real model): {cr['flat_fail']} corners exceed 0.5 dB; worst {wf['passband_flatness_dB_max_abs']:.3f} dB at {wf['corner']}", wf["passband_flatness_dB_max_abs"], "<= 0.5", 0.5 - wf["passband_flatness_dB_max_abs"])
            rec.add("REQ-GAIN-CORNERS", "failed" if cr["gain_fail"] else "passed", f"gain range over all corners {cr['gain_min']:.4f}..{cr['gain_max']:.4f} vs 1.235..1.365", cr["gain_max"], "1.235..1.365", min(1.365 - cr["gain_max"], cr["gain_min"] - 1.235))
            rec.add("REQ-ALIAS-CORNERS", "failed" if cr["stop_fail"] else "passed", f"worst stopband over all corners {cr['stop_worst']:.1f} dB", cr["stop_worst"], "<= -55.8", -55.8 - cr["stop_worst"])
        (run_dir / "corners_summary.json").write_text(json.dumps({k: v for k, v in cr.items() if k != "rows"}, indent=1, default=float))

    with timed(rec, "pcb"):
        b = pcb.build_board(run_dir, REPO_ROOT / "designs/fe-aaf-001/placement.yaml")
        rec.stages.append(b)
        if b.outcome != "passed":
            for c in ("DRC_VIOLATIONS", "DRC_UNCONNECTED", "DRC_PARITY"):
                rec.add(c, "blocked", "board build failed")
            return finish(rec, run_dir, t_all)
        shutil.copy(run_dir / f"{STEM}.kicad_pcb", run_dir / "snapshot_unrouted.kicad_pcb")
        steps = pcb.route(run_dir, f"{STEM}.kicad_pcb", f"{STEM}.kicad_pcb")
        rec.stages += steps
        if any(s.outcome != "passed" for s in steps):
            for c in ("DRC_VIOLATIONS", "DRC_UNCONNECTED", "DRC_PARITY"):
                rec.add(c, "blocked", "routing failed: " + next(s for s in steps if s.outcome != "passed").name)
            return finish(rec, run_dir, t_all)
        if mutate_pcb_net:
            m = run_in_image("mutate_board", ["python3", "/opt/ae/mutate_board.py", f"{STEM}.kicad_pcb", mutate_pcb_net, f"{STEM}.kicad_pcb"], run_dir)
            m.outcome = "passed" if m.exit_code == 0 else "error"
            rec.stages.append(m)
        dr, d = pcb.drc(run_dir, f"{STEM}.kicad_pcb", "drc.json")
        rec.stages.append(dr)
        if not d:
            for c in ("DRC_VIOLATIONS", "DRC_UNCONNECTED", "DRC_PARITY"):
                rec.add(c, "error", "DRC produced no report")
        else:
            for cid, key in (("DRC_VIOLATIONS", "violations"), ("DRC_UNCONNECTED", "unconnected_items"), ("DRC_PARITY", "schematic_parity")):
                items = d["report"].get(key, [])
                rec.add(cid, "passed" if not items else "failed", f"{len(items)} items" + (": " + "; ".join(i["description"] for i in items[:2]) if items else ""))
            routed = [s for s in steps if s.name == "freerouting"][0]
            m = re.search(r"final score: ([\d.]+) \((\d+) unrouted and (\d+) violations\)", routed.stdout + routed.stderr)
            if m:
                rec.timings["router_final_score"] = float(m.group(1))

    with timed(rec, "release"):
        rec.stages += release.export_all(run_dir, STEM)
        counts = {c.id: (0 if c.outcome == "passed" else 1) for c in rec.checks if c.id.startswith("DRC_")}
        o, d = release.check_bom(run_dir)
        rec.add("REQ-BOM", o, d)
        o, d = release.check_mfg(run_dir, STEM, counts if counts else None)
        rec.add("REQ-MFG", o, d)
    return finish(rec, run_dir, t_all)


def finish(rec: RunRecord, run_dir: Path, t_start: float) -> RunRecord:
    rec.timings["total_wall_s"] = round(time.monotonic() - t_start, 1)
    inputs = collect_inputs({
        "schematic": run_dir / f"{STEM}.kicad_sch", "netlist": run_dir / f"{STEM}.net", "board": run_dir / f"{STEM}.kicad_pcb",
        "components": REPO_ROOT / "designs/fe-aaf-001/components.yaml", "placement": REPO_ROOT / "designs/fe-aaf-001/placement.yaml",
        "kicad_project": REPO_ROOT / "designs/fe-aaf-001/fe_aaf_001.kicad_pro", "requirements": REPO_ROOT / "requirements/fe-aaf-001.yaml",
        "pin_evidence": REPO_ROOT / "evidence/pins/OPA2314AID.yaml", "model_original": REPO_ROOT / "models/cache/OPA2314/x/opa2314.lib",
        "model_shim": REPO_ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib", "freerouting_jar": FREEROUTING_JAR,
        "dut_deck": run_dir / "work" / "ac_rs100_dut.cir", "checks_sim_code": REPO_ROOT / "src/ae/sim.py", "checks_metrics_code": REPO_ROOT / "src/ae/sim_metrics.py",
        "kicad_symbol_libs_tree": REPO_ROOT / "vendor/kicad-libs-10.0.8/symbols/Amplifier_Operational.kicad_sym"})
    summary: dict[str, int] = {}
    for c in rec.checks:
        summary[c.outcome] = summary.get(c.outcome, 0) + 1
    record = {"run_id": rec.run_id, "label": rec.label, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "environment": tool_versions(),
              "inputs_sha256": inputs, "summary": summary, "timings_s": rec.timings,
              "checks": [c.__dict__ for c in rec.checks],
              "stages": [{k: v for k, v in s.to_json().items() if k not in ("stdout", "stderr")} | {"stdout_tail": s.stdout[-1500:], "stderr_tail": s.stderr[-800:]} for s in rec.stages]}
    (run_dir / "record.json").write_text(json.dumps(record, indent=2, default=float))
    return rec


if __name__ == "__main__":
    import sys
    r = run_reference(Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "runs/ref/baseline")
    for c in r.checks:
        print(f"{c.id:<18}{c.outcome:<10}{c.detail[:110]}")
    print(r.timings)

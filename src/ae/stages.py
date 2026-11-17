"""Recorded pipeline stages for the reference design (each returns a StageRecord + outcome)."""
from __future__ import annotations

import json
from pathlib import Path

from ae.netlist import net_partition, read_netlist
from ae.toolchain import StageRecord, run_in_image


def kicad_erc(work: Path, sch: str) -> tuple[StageRecord, dict]:
    rec = run_in_image("erc", ["kicad-cli", "sch", "erc", "--format", "json", "--severity-all", "-o", "erc.json", sch], work)
    report = json.loads((work / "erc.json").read_text()) if (work / "erc.json").exists() else {}
    violations = [v for sheet in report.get("sheets", []) for v in sheet["violations"]]
    rec.outcome = "error" if rec.exit_code != 0 or not report else ("failed" if violations else "passed")
    return rec, {"violations": violations}


def kicad_netlist(work: Path, sch: str, out: str) -> StageRecord:
    rec = run_in_image("netlist", ["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch], work)
    rec.outcome = "passed" if rec.exit_code == 0 and (work / out).exists() else "error"
    return rec


def compare_netlist_to_intent(netlist_path: Path, intended: dict[str, set[tuple[str, str]]]) -> tuple[str, str]:
    """KiCad's exported connectivity (truth) vs the stated intent. Unconnected extra pins are reported, not ignored."""
    _, nets = read_netlist(netlist_path)
    actual = net_partition(nets)
    problems = []
    for name, pins in intended.items():
        got = actual.get(name)
        if got is None:
            problems.append(f"net {name!r} missing in KiCad netlist")
        elif set(got) != set(pins):
            problems.append(f"net {name!r}: missing={sorted(set(pins) - set(got))} extra={sorted(set(got) - set(pins))}")
    for name in actual:
        if name not in intended:
            problems.append(f"unexpected net {name!r}: {sorted(actual[name])}")
    return ("failed", "; ".join(problems)) if problems else ("passed", f"{len(intended)} nets / {sum(len(p) for p in intended.values())} pin connections identical to intent")

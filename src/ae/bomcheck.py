"""Schematic value / package / tolerance / dielectric / rating <-> MPN consistency check (reads KiCad's exported netlist).

The MPN is decoded with the manufacturer's own ordering scheme (Kemet MLCC "C0805C183J5GACTU"; Yageo RC/RT chip resistors) and compared with what the
schematic says. A swapped value (defect D1) leaves ERC/DRC/parity untouched but breaks this check. Per-line outcomes:
  passed | failed (a mismatch) | unresolved (no MPN selected / not decodable: NEVER counted as passed).
Overall: failed if any mismatch, else blocked if any line unresolved, else passed.
Scope limits: decodes only the families above; U1 is checked against a hand table; no stock, price or lifecycle; rating check uses an assumed per-net max voltage table.
"""
from __future__ import annotations

import re
from pathlib import Path

from ae.netlist import read_netlist
from ae.spice import parse_si

KEMET_TOL = {"F": 1.0, "G": 2.0, "J": 5.0, "K": 10.0, "M": 20.0}
KEMET_V = {"9": 6.3, "8": 10.0, "4": 16.0, "3": 25.0, "6": 35.0, "5": 50.0, "1": 100.0}
KEMET_DIEL = {"G": "C0G", "R": "X7R", "P": "X5R"}
RC_TOL = {"F": 1.0, "J": 5.0}
RT_TOL = {"L": 0.01, "W": 0.05, "B": 0.1, "C": 0.25, "D": 0.5, "F": 1.0}
RT_TCR = {"A": 5, "B": 10, "C": 15, "D": 25, "E": 50}
MAX_NET_V = {"+5V": 5.25, "VREF": 3.333, "VMID": 1.7}      # assumed worst applied voltages (contract supply/VREF corners); other signal nets <= 3.4 V
DEFAULT_NET_V = 3.4
HAND_TABLE = {"OPA2314AID": {"value": "OPA2314AID", "package": "SOIC-8", "source": "TI SBOS563G orderable table: OPA2314AID = SOIC (D) 8"}}


def resistance_from_code(code: str) -> float:
    m = re.fullmatch(r"(\d+)([RKM])(\d*)", code)
    if not m:
        raise ValueError(f"bad resistance code {code!r}")
    whole, unit, frac = m.groups()
    return float(f"{whole}.{frac or '0'}") * {"R": 1.0, "K": 1e3, "M": 1e6}[unit]


def decode(mpn: str) -> dict | None:
    m = re.fullmatch(r"C(\d{4})C(\d)(\d)(\d)([FGJKM])(\d)([GRP])A\w+", mpn)
    if m:
        size, d1, d2, mult, tol, v, diel = m.groups()
        return {"kind": "cap", "size": size, "value": int(d1 + d2) * 10 ** int(mult) * 1e-12, "tol": KEMET_TOL[tol], "volt": KEMET_V[v], "diel": KEMET_DIEL[diel], "family": "Kemet MLCC"}
    m = re.fullmatch(r"RC(\d{4})([FJ])R-07(\w+?)L", mpn)
    if m:
        size, tol, code = m.groups()
        return {"kind": "res", "size": size, "value": resistance_from_code(code), "tol": RC_TOL[tol], "family": "Yageo RC (thick film)"}
    m = re.fullmatch(r"RT(\d{4})([LWBCDF])R([ABCDE])07(\w+?)L", mpn)
    if m:
        size, tol, tcr, code = m.groups()
        return {"kind": "res", "size": size, "value": resistance_from_code(code), "tol": RT_TOL[tol], "tcr_ppm": RT_TCR[tcr], "family": "Yageo RT (thin film)"}
    return None


def _pct(text: str) -> float | None:
    m = re.fullmatch(r"\s*([\d.]+)\s*%?\s*", text or "")
    return float(m.group(1)) if m else None


def _volts(text: str) -> float | None:
    m = re.fullmatch(r"\s*([\d.]+)\s*V?\s*", text or "", re.I)
    return float(m.group(1)) if m else None


def check_line(ref: str, f: dict, value: str, footprint: str, applied_v: float) -> dict:
    mpn = f.get("MPN", "").strip()
    status = f.get("MPN_Status", "")
    out = {"refs": ref, "mpn": mpn, "evidence": status, "problems": [], "notes": []}
    if mpn in HAND_TABLE:
        h = HAND_TABLE[mpn]
        if value != h["value"]:
            out["problems"].append(f"value {value!r} != {h['value']!r}")
        if h["package"].lower().replace("-", "") not in footprint.lower().replace("-", "").replace("_", ""):
            out["problems"].append(f"footprint {footprint!r} is not {h['package']}")
        out["outcome"] = "failed" if out["problems"] else "passed"
        return out
    if not mpn or mpn.lower() in ("unselected", "unresolved") or mpn.lower().startswith("generic"):
        out.update(outcome="unresolved")
        out["notes"].append("no MPN selected: value/package/tolerance/rating cannot be tied to a part")
        return out
    d = decode(mpn)
    if d is None:
        out.update(outcome="unresolved")
        out["notes"].append("MPN not decodable by this check (family not supported)")
        return out
    try:
        sv = parse_si(value)
    except ValueError:
        out["problems"].append(f"schematic value {value!r} not parseable")
        sv = None
    if sv is not None and abs(sv - d["value"]) > 1e-3 * d["value"]:
        out["problems"].append(f"VALUE: schematic {value} but MPN {mpn} decodes to {d['value']:.6g}")
    pkg = re.search(r"_(\d{4})_", footprint)
    if not pkg or pkg.group(1) != d["size"]:
        out["problems"].append(f"PACKAGE: footprint {footprint} vs MPN size {d['size']}")
    want_tol = _pct(f.get("Tolerance", ""))
    if want_tol is None:
        out["problems"].append("schematic has no Tolerance field")
    elif d["tol"] > want_tol + 1e-9:
        out["problems"].append(f"TOLERANCE: MPN is +-{d['tol']}% but schematic requires +-{want_tol}%")
    elif d["tol"] < want_tol:
        out["notes"].append(f"MPN tolerance +-{d['tol']}% is tighter than the +-{want_tol}% assumption")
    if d["kind"] == "cap":
        if f.get("Dielectric", "") != d["diel"]:
            out["problems"].append(f"DIELECTRIC: schematic {f.get('Dielectric') or '(none)'} vs MPN {d['diel']}")
        req_v = _volts(f.get("Voltage", ""))
        if req_v is not None and d["volt"] + 1e-9 < req_v:
            out["problems"].append(f"RATING: MPN {d['volt']:g} V < schematic {req_v:g} V")
        if d["volt"] < applied_v:
            out["problems"].append(f"RATING: MPN {d['volt']:g} V < max applied {applied_v:g} V")
        elif d["diel"] != "C0G" and applied_v / d["volt"] > 0.5:
            # NOT a failure and NOT a contract requirement. The requirement that matters is functional: effective capacitance under DC bias (and temperature/age)
            # must stay above what the circuit needs for that role (see docs/stability-and-capacitor-notes.md). The manufacturer bias curve was not obtained.
            out["notes"].append(f"rating margin note: {applied_v:g} V applied = {100*applied_v/d['volt']:.0f}% of the {d['volt']:g} V rating; effective-capacitance coverage under bias MISSING (no manufacturer curve)")
    out["outcome"] = "failed" if out["problems"] else "passed"
    return out


def check_netlist(netlist: Path) -> tuple[str, str, list[dict]]:
    comps, nets = read_netlist(netlist)
    pin_nets: dict[str, set[str]] = {}
    for net, nodes in nets.items():
        for ref, _, _ in nodes:
            pin_nets.setdefault(ref, set()).add(net.lstrip("/"))
    groups: dict[tuple, list[dict]] = {}
    for c in comps:
        if c["ref"].startswith(("TP", "#")):
            continue
        key = (c["value"], c["footprint"], c["fields"].get("MPN", ""), c["fields"].get("Tolerance", ""), c["fields"].get("Dielectric", ""), c["fields"].get("Voltage", ""))
        groups.setdefault(key, []).append(c)
    lines = []
    for group in groups.values():
        worst_v = max((MAX_NET_V.get(n, DEFAULT_NET_V) for c in group for n in pin_nets.get(c["ref"], ()) if n != "GND"), default=DEFAULT_NET_V)
        c = group[0]
        lines.append(check_line(",".join(g["ref"] for g in group), c["fields"], c["value"], c["footprint"], worst_v))
    bad = [l for l in lines if l["outcome"] == "failed"]
    open_ = [l for l in lines if l["outcome"] == "unresolved"]
    detail = f"{len(lines)} lines: {sum(l['outcome']=='passed' for l in lines)} consistent, {len(bad)} MISMATCH, {len(open_)} unresolved"
    if bad:
        detail += " | " + "; ".join(f"{l['refs']}: {'; '.join(l['problems'])}" for l in bad)
    if open_:
        detail += " | unresolved: " + ", ".join(l["refs"] for l in open_)
    warn = [f"{l['refs']}: {n}" for l in lines for n in l["notes"] if "rating margin note" in n]
    if warn:
        detail += " | " + "; ".join(warn)
    outcome = "failed" if bad else ("blocked" if open_ else "passed")
    return outcome, detail, lines


if __name__ == "__main__":
    import sys
    o, d, lines = check_netlist(Path(sys.argv[1]))
    print(o, "-", d)

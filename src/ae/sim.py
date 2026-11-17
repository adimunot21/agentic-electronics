"""ngspice test benches + requirement evaluation for FE-AAF-001.

Every analysis writes raw data (wrdata CSV + ngspice stdout) into the run directory so a result can be re-derived.
Outcomes per requirement: passed | failed | blocked | not_tested. A measurement the model cannot support is
`not_tested`, never `passed`.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from ae import sim_metrics as sm
from ae.spice import build_dut_deck
from ae.toolchain import REPO_ROOT, StageRecord, run_in_image

SIM_TIMEOUT_S = 60
STOP_AFTER_CONSECUTIVE_BLOCKED = 2     # progress/stop rule for corner sweeps
MODEL_SHIM = "opa2314_ngspice_shim.lib"
VOS_MAX_V = 2.5e-3          # datasheet SBOS563G p.7: |Vos| max 2.5 mV @25C; the model only reproduces ~typical
SUPPLY = (4.75, 5.25)
VREF = (3.267, 3.333)
RB_TOL = 0.001              # 0.1 % bias divider resistors


@dataclass
class Measurement:
    req: str
    metric: str
    value: float | None
    limit: str
    outcome: str
    detail: str = ""
    margin: float | None = None


@dataclass
class SimRun:
    work: Path
    netlist: Path
    stages: list[StageRecord] = field(default_factory=list)
    measurements: list[Measurement] = field(default_factory=list)
    deck_kw: dict = field(default_factory=dict)     # default perturbations (e.g. {'scale': {'C5': 0.22}}) applied to every deck
    evidence: dict = field(default_factory=lambda: yaml.safe_load((REPO_ROOT / "evidence/pins/OPA2314AID.yaml").read_text()))

    def deck(self, **kw) -> str:
        merged = {**self.deck_kw, **kw}
        if "scale" in self.deck_kw and "scale" in kw:
            merged["scale"] = {**self.deck_kw["scale"], **kw["scale"]}
        return build_dut_deck(self.netlist, self.evidence, MODEL_SHIM, **merged)

    def prepare(self) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / ".spiceinit").write_text("set ngbehavior=psa\n")            # must exist before the netlist is read
        (self.work / MODEL_SHIM).write_text((REPO_ROOT / "models/cache/OPA2314" / MODEL_SHIM).read_text())

    def ng(self, name: str, tb: str, deck: str | None = None) -> StageRecord:
        (self.work / f"{name}_dut.cir").write_text(deck or self.deck())
        (self.work / f"{name}.cir").write_text(tb.replace("@DUT@", f"{name}_dut.cir"))
        # 60 s hard limit (normal runs take 1-5 s): a latched/non-converging circuit must end as `blocked`, not hang for the default 600 s
        rec = run_in_image(name, ["ngspice", "-b", "-o", f"{name}.log", f"{name}.cir"], self.work, timeout_s=SIM_TIMEOUT_S)
        out = (self.work / f"{name}.log").read_text() if (self.work / f"{name}.log").exists() else rec.stdout
        rec.stdout = out
        fallback = "Transient op" in out
        rec.outcome = "blocked" if rec.exit_code == 124 else "error" if rec.exit_code != 0 else ("blocked" if fallback else "passed")
        if rec.exit_code == 124:
            rec.stderr += f"\nBLOCKED: ngspice exceeded {SIM_TIMEOUT_S} s (no convergence); container killed"
        if fallback:
            rec.stderr += "\nBLOCKED: ngspice fell back to 'transient op' (no true DC operating point); results not trusted"
        self.stages.append(rec)
        return rec

    def add(self, *args, **kw) -> Measurement:
        m = Measurement(*args, **kw)
        self.measurements.append(m)
        return m


def header(vcc=5.0, vref=3.3, rs=100.0, src="DC 0 AC 1") -> str:
    return f"""* test bench (generated)
.include @DUT@
Vcc P5V 0 DC {vcc}
Vref VREF 0 DC {vref}
Vsrc src 0 {src}
Rs src AIN {rs}
"""


def scan_vals(text: str, names: list[str]) -> dict[str, float]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([a-z_0-9()]+)\s*=\s*([-+0-9.e]+)\s*$", line, re.I)
        if m and m.group(1).lower() in names:
            out[m.group(1).lower()] = float(m.group(2))
    return out


# ---------------------------------------------------------------- analyses
def dc_corners(run: SimRun, req_bias, req_iq) -> None:
    worst_err, worst_iq, worst_cfg, blocked, streak, ran = 0.0, 0.0, None, 0, 0, 0
    for vcc, vref, sa, sb, rbs in itertools.product(SUPPLY, VREF, (-1, 1), (-1, 1), (-1, 1)):
        if streak >= STOP_AFTER_CONSECUTIVE_BLOCKED:      # explicit stop rule: no operating point twice in a row -> stop burning time
            blocked += 1
            continue
        scale = {"R7": 1 + rbs * RB_TOL, "R8": 1 - rbs * RB_TOL}
        deck = run.deck(vos_a=sa * VOS_MAX_V, vos_b=sb * VOS_MAX_V, scale=scale)
        name = f"dc_{vcc}_{vref}_{sa:+d}{sb:+d}{rbs:+d}".replace(".", "p").replace("+", "p").replace("-", "m")
        tb = header(vcc, vref, src="DC 0") + ".control\nop\nprint v(aout) i(vcc)\n.endc\n.end\n"
        rec = run.ng(name, tb, deck)
        ran += 1
        if rec.outcome != "passed":
            blocked += 1
            streak += 1
            continue
        streak = 0
        vals = scan_vals(rec.stdout, ["v(aout)", "i(vcc)"])
        err = (vals["v(aout)"] - vref / 2) * 1e3
        if abs(err) > abs(worst_err):
            worst_err, worst_cfg = err, (vcc, vref, sa, sb, rbs)
        worst_iq = max(worst_iq, abs(vals["i(vcc)"]) * 1e3)
    if blocked:
        run.add("REQ-BIAS", "output_dc_error_mV_max_abs", None, "<= 10", "blocked", f"{blocked} of 32 corner runs have no DC operating point ({ran} attempted; stopped after {STOP_AFTER_CONSECUTIVE_BLOCKED} consecutive blocked)")
        run.add("REQ-IQ", "iq_mA_max", None, "<= 1.0", "blocked", f"{blocked} corner runs blocked")
        return
    run.add("REQ-BIAS", "output_dc_error_mV_max_abs", abs(worst_err), "<= 10", "passed" if abs(worst_err) <= 10 else "failed",
            f"worst of 32 corners (supply x VREF x Vos_A x Vos_B x divider skew): {worst_cfg}; Vos corners = +-2.5 mV injected (datasheet max)", 10 - abs(worst_err))
    run.add("REQ-IQ", "iq_mA_max", worst_iq, "<= 1.0", "passed" if worst_iq <= 1.0 else "failed",
            "model Iq (typical per datasheet); datasheet MAX 190 uA/amp would give 0.38 mA: analysis shows margin either way", 1.0 - worst_iq)


def ac_run(run: SimRun, ideal_fn, rs: float, tag: str) -> dict:
    tb = header(rs=rs, src="DC 0 AC 1") + ".control\nop\nac dec 40 10 10Meg\nwrdata ac.csv v(src) v(ain) v(aout)\n.endc\n.end\n"
    rec = run.ng(f"ac_{tag}", tb.replace("ac.csv", f"ac_{tag}.csv"))
    if rec.outcome != "passed":
        return {"blocked": True}
    f, (vsrc, vain, vaout) = sm.read_wrdata(run.work / f"ac_{tag}.csv", 3, True)
    return sm.ac_metrics(f, vsrc, vain, vaout, rs_ohm=rs, ideal_mag=np.abs(ideal_fn(f, rs)))


def tran_headroom(run: SimRun) -> dict:
    result = {"min": 9.0, "max": -9.0, "n": 0, "blocked": 0}
    for f_hz, tstop, tmax in ((1000, 8e-3, 2e-6), (50, 0.12, 20e-6), (400, 20e-3, 5e-6)):
        for vcc, vref in itertools.product(SUPPLY, VREF):
            tb = header(vcc, vref, src=f"DC 0 SIN(0 1.0 {f_hz})") + f".control\ntran {tmax} {tstop} 0 {tmax}\nwrdata tr.csv v(aout)\n.endc\n.end\n"
            name = f"tran_{f_hz}_{vcc}_{vref}".replace(".", "p")
            rec = run.ng(name, tb.replace("tr.csv", f"{name}.csv"))
            if rec.outcome != "passed":
                result["blocked"] += 1
                continue
            t, (v,) = sm.read_wrdata(run.work / f"{name}.csv", 1, False)
            m = sm.tran_metrics(t, v)
            result["min"], result["max"], result["n"] = min(result["min"], m["output_min_V"]), max(result["max"], m["output_max_V"]), result["n"] + 1
    return result


def noise_run(run: SimRun) -> float | None:
    tb = header(src="DC 0 AC 1") + ".control\nnoise v(aout) Vsrc dec 20 1 20k\nprint onoise_total\n.endc\n.end\n"
    rec = run.ng("noise", tb)
    if rec.outcome != "passed":
        return None
    v = scan_vals(rec.stdout, ["onoise_total"])
    return v.get("onoise_total")


def settling_run(run: SimRun, cs_pf: float = 10.0, ron_ohm: float = 200.0) -> tuple[float | None, str]:
    base = header(src="DC 0 SIN(0 1.0 1000)")
    ref_tb = base + ".control\ntran 1u 4.5m 0 1u\nwrdata ref.csv v(aout)\n.endc\n.end\n"
    rec = run.ng("settle_ref", ref_tb)
    if rec.outcome != "passed":
        return None, "reference transient blocked"
    t, (v,) = sm.read_wrdata(run.work / "ref.csv", 1, False)
    window = (t >= 3e-3) & (t <= 4e-3)
    t_peak, v_min = float(t[window][np.argmax(v[window])]), float(np.min(v[window]))
    tb = (base + f".model SWM SW(Ron={ron_ohm} Roff=1G Vt=0.5 Vh=0.05)\nSsamp AOUT cs ctl 0 SWM\nCs cs 0 {cs_pf}p\n"
          f"Vctl ctl 0 PULSE(0 1 {t_peak:.9e} 1n 1n 1u 1)\n.ic v(cs)={v_min:.6f}\n"
          f".control\ntran 1u {t_peak + 2e-6:.9e} 0 1u\nwrdata ld.csv v(cs)\n.endc\n.end\n")
    rec = run.ng("settle_loaded", tb)
    if rec.outcome != "passed":
        return None, "loaded transient blocked"
    tl, (vcs,) = sm.read_wrdata(run.work / "ld.csv", 1, False)
    err = sm.acquisition_error_mv(tl, vcs, t, v, t_peak + 1e-6)
    return err, f"sample cap precharged to {v_min:.3f} V, acquisition at the {np.max(v[window]):.3f} V peak (t={t_peak*1e3:.4f} ms), 1 us"


def load_ideal():
    spec = importlib.util.spec_from_file_location("derive_filter", REPO_ROOT / "analysis" / "derive_filter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    params = json.loads((REPO_ROOT / "analysis" / "filter_design.json").read_text())["params"]
    return lambda f, rs: mod.ideal_total_response(f, params, rs=rs)


def evaluate_all(run: SimRun) -> list[Measurement]:
    contract = {r["id"]: r for r in yaml.safe_load((REPO_ROOT / "requirements/fe-aaf-001.yaml").read_text())["requirements"]}
    run.prepare()
    dc_corners(run, contract["REQ-BIAS"], contract["REQ-IQ"])
    ideal = load_ideal()
    ac_hi, ac_lo = ac_run(run, ideal, 100.0, "rs100"), ac_run(run, ideal, 0.01, "rs0")
    if ac_hi.get("blocked") or ac_lo.get("blocked"):
        for r in ("REQ-GAIN", "REQ-FLAT", "REQ-ALIAS", "REQ-ZIN", "REQ-STAB"):
            run.add(r, "ac", None, "", "blocked", "AC run blocked (no DC operating point)")
    else:
        g = [ac_hi["gain_400hz_V_per_V"], ac_lo["gain_400hz_V_per_V"]]
        c = contract["REQ-GAIN"]["check"]
        ok = all(c["min"] <= x <= c["max"] for x in g)
        run.add("REQ-GAIN", "gain_400hz_V_per_V", min(g, key=lambda x: abs(x - 1.3)), f"{c['min']}..{c['max']}", "passed" if ok else "failed", f"Rs=100: {g[0]:.4f}, Rs=0: {g[1]:.4f} (nominal parts)")
        fl = max(ac_hi["passband_flatness_dB_max_abs"], ac_lo["passband_flatness_dB_max_abs"])
        run.add("REQ-FLAT", "passband_flatness_dB_max_abs", fl, "<= 0.5", "passed" if fl <= 0.5 else "failed", "nominal parts, Rs 0 and 100", 0.5 - fl)
        st = max(ac_hi["stopband_abs_gain_dB_max_19k_to_1M"], ac_lo["stopband_abs_gain_dB_max_19k_to_1M"])
        run.add("REQ-ALIAS", "stopband_abs_gain_dB_max_19k_to_1M", st, "<= -55.8", "passed" if st <= -55.8 else "failed", "19 kHz..1 MHz, nominal parts, real op-amp model", -55.8 - st)
        run.add("REQ-ZIN", "zin_min_ohm", ac_hi["zin_min_ohm"], ">= 10000", "passed" if ac_hi["zin_min_ohm"] >= 1e4 else "failed", "50 Hz-1 kHz, Rs=100")
        pk = max(ac_hi["peaking_above_ideal_dB_max"], ac_lo["peaking_above_ideal_dB_max"])
        run.add("REQ-STAB", "peaking_above_ideal_dB_max", pk, "<= 3.0", "passed" if pk <= 3.0 else "failed", f"10 Hz-10 MHz vs ideal-op-amp response; max excess at {ac_hi['peaking_at_hz']:.0f} Hz (sim {ac_hi['gain_at_peak_dB']:.1f} dB vs ideal {ac_hi['ideal_at_peak_dB']:.1f} dB); transient ringing not tested", 3.0 - pk)
    hr = tran_headroom(run)
    if hr["blocked"] or hr["n"] == 0:
        run.add("REQ-HEADROOM", "output_range_V", None, "0.10..3.20", "blocked", f"{hr['blocked']} runs blocked")
    else:
        ok = hr["min"] >= 0.10 and hr["max"] <= 3.20
        run.add("REQ-HEADROOM", "output_range_V", None, "0.10..3.20", "passed" if ok else "failed", f"1.0 Vpk at 50/400/1000 Hz, supply and VREF corners: min {hr['min']:.3f} V, max {hr['max']:.3f} V (n={hr['n']})", min(hr["min"] - 0.10, 3.20 - hr["max"]))
    err, note = settling_run(run)
    run.add("REQ-SETTLE", "acquisition_error_mV_max_abs", None if err is None else abs(err), "<= 0.403", "blocked" if err is None else ("passed" if abs(err) <= 0.403 else "failed"), note, None if err is None else 0.403 - abs(err))
    n = noise_run(run)
    run.add("REQ-NOISE", "output_noise_mV_rms", None if n is None else n * 1e3, "<= 0.2", "blocked" if n is None else ("passed" if n * 1e3 <= 0.2 else "failed"),
            "1 Hz-20 kHz incl. 100 ohm source; model carries noise sources (datasheet en 14 nV/rtHz @1k); not validated against hardware", None if n is None else 0.2 - n * 1e3)
    return run.measurements

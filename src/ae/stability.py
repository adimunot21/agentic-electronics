"""Circuit-specific STABILITY EVIDENCE benches for FE-AAF-001: loop gain of each op-amp stage, an input step, and a separate load step.

STATUS: measurement benches only. NO pass/fail thresholds are adopted here and nothing in this module approves REQ-STAB revision 3. REQ-STAB (rev 2, in force)
stays FAILED in the run record; these results are evidence a reviewer may use to judge what a stability requirement should be.

Loop gain (series voltage injection at the op-amp INPUT PIN): the amplifier's inverting-input pin is moved from the feedback node `n` to a new node `nA` and an AC
source Vinj sits between them, V(nA) - V(n) = Vinj. With the chain gain G = V(n)/V(nA) (amplifier, output network, back to `n`), the loop gain is T = -G, positive and large at
low frequency for negative feedback. WHY THE INPUT PIN: single voltage injection is exact only if the impedance looking INTO the receiving side is >> the driving side's. At the
input pin the receiver is a CMOS gate (~infinite) and the driver is the network node, so it holds at every frequency. (A first version broke the loop at the op-amp OUTPUT, where
the stage-1 feedback network is C2 = 53 ohm at the 3 MHz crossover, i.e. LOWER than the model's output impedance: that assumption fails exactly where the phase margin is read.)
Input step: 0.2 V at the source (through Rs = 100 ohm), compared against the SAME netlist with ideal op-amps (VCVS gain 1e6).
Load step: 50 uA current sink pulse on AOUT with the input quiet, compared against the same ideal-op-amp netlist.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from ae import sim_metrics as sm
from ae.sim import SimRun, header

STAGES = {"stage1_MFB(U1A)": ("XU1A", "N_INV"), "stage2_1st_order(U1B)": ("XU1B", "N_INV2")}      # (op-amp instance, its inverting-input node)


# ----------------------------------------------------------------------------- deck transforms
def inject_series(deck: str, element: str, node: str, tag: str) -> str:
    """Move `element`'s connection to `node` onto `<node>A` and put the AC injection source between them: V(<node>A) - V(node) = Vinj."""
    lines, hit = [], False
    for line in deck.splitlines():
        tok = line.split()
        if tok and tok[0] == element:
            if node not in tok[1:]:
                raise ValueError(f"{element} is not connected to {node}: {line!r}")
            tok = [tok[0]] + [t + "A" if t == node else t for t in tok[1:]]
            line, hit = " ".join(tok), True
        lines.append(line)
    if not hit:
        raise ValueError(f"element {element} not found in deck")
    lines.append(f"Vinj_{tag} {node}A {node} DC 0 AC 1")
    return "\n".join(lines) + "\n"


def ideal_opamp_deck(deck: str) -> str:
    """Replace each OPA2314 instance by an ideal VCVS (gain 1e6) with the same in+/in-/out nodes: the SAME netlist with ideal op-amps."""
    out = []
    for line in deck.splitlines():
        m = re.fullmatch(r"X(\S+) (\S+) (\S+) (\S+) (\S+) (\S+) OPA2314", line.strip())
        out.append(f"E{m.group(1)} {m.group(6)} 0 {m.group(2)} {m.group(3)} 1e6" if m else line)
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- pure metrics (unit-tested)
def loop_margins(f: np.ndarray, T: np.ndarray) -> dict:
    """Crossover (last downward |T| = 1), phase margin, gain margin (first -180 deg crossing). T must be the loop gain with T > 0 at DC."""
    T = np.asarray(T, dtype=complex)
    if abs(np.degrees(np.angle(T[0]))) > 135:       # sign-flipped input only (phase near +-180 at the lowest frequency). NOT `real < 0`: a loop whose
        T = -T                                      # dominant pole already gives -90 deg at the first point has a tiny NEGATIVE real part (bug: PM 250 deg)
    mag, ph = np.abs(T), np.unwrap(np.angle(T)) * 180 / np.pi
    down = np.where((mag[:-1] >= 1) & (mag[1:] < 1))[0]
    out = {"dc_loop_gain_dB": float(20 * np.log10(mag[0])), "crossover_hz": None, "phase_margin_deg": None, "gain_margin_dB": None, "phase_cross_hz": None}
    if len(down):
        i = down[-1]
        frac = (0 - np.log10(mag[i])) / (np.log10(mag[i + 1]) - np.log10(mag[i]))
        fc = 10 ** (np.log10(f[i]) + frac * (np.log10(f[i + 1]) - np.log10(f[i])))
        out["crossover_hz"] = float(fc)
        out["phase_margin_deg"] = float(180 + np.interp(np.log10(fc), np.log10(f), ph))
    cross = np.where((ph[:-1] > -180) & (ph[1:] <= -180))[0]
    if len(cross):
        i = cross[0]
        frac = (-180 - ph[i]) / (ph[i + 1] - ph[i])
        fp = 10 ** (np.log10(f[i]) + frac * (np.log10(f[i + 1]) - np.log10(f[i])))
        out["phase_cross_hz"] = float(fp)
        out["gain_margin_dB"] = float(-np.interp(np.log10(fp), np.log10(f), 20 * np.log10(mag)))
    return out


def step_metrics(t, v, v_ideal, t_step: float, window_end: float) -> dict:
    """Input-step comparison against the ideal-op-amp response on a common grid (all in mV unless stated)."""
    grid = np.linspace(t_step - 20e-6, window_end, 6000)
    a, b = np.interp(grid, t, v), np.interp(grid, t, v_ideal)
    pre = grid < t_step
    a, b = a - a[pre].mean(), b - b[pre].mean()
    diff = a - b
    post = grid > t_step
    step_out = float(b[post][-len(b[post]) // 10:].mean())          # ideal response level late in the window (output step size incl. AC-coupling droop)
    tail = grid > t_step + 0.5 * (window_end - t_step)
    return {"output_step_mV_ideal_late": 1e3 * step_out, "max_abs_diff_mV": 1e3 * float(np.abs(diff[post]).max()), "max_abs_diff_pct_of_step": 100 * float(np.abs(diff[post]).max() / abs(step_out)),
            "overshoot_pct_sim": 100 * float((a[post].max() - step_out) / abs(step_out)), "overshoot_pct_ideal": 100 * float((b[post].max() - step_out) / abs(step_out)),
            "residual_pp_mV_second_half_diff": 1e3 * float(diff[tail].max() - diff[tail].min())}


def release_metrics(t, v, t_off: float, v0: float, band_mV: float = 0.05) -> dict:
    """After the load is released AOUT should return to its unloaded level v0: peak excursion beyond v0, and time to stay within +-band (mV, us)."""
    t, v = np.asarray(t), np.asarray(v)
    seg = t >= t_off
    err = v[seg] - v0
    tt = t[seg] - t_off
    outside = np.where(np.abs(err) > band_mV * 1e-3)[0]
    return {"max_excursion_mV": 1e3 * float(np.abs(err).max()), "return_to_band_us": 1e6 * (float(tt[outside[-1]]) if len(outside) else 0.0), "final_error_mV": 1e3 * float(err[-20:].mean())}


def load_step_metrics(t, v, t_on: float, t_off: float, band_mV: float = 0.05) -> dict:
    """Response of AOUT to a current-sink pulse: static droop, peak, overshoot beyond the static value, recovery time to +-band, ring count (mV, us)."""
    t, v = np.asarray(t), np.asarray(v)
    v0 = v[(t > t_on - 10e-6) & (t < t_on - 2e-6)].mean()
    vl = v[(t > t_off - 10e-6) & (t < t_off - 2e-6)].mean()
    static = vl - v0
    seg = (t >= t_on) & (t <= t_off)
    dev = v[seg] - v0
    tt = t[seg] - t_on
    peak = dev[np.argmax(np.abs(dev))]
    over = (np.abs(peak) - abs(static)) if np.sign(peak) == np.sign(static) else np.abs(peak)
    err = dev - static
    outside = np.where(np.abs(err) > band_mV * 1e-3)[0]
    settle = float(tt[outside[-1]]) if len(outside) else 0.0
    sign_changes = int(np.sum(np.diff(np.sign(err[np.abs(err) > band_mV * 1e-3])) != 0)) if (np.abs(err) > band_mV * 1e-3).any() else 0
    return {"static_droop_mV": 1e3 * float(static), "peak_dev_mV": 1e3 * float(peak), "overshoot_beyond_static_mV": 1e3 * float(over), "recovery_to_band_us": 1e6 * settle, "band_mV": band_mV, "sign_changes_outside_band": sign_changes}


# ----------------------------------------------------------------------------- benches (ngspice)
def loop_gain(run: SimRun, stage: str) -> dict:
    element, node = STAGES[stage]
    tag = re.sub(r"\W", "", stage)[:6]
    deck = inject_series(run.deck(), element, node, tag)
    tb = header(src="DC 0") + f".control\nop\nac dec 50 10 100Meg\nwrdata lg_{tag}.csv v({node}) v({node}A)\n.endc\n.end\n"
    rec = run.ng(f"lg_{tag}", tb, deck)
    if rec.outcome != "passed":
        return {"stage": stage, "outcome": rec.outcome}
    f, (vn, va) = sm.read_wrdata(run.work / f"lg_{tag}.csv", 2, True)
    T = -vn / va                                   # T = -G, G = V(n)/V(nA)
    m = loop_margins(f, T)
    m.update({"stage": stage, "outcome": "measured", "T_dB_at": {str(int(x)): float(20 * np.log10(abs(np.interp(np.log10(x), np.log10(f), np.abs(T))))) for x in (100, 1e3, 1e4, 1e5, 1e6)}})
    np.savetxt(run.work / f"lg_{tag}_T.csv", np.c_[f, 20 * np.log10(np.abs(T)), np.unwrap(np.angle(T)) * 180 / np.pi], delimiter=",", header="f_Hz,T_dB,T_phase_deg", comments="")
    return m


def input_step(run: SimRun, amp: float = 0.2) -> dict:
    t_step, t_end = 100e-6, 4e-3
    res = {}
    for label, deck in (("real", run.deck()), ("ideal", ideal_opamp_deck(run.deck()))):
        tb = header(src=f"DC 0 PULSE(0 {amp} {t_step} 1n 1n 1)") + f".control\ntran 1u {t_end} 0 1u\nwrdata st_{label}.csv v(aout)\n.endc\n.end\n"
        rec = run.ng(f"st_{label}", tb, deck)
        if rec.outcome != "passed":
            return {"outcome": rec.outcome, "which": label}
        res[label] = sm.read_wrdata(run.work / f"st_{label}.csv", 1, False)
    (t, (v,)), (ti, (vi,)) = res["real"], res["ideal"]
    m = step_metrics(t, v, np.interp(t, ti, vi), t_step, t_end)
    m.update({"outcome": "measured", "input_step_V": amp})
    return m


def load_step(run: SimRun, amp: float = 50e-6) -> dict:
    t_on, t_off, t_end = 20e-6, 60e-6, 100e-6
    res = {}
    for label, deck in (("real", run.deck()), ("ideal", ideal_opamp_deck(run.deck()))):
        tb = header(src="DC 0") + f"Iload AOUT 0 PULSE(0 {amp} {t_on} 10n 10n {t_off - t_on} 1)\n.control\ntran 20n {t_end} 0 20n\nwrdata ld_{label}.csv v(aout)\n.endc\n.end\n"
        rec = run.ng(f"ld_{label}", tb, deck)
        if rec.outcome != "passed":
            return {"outcome": rec.outcome, "which": label}
        res[label] = sm.read_wrdata(run.work / f"ld_{label}.csv", 1, False)
    out = {"outcome": "measured", "load_step_A": amp}
    for label, (t, (v,)) in res.items():
        v0 = float(np.asarray(v)[(np.asarray(t) > t_on - 10e-6) & (np.asarray(t) < t_on - 2e-6)].mean())
        out[label] = load_step_metrics(t, v, t_on, t_off)
        out[label]["release"] = release_metrics(t, v, t_off, v0)
    return out


def run_all(netlist: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    run = SimRun(work=out / "work", netlist=netlist)
    run.prepare()
    results = {"loop_gain": {s: loop_gain(run, s) for s in STAGES}, "input_step": input_step(run), "load_step": load_step(run)}
    (out / "stability_results.json").write_text(json.dumps(results, indent=1, default=float))
    return results


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parents[2]
    rec_path = root / "runs/ref/baseline/record.json"
    if rec_path.exists():     # keep the in-force failure visible in front of every result
        checks = {c["id"]: c for c in json.loads(rec_path.read_text())["checks"]}
        print(f"REQ-STAB (rev 2, IN FORCE): {checks['REQ-STAB']['outcome'].upper()}  value {checks['REQ-STAB']['value']:.1f} dB vs <= 3.0 | "
              f"rev 3 (PROPOSED, NOT APPROVED, AC-peaking only): {checks['REQ-STAB-R3-AC-PEAKING-PROPOSED']['outcome']}  |  results below are EVIDENCE with NO adopted thresholds\n")
    res = run_all(Path(sys.argv[1] if len(sys.argv) > 1 else root / "runs/ref/baseline/fe_aaf_001.net"), Path(sys.argv[2] if len(sys.argv) > 2 else root / "runs/ref/stability"))
    print(json.dumps(res, indent=1, default=float))

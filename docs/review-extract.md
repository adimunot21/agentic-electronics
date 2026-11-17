# Review extract (2026-11-17): D1 result, model/patch, REQ-STAB, settling + Monte Carlo, BOM verification

Raw evidence: `runs/ref/baseline/record.json`, `runs/ref/defects/D1_swap_C1_C2_values/`, `runs/ref/review/` (one-off scripts + outputs), `evidence/bom-verification.yaml`.

## 0. D1 (values of C1 = 18 nF [node X to GND] and C2 = 1 nF [MFB feedback] swapped in the schematic)
Your prediction held: connectivity checks stay clean, response checks fail. Why each check came out as it did (D1 record vs baseline):

| Check | D1 | Why |
|---|---|---|
| ERC, netlist-vs-intent, pin audit (symbol/footprint/header/wiring), deck-vs-netlist, DRC, parity | passed | They compare connectivity, pin numbers and geometry. Two capacitors with swapped *values* are the same nets and pins. |
| REQ-GAIN | **failed**, 0.2748 (Rs=0) / 0.2744 (Rs=100) vs 1.235-1.365 | Damping term of the MFB stage is C2*(R2+R3+R2R3/R1): C2 18x larger and C1 18x smaller makes Q about 1/18 of design (Q~1 -> ~0.06): two real poles, the lower far below 400 Hz. |
| REQ-FLAT | **failed**, 12.32 dB vs 0.5 | Same cause: the passband is no longer flat. Analytic ideal model (derive_filter.py, written before the run) predicted gain 0.274, flatness 12.3 dB. |
| REQ-ALIAS | passed, -68.3 dB (baseline -63.5) | The wrong network attenuates *more* everywhere, so a stopband-only test cannot see this defect. |
| REQ-ZIN | passed, 66.2 kohm | Input impedance is set by C4 + R1; the swapped capacitors are behind R1. |
| REQ-BIAS, REQ-IQ | passed | DC: capacitors are open; same operating point. |
| REQ-HEADROOM | passed, 0.483-2.787 V | Output swing shrinks with the gain, so it is *more* inside the limits. |
| REQ-SETTLE | passed, 0.0008 mV | Vacuous here: the output is ~1.76 V peak, so the sampling step and kickback are small. |
| REQ-NOISE | passed, 3.2 uV | Lower gain, lower output noise. |
| REQ-STAB | passed (that record used the proposed rev-3 form, -0.05 dB) | The metric compares to the ideal response of the *intended* values; a response lower than ideal can never exceed it. It cannot see value errors. |
| REQ-BOM | failed (as baseline) | Unrelated. |

Gap this exposed: swapping the `Value` text did **not** swap the `MPN` fields, so the BOM lists C1 = "1n" with the 18 nF part's MPN. No check compares a BOM value with its MPN's decoded value; one would have caught D1 at the BOM stage. Not built (no new infrastructure requested).
Also: the defect runs predate the REQ-STAB split (section 2) and the BOM verification; their records are bound to the older input hashes.

## 1. TI model identity, source, patch
* **Part/model:** TI OPA2314 PSpice model, file header `* OPA2314 - Rev. C`, "Created by Paul Goedeke; October 15, 2018 - Revised by GPAMPS Team; 2021-07-16", "Green-Williams-Lis Op Amp Macro-model Architecture", "Copyright 2018 by Texas Instruments Corporation". Single-channel `.subckt OPA2314 IN+ IN- VCC VEE OUT` (the dual part is two instances).
* **Source:** `https://www.ti.com/lit/zip/sbombu1` -> `https://www.ti.com/lit/mo/sbombu1a/sbombu1a.zip` (TI product page lists it as SBOMBU1A, PSpice model), HTTP `last-modified: Wed, 10 Aug 2022`, retrieved 2026-11-17. Archive sha256 `3e0fc936312ab6de2d18fd33308bb71ed7d60b27f2f3bae7dbf7fc477ef0ca3b`; `opa2314.lib` (CRLF, as shipped) sha256 `36b70848dc6b35224529b58870803b26e5d33b0739786938c149835c03a031fe`; patched LF file `8ad0db9d5272a7801707a209fd3212d0b1517dc40bba8428339a50c71e9b5cb9`. **Licence: none stated in the file;** TI's model terms were not retrieved. Not committed.
* **Patch (the whole diff, after CRLF->LF):** line 204
  `-X_SW_OR             CLAMP N166377 N166817 SW_OR`
  `+* NGSPICE-SHIM (patched out, see make_ngspice_shim.py): X_SW_OR             CLAMP N166377 N166817 SW_OR`
* **What the patched-out cell does:** `.subckt SW_OR CLAMP OLN OLP` = two voltage-controlled switches (Ron 10 mohm, Roff 1 Gohm, Von 10 mV) that tie the internal `CLAMP` node to the overload-limit nodes OLP/OLN when it moves more than 10 mV beyond them: the overload-recovery clamp. Without it that internal node is not bounded when the output saturates, so **saturation/overload recovery behaviour changes; it was not tested** (the unpatched model cannot reach a DC point, so a saturation comparison was not possible). The design claims nothing about overload.
* **Evidence the patch does not change linear behaviour** (follower, RL = 10 k to mid-supply, CL = 10 pF, `runs/ref/review/model/`): original vs shim give identical -3 dB frequency 7.356683 MHz, identical peak 3.030764 dB at 3.548134 MHz, identical integrated noise 4.805827 uV, Iq 150.05 uA both; DC output 2.500516 V (original, via the transient fallback) vs 2.500493 V (shim): 23 uV apart.
* **Model vs datasheet (SBOS563G), measured here** (shim, open loop, same load): GBW 3.37 MHz vs 3 MHz typ (+12 %); **phase margin 58 deg vs 65 deg typ (7 deg lower)**; DC gain 126.9 dB vs 128 dB typ (5.5 V); Vos ~0.48 mV vs 0.5 typ; Iq 150 uA/amp vs 150 typ. Not compared: noise density, output swing, Zo, PSRR, CMRR. The model is therefore an approximation of the datasheet, with a lower phase margin than the data sheet; it is not validated against hardware.

## 2. REQ-STAB: original, revised, evidence
* **Rev 1 (git b7660b6):** "Both op-amp outputs are stable (no sustained oscillation) with the 100 ohm + 1 nF output network; transient step overshoot at AOUT <= 10 % of step." Replaced before any result because a 3rd-order Butterworth overshoots ~8 % by itself.
* **Rev 2 (IN FORCE; reproduced verbatim in `requirements/fe-aaf-001.yaml`):** "No unintended peaking: at every frequency from 10 Hz to 10 MHz the simulated AOUT gain does not exceed the ideal-op-amp prediction of the same component values by more than 3 dB (catches op-amp/cap-load instability that the ideal model cannot show). Transient step stability is NOT tested."
* **Rev 3 (PROPOSED, NOT APPROVED):** same, but the ideal curve is floored at -90 dB: gain <= max(ideal, -90 dB) + 3 dB.
* **Original failure preserved:** 46.54 dB at 10 MHz (sim -178.9 dB, ideal -225.5 dB) on the pre-patch design: `runs/ref/repair-0001/before/sim_results_rev2.json`. On the final design rev 2 **still fails: 52.9 dB** at 10 MHz (sim -172.6, ideal -225.5); the run record now reports `REQ-STAB` (rev 2) = failed and `REQ-STAB-R3-PROPOSED` = passed (0.09 dB) side by side.
* **Raw evidence it is not peaking (`runs/ref/review/stab/`):** the excess exceeds 3 dB from 266 kHz upward where simulated gain is -127.7 dB falling to -172.6 dB (baseline curve in `S0_baseline_curve.csv`: at 1 MHz sim -145.1 vs ideal -165.5 dB). **Correction of my earlier wording:** I had called this "numerical noise". That is not supported: changing gmin (1e-15, 1e-9) and tolerances (reltol 1e-6) leaves every level identical to 0.1 dB, so it is deterministic model behaviour (finite op-amp gain/output impedance versus a deeper-falling ideal). The floor is justified only by irrelevance (< -90 dB is < 0.03 mV per V against a 0.8 mV LSB). The contract text was corrected accordingly.
* **Can the revised check tell cases apart? Partly, on AC only** (real model, same netlist, `stab_cases.json`):

| Case | rev 2 (unfloored) | rev 3 (floored) | note |
|---|---|---|---|
| S0 baseline (R6 475 ohm, C5 330 pF) | 52.9 dB | **0.09 dB** | stable |
| S3 R6 0.475 ohm, C5 3.3 nF | 37.3 | **0.17** | small load, no excess |
| S4 R6 4.75 ohm, C5 10 nF | 27.7 | **0.33** | |
| S1 R6 0.475 ohm, C5 99 nF | 22.8 | **7.30** (flagged) | gain 22 dB above ideal at 100 kHz |
| S2 R6 0.0475 ohm, C5 330 nF | 27.6 | **27.15** (flagged) | gain dips to -71 dB at 30 kHz then *rises* to -63 dB at 56 kHz (non-monotonic bump) |
Rev 2 ranks the stable baseline *worse* than S1/S2, i.e. it cannot discriminate; rev 3 separates S0/S3/S4 (<0.4 dB) from S1/S2 (7-27 dB). **Limits:** no case showed sustained oscillation (AC analysis cannot show it; no transient oscillation test was run), S1/S2 are "heavy capacitive load with almost no isolation resistor" cases, not demonstrated unstable ones, and only five cases were tried. The 27 dB S2 bump sits at -63 dB, which REQ-ALIAS (<= -55.8 dB) would still pass: STAB flags something ALIAS does not.

## 3. REQ-SETTLE definition, ADC assumptions, corners; Monte Carlo
**Measurement (`ae/sim.py: settling_run`).** Circuit as designed, 1 kHz sine, 1.0 Vpk, Rs = 100 ohm, nominal 5.0 V / 3.3 V, no Vos. Run A (reference): transient to 4.5 ms; t_peak = time of the AOUT maximum in 3-4 ms; v_min = AOUT minimum in the same window (precharge level = the opposite extreme: step ~2.53 V). Run B: identical plus an ngspice switch (`SW`, Ron = 200 ohm, Roff = 1 Gohm, Vt 0.5 V, Vh 0.05 V) from AOUT to Cs = 10 pF, Cs initialised by `.ic` to v_min, closed by a 1 us pulse (1 ns edges) starting at t_peak. **Error = V_Cs(t_peak + 1 us) - V_AOUT_reference(t_peak + 1 us)**, limit 1/2 LSB = 0.403 mV (12 bit, 3.3 V). Sampling at the peak means the signal slope is ~0 during the window.
**ADC assumptions (all assumed, no ADC part):** ideal switch Ron 200 ohm, Cs 10 pF, acquisition 1.0 us, worst-case opposite-extreme precharge, ideal reference, no input leakage, no charge injection/clock feedthrough, no conversion-time kickback.
**Tested:** (a) sweep R6 x C5 (6 x 4 grid, nominal parts, Cs 10 pF): `runs/ref/repair-0001/before/sweep_riso_cf.txt` (note the spiky, non-monotonic landscape; the original 100 ohm/1 nF is in a bad spot); (b) chosen R6 = 475, C5 = 330 pF at R6 +-1 % x C5 +-5 % corners + nominal x Cs 5/10/15 pF (15 runs, Ron 200): worst **0.043 mV**; (c) nominal parts, Ron 50/100/200/400/800 ohm x Cs 5/10/15 pF: 0.000-0.028 mV (this review); (d) step-size check: tmax 1 us -> 0.0016 mV, tmax 100 ns -> 0.0048 mV (5 ns was attempted and blocked by the 60 s limit). Baseline suite value 0.0014 mV.
**Not tested:** other sampling phases/frequencies/amplitudes; supply/VREF corners; Vos corners; temperature; acquisition times other than 1 us; op-amp unit-to-unit spread; the 12 other combinations of simultaneous tolerance + Ron corners; a real ADC. **Caveat from the waveform (`settle_check.py`):** AOUT is kicked by -60 mV, recovers with tau ~ 0.2 us, and overshoots by +0.2 to +0.4 mV at 0.5-0.7 us; the 1 us pass has margin, but an acquisition window of ~0.7 us or less would sit at the 0.403 mV limit.

**Monte Carlo (`runs/ref/mc/mc_results.json`, real model, N = 2000, seed 20261117, AC requirements only).** Draws (uniform, independent): R1-R6 +-1 %, R7/R8 +-0.1 %, C1/C2/C3/C5 +-5 %, **C4 in [0.6, 1.2] x nominal** (X7R tolerance + bias loss; the real part is 10 %, K), Rs in [0.01, 100] ohm. (C8 and supply/VREF/Vos not varied: DC and transient requirements are corner-based, not Monte Carlo.) **Failures: 6 of 2000 (0.3 %), all REQ-FLAT**, by 0.004-0.014 dB (0.504-0.514 dB vs 0.5): samples #163, 337, 564, 876, 1246, 1559. No sample blocked. They share a systematic pattern: C1 x0.95-0.97 with C2 x1.03-1.05 and C3 x1.04-1.05 (C4 scale of the six: 0.69-1.12; correlation of flatness with C4 = 0.00). Flatness over all samples: mean 0.249, p95 0.421, p99 0.472, max 0.514 dB; 40 samples lie in 0.45-0.5 dB. Other metrics: gain 1.257-1.350 (limit 1.235-1.365), stopband worst -62.1 dB, Zin min 64.3 kohm, rev-3 peaking max 1.46 dB. **REQ-STAB rev 2 fails in all 2000 samples (deterministic, section 2);** the 99.7 % overall yield uses the proposed rev-3 form. Worst-case *corner* (all capacitors at the unfavourable extremes) was not simulated; this systematic pattern suggests flatness would fail there.

## 4. BOM verification (`evidence/bom-verification.yaml`; levels defined there)
| Refs | MPN | Result | Compared / source | Open |
|---|---|---|---|---|
| U1 | OPA2314AID | **manufacturer sheet** | TI SBOS563G (2015): pins, SOIC-8 D0008A, 190 uA, 2.5 mV, 7 V abs max | newer datasheet revision not checked |
| C1 / C2 / C3 / C5 | C0805C183J5GACTU / C0805C102J5GACTU / C0805C562J5GACTU / C0805C331J5GACTU | **manufacturer sheet** (Kemet part sheet generated 2026-11-17) | 18 nF / 1 nF / 5.6 nF / 330 pF, 5 %, C0G, 50 V, 0805/2012, -55/+125 C | ESR/parasitics not read |
| C4 | C1206C225K4RACTU | manufacturer sheet | 2.2 uF, **10 %** (contract assumes up to 20 %), X7R, 16 V, 1206/3216 | capacitance at 1.65 V bias: DC-bias plot not read |
| C6, C7 | C1206C106K8RACTU (newly assigned) | manufacturer sheet | 10 uF, 10 %, X7R, **10 V**, 1206 | 10 V part on a 5 V rail: bias loss not quantified |
| C8 | C0603C104K4RACTU (newly assigned) | manufacturer sheet | 100 nF, 10 %, X7R, 16 V, 0603 | |
| R1-R6 | RC0603FR-0764K9L / -0784K5L / -076K49L / -0717K8L (R4,R5) / -07475RL | **family spec + listing** | Yageo RC0603 spec V.4 (2009): code decoding F = 1 %, R = paper reel, 07 = 7 inch, 1/10 W; each exact code seen in distributor listings (search summaries; pages blocked): thick film 0603, 0.1 W, 1 %, 100 ppm/C | no code-specific manufacturer document; spec revision old: it says 50 V working voltage, distributors say 75 V (irrelevant at < 5 V) |
| R7, R8 | RT0603BRD0710KL | family spec + listing | Yageo RT spec V.7 (2017): B = 0.1 %, D = 25 ppm/C, RT0603 1/10 W, 75 V, -55/+155 C; listings agree; BRE variant is 50 ppm (suffix matters) | resistance-range cell for 10 k read from a garbled table row |
| J1, J2 | **UNRESOLVED** | none | needs: 1x3, 2.54 mm, THT, ~0.64 mm square pin (1.0 mm drill), pin 1 first | MPN, pin size, current, plating; manufacturer site blocked |
| TP1-TP4 | bare pads | n/a | | |
Stock, price and lead time are not recorded. `REQ-BOM` stays **failed** (J1/J2 have no MPN); the other lines are graded as above in `release/bom.csv` (`MPN_Status`). Distributor evidence is second-hand (search summaries) and is labelled so.

# OPA2314 ngspice shim: open-loop gain, GBW and common-mode behaviour vs the datasheet

Written 2026-11-20 (research only; nothing in models/, requirements/, evidence/, src/ changed). Legend: **[M]** measured by a throwaway ngspice run (ngspice 44.2 in the pinned image via `ae.toolchain.run_in_image`, `ngbehavior=psa`, shim exactly as in `models/cache/OPA2314/`), **[R]** read from the datasheet text (`evidence/datasheets/OPA2314/ds.txt`, SBOS563G), **[G]** read off a datasheet figure by eye / pixel position (approximate, stated tolerance), **[I]** inferred.
Scripts/raw data (not in repo): `/tmp/claude-1000/-home-adimunot21--claude-spares-spare-fknwJ1/a0e6798f-a9d5-4375-87bf-0dd28263e580/scratchpad/gbw/` (`gen.py`, `an.py`, `an3.py`, `aaf.py`, `w/`).

## 1. What the datasheet says
* [R] GBW 3 MHz typ, **VS = 5 V, RL = 10 kohm, CL = 10 pF** (2.7 MHz at 1.8 V). No min/max given for GBW. Phase margin 65 deg typ, **VS = 5 V, G = 1, RL = 10 kohm** (CL not stated in that row). AOL 128 dB typ (VS 5.5 V, RL 10k, 0.2 V < Vo < V+ - 0.2 V; min 100 dB). Ro 570 ohm at 100 Hz, VS 5.5 V. en 14 nV/rtHz at 1 kHz. CMRR 96 dB typ (75 min) for VCM below V+ - 1.3 V; 80 dB typ (66 min) over VCM = -0.2 .. 5.7 V (VS 5.5 V).
* [R] Input stage (section 7.3.2): N-pair on near the positive rail, P-pair below; both on in a transition region "typically (V+) - 1.4 V to (V+) - 1.2 V", 200 mV wide, may shift by up to 300 mV with process: anywhere from V+ - 1.7 V to V+ - 0.9 V; PSRR, CMRR, offset "may be degraded" there.
* [G] Fig. 1 (pdf p.10, VS = +-2.5 V, RL = 10k/10 pF): gain falls at -20 dB/dec; reading the line at 1 MHz gives about 10 dB (+-1 dB), the 0 dB crossing is at about 2.9-3.2 MHz; phase about -100 deg at 1 MHz, -120 deg at 3 MHz, -141 deg at 5 MHz (green curve, pixel read, +-2 deg).
* [G] Fig. 7 (pdf p.11, VS = +-2.75 V, typical units): offset vs VCM is flat to about +1.5 V, then steps over about +1.55..+1.9 V (i.e. V+ - 1.2 .. V+ - 0.85 V) by roughly +-0.1 to +-0.3 mV per unit, units fan out between about -0.5 and +0.45 mV. Both signs of step occur.
* NOT available: any GBW min/max, any GBW-vs-VCM or vs-RF plot, Aol phase at other CL (the pdf has Fig. 1 only at 10 pF), a numeric table of Fig. 1/Fig. 7. The model-file header just lists "GBW, Aol, Zo..." as "simulated parameters" with no numbers.

## 2. Measurements of the shim [M]
Open loop: loop broken with a 1 GH inductor in feedback / 1 GF cap at -IN (DC closed, AC open), +-2.5 V supply (= VS 5 V), RL 10 kohm to mid, IN+ at mid.

| CL | DC gain | Aol x f (flat part) | unity-gain f | phase margin |
|---|---|---|---|---|
| 0 | 129.0 dB | 3.57 MHz | 3.39 MHz | 64.5 deg |
| 10 pF | 129.0 dB | 3.566 MHz | **3.37 MHz** | 58.1 deg |
| 20 pF | 129.0 dB | | 3.31 MHz | 52.4 deg |
| 50 pF | 129.0 dB | | 3.04 MHz | 39.7 deg |
| 100 pF | 129.0 dB | | 2.59 MHz | 29.0 deg |

* Aol is a clean single pole: 3 dB corner 1.3 Hz, then 20 dB/dec with phase -89 deg from 100 Hz to 30 kHz, -99.6 deg at 1 MHz, -118.6 deg at 3 MHz, -136.6 deg at 5 MHz. Aol x f stays at 3.566 MHz from 10 Hz to 300 kHz and droops only above 1 MHz (3.41 MHz at 3 MHz).
* Open-loop Zo (1 A current injection, -IN ac-grounded): 579 ohm at 100 Hz and 575.6 ohm flat out to 10 MHz (a plain resistance, 5.5 kohm at 1 Hz, 875 ohm at 10 Hz). Datasheet 570 ohm at 100 Hz: matches.
* Aol, fu and PM were identical to 4+ digits at Vcm = 1.5, 3.5, 4.5 V (absolute, 0/5 V supply): the model's AC behaviour does not depend on Vcm.
* Closed loop, non-inverting, RL 10k (-3 dB frequency; "f3 x G0" is the apparent GBW):

| G (noise gain) | CL 0 | CL 10 pF | CL 20 pF | CL 100 pF |
|---|---|---|---|---|
| 1 | 7.45 MHz (+2.3 dB peak) | 7.36 MHz (+3.0 dB peak) | 6.98 MHz (+4.0 dB) | 4.68 MHz (+8.8 dB) |
| 10 | 388 kHz (3.88 MHz) | 394 kHz (3.94 MHz) | 399 kHz | 449 kHz |
| 26 (Rf 25k, Rg 1k) | 138.9 kHz (3.61 MHz) | 139.5 kHz (3.63 MHz) | 140.2 kHz | 145.9 kHz (3.79 MHz) |

* The draft's difference amplifier (RA = RC = 10k, RF = RB = 249k, ratio 24.9, noise gain 25.9, ref 1.65 V, load 10k) [M]: -3 dB at **149.5 kHz with CL = 0, 158.2 kHz with CL = 100 pF**, no peaking above 0.0000 dB (normalised at 100 Hz); identical at Vcm = 1.0, 3.6, 4.2 V. This reproduces the 141-158 kHz.
* Same noise gain, different absolute feedback resistance (non-inverting, CL ~0, RL 10k): Rf/Rg = 2.49k/100: 116.3 kHz; 25k/1k: 138.9 kHz; 249k/10k: 149.5 kHz; 2.49M/100k: 237 kHz (peaking). With RL removed: 122 / 147 / 159 kHz. With the shim's input capacitors (5 pF CM, 1 pF diff, `C_C_CMn/CMp/DIFF`) set to 1e-18 in a scratch copy: 121.9 / 146.3 / 150.0 kHz. So the apparent GBW depends on the resistor values of the circuit under test.

## 3. Why the hand estimate (116 kHz) and the shim (139-158 kHz) differ [M]/[I]
Hand: 3 MHz / 25.9 = 115.8 kHz. The shim's difference, in order of size:
1. **The model's own Aol x f is 3.57 MHz, not 3.0 MHz [M]**: 3.566 / 25.9 = 137.7 kHz (+19 %). This is the main part. fu (3.37 MHz at 10 pF) is 12 % above 3 MHz; the curve is flat at -20 dB/dec so Aol x f is the better figure for closed-loop gain 26.
2. **Resistive loading of the output [M]**: the 10k-load Aol curve already includes the Zo = 575 ohm divider (0.946). A feedback network that loads the output more (2.59k total) loses up to ~18 % of loop gain (116-122 kHz); the 259k network does not (+0.2 %). Hence the circuit-dependent spread: a small-resistor test gives a number closer to 3 MHz/NG by cancellation of two errors, not because the GBW is 3 MHz.
3. **Input capacitance lead with large resistors [M]**: with RF = 249k the model's 5 pF/1 pF input capacitors raise f3 from 150.0 to 159.1 kHz (+6 %) (and to 237 kHz at 2.49M, peaking). The datasheet gives only CIN (1 pF diff, 5 pF CM), consistent with the model's values, so this part is plausibly physical.
4. **CL [M]**: 100 pF (A-LOAD for the second circuit) adds about 5-6 % (149.5 to 158.2 kHz): the output pole of Zo x CL interacts with the loop (compare PM 64.5 to 29 deg at G = 1; at G = 26 the loop crossover is at 140 kHz so no peaking appears).
5. Not an effect: peaking near crossover (none above 0.0000 dB at G = 25.9); non-single-pole Aol (phase -91 deg at 100 kHz, finite-Aol error < 0.1 %); nominal vs typical (the hand estimate uses the typical figure too).
Hand estimate fairness: the datasheet GBW is itself a rounded typical at 10 pF/10k; the hand figure ignores the resistive loading and input-capacitance terms in item 2 and 3, which each move the real circuit by several percent either way.

## 4. Optimistic or matching? [M] vs [G]/[R]
* Phase margin: **matches**. 64.5 deg at G = 1, RL 10k, CL 0 vs 65 deg [R]; at 10 pF 58 deg, Fig. 1 gives about 60 deg at 3 MHz [G]. Phase curve within about 2-5 deg of Fig. 1 up to 5 MHz [G].
* Zo, DC gain (129 dB vs 128 dB typ [R]): match.
* **GBW: the model is optimistic by about 12-19 %**: Aol x f 3.57 MHz and fu 3.37 MHz vs datasheet GBW 3 MHz typ [R]. Fig. 1 read at 1 MHz gives about 10 dB (Aol x f about 3.1 MHz) vs the model's 11.0 dB: model about 1 dB (12 %) above the printed curve [G], with my read uncertainty about +-0.5 dB (+-6 %). Take "model GBW 12-19 % above the datasheet typical, 6-19 % allowing for figure-reading error" as the supported statement. Against a datasheet that gives no GBW minimum, the shim is **not** a worst-case-slow model; it is a slightly fast typical.
* Closed-loop impact: shim f3 for the diff amp is 138.9-158 kHz vs about 116-125 kHz expected from the datasheet (3.0-3.1 MHz) in the same circuit, i.e. the shim's bandwidth is roughly 15-25 % too high for noise gain 26 (the circuit-dependent extras in section 3 are partly physical).

## 5. Implications
**(a) FE-AAF-001 [M, scratch shim copies]** (nominal netlist `runs/ref/baseline/fe_aaf_001.net`, Rs 50 ohm; copies with AOL_2 gain scaled by 0.841 and 0.701 so Aol x f = 3.0 and 2.5 MHz; repo shim untouched):

| metric | shim (3.57 MHz) | 3.0 MHz | 2.5 MHz |
|---|---|---|---|
| passband flatness (REQ-FLAT, 0.5 dB) | 0.098 dB | 0.095 | 0.093 |
| worst stopband gain 19 kHz-1 MHz (REQ-ALIAS, -55.8 dB) | -60.338 dB | -60.346 | -60.357 |
| gain at 1 MHz | -143.2 dB | -141.2 | -139.2 |
| peaking above ideal, floored (rev 3 form) | 0.105 dB | 0.127 | 0.153 |
| peaking above ideal, unfloored (rev 2, in force) | 52.8 dB | 52.3 | 51.8 |

The worst alias point is the 19 kHz edge, set by the filter, so slowing the op-amp by 16 % or 30 % changes it by 0.01-0.02 dB: **the existing alias, flatness and rev-3 peaking numbers are insensitive to the GBW error** (they are controlled by the passive network; the finite-Aol floor sits near -140 dB at 1 MHz and the response beyond 100 kHz is below -100 dB either way). The rev 2 "excess" of 52.8 dB is an artefact of comparing a -143 dB simulated gain with a much lower ideal-model value; it falls only 0.5-1 dB with slower GBW, so the conclusion "rev 2 fails deterministically" is unchanged and not caused by the GBW error. Caveat: only the baseline nominal netlist was checked; a design whose stopband relies on op-amp rolloff (e.g. a low-order active filter) would see the GBW error directly. Note the second stage in this filter runs at noise gain about 2, where a 3 MHz GBW gives f3 about 1.5 MHz, well outside the 19 kHz-1 MHz stopband logic; the figures above include that.

**(b) FE-ISENSE-001 draft [M/H]**
* REQ-BW (>= 20 kHz): the datasheet-based expectation is about 116-125 kHz, the shim gives 139-158 kHz; both are 5.8-7.9x the 20 kHz limit. The verdict does not depend on the discrepancy; DER-BW should be reworded (the 116 kHz hand number is the datasheet-typical; the 141-158 kHz is the shim's 3.57 MHz Aol x f plus circuit effects, so it is a model number about 20 % above the datasheet-typical expectation). Gain margin to the 20 kHz limit with a worst-case GBW 20 % below typical: still above 4x [I].
* REQ-STEP (settle 1 % in 40 us, overshoot 10 %): single-pole tau = 1/(2 pi 120 kHz) = 1.3 us (datasheet-based) vs 1.0 us (shim); settling 4.6 tau is about 6 us either way, 6x margin; overshoot is 0 in both (no peaking at G = 26). Slew 1.5 V/us [R]: a 1.25 V output step is within the slew limit, no issue. CL 100 pF only helps at this gain.
* REQ-NOISE: unaffected by GBW at 20 kHz measurement bandwidth (en 14 nV/rtHz at 1 kHz is a model parameter `NVR = 14`; the 1/f corner uses `NLF = 50` nV at 10 Hz and the model's curve was not compared with Fig. 12 here [U]). Output bandwidth 150 kHz means 20 kHz-150 kHz noise exists above the band but is not counted by the requirement.
* Margin to hand-check: a unity-gain-stable amp driving 100 pF at G = 26 has no stability issue (loop crossover at 140 kHz, phase -91 deg); at G = 1 with 100 pF the shim has 29 deg PM and 8.8 dB peaking: any design variant that uses the second channel as a unity-gain reference buffer with a capacitive load needs the stability/peaking checks (REQ-BW's 1 dB peaking limit applies to Vdiff-AOUT only).

## 6. Common-mode transition region (Q6, second half) [M]
Unity-gain buffer, 0/5 V supply, RL 10k, Vin swept -0.2..5.2 V in 10 mV steps; and the diff amp (RA = RC = 10k, RF = RB = 249k, ref 1.65 V, ideal resistors, Vd = 0 and +-52.5 mV) over Vcm = 0..5 V in 10 mV steps (input node v(+IN) = 0.064 .. 4.87 V).
* Buffer Vos: +0.464 mV at Vcm = 0 V rising **linearly** to +0.528 mV at 5.0 V (15 uV/V, a constant slope: 14.8 uV/V over 0-3 V, 15.0 uV/V over 4-4.8 V, i.e. CMRR about 96.5 dB at every Vcm, equal to the datasheet's below-transition typical 96 dB). Largest change between adjacent 10 mV points: 1.4 uV. **No transition region**: nothing at V+ - 1.3 V = 3.7 V (3.5-3.9 V: +0.508..+0.514 mV, continuous). At the extremes: Vin = -0.2 V gives Vos +50 mV, Vin = 5.2 V gives -18 mV (the model's `VCM_CLAMP` at VEE - 0.2 V / VCC + 0.2 V; outside the useful range).
* Diff amp: gain -24.900 (sign from my wiring) at all Vcm (min/max equal to 5 digits); output error at Vd = 0 is +11.8 mV at Vcm 0 rising smoothly to +13.7 mV at 5 V (includes the 0.48 mV Vos x 25.9 = 12 mV); 3.0 -> 4.2 V shift is **0.46 mV** with ideal resistors (the model's op-amp CMRR term only; the draft's 6.2 mV is resistor mismatch); no step at v(+IN) = 3.0-4.1 V; the largest 10 mV-step change is 3.8 uV.
* Datasheet [R]/[G] expectation: a step of order +-0.1..0.3 mV (typical units, up to about +-0.5 mV between units) in Vos near V+ - 1.2 .. V+ - 0.85 V [G], i.e. a few mV to about 8-13 mV at AOUT for noise gain 25.9, and a worst-case bound from the 66 dB full-range CMRR minimum [R]: 5.9 V x 501 uV/V = 3 mV [I], i.e. up to about 77 mV at AOUT if all of it falls in the Vcm span of interest (not a likely case; typical full-range 80 dB is consistent with Fig. 7).
* The transition does lie inside the draft's span: V+ = 4.75..5.25 V puts V+ - 1.7 .. V+ - 0.9 V at 3.05 .. 4.35 V, and the +IN node for Vcm 3.0-4.2 V is 2.95-4.1 V. **The shim does not reproduce the transition; it is smooth.** It is therefore optimistic for REQ-CMSHIFT (15 mV), REQ-ZERO (120 mV) in the draft: with the model nothing sees the step; a real part may add on the order of 5-10 mV typical to the common-mode shift, i.e. 11-16 mV for the 0.1 % witness (6.2 mV in the model), against the 15 mV limit [I] and has no datasheet-guaranteed bound below 15 mV across the transition. The draft's DER-CM bound used 75 dB, which the datasheet only guarantees below V+ - 1.3 V; across the transition the guaranteed figure is the 66 dB full-range one. Recommend that Section 7's "[U]" be kept and that REQ-CMSHIFT be marked model-based with the datasheet-based note (owner question).
* GBW at the Vcm used: AC response is identical at Vcm 1.0, 3.6, 4.2 V [M]; the real part's GBW may differ slightly between the P-pair and N-pair regions (unspecified in the datasheet, no figure).

## 7. Summary of confidence
Measured (exact): all shim numbers in sections 2 and 6, the scaled-shim AAF runs. Read from text: section 1 limits, transition-region text. Read from figures (approximate): Fig. 1 gain/phase points and Fig. 7 step sizes (eye/pixel reading, +-1 dB and +-0.1 mV). Inferred: the 12-19 % optimism statement (depends on figure read), the worst-case bounds, the FE-ISENSE-001 projections.

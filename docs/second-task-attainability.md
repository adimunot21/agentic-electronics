# Second improvement task: attainability review (outcome: NOT FROZEN)

Written 2026-11-19, before any candidate work. No task or policy file was added; no candidate was evaluated, searched or tuned; no values that might solve a task are recorded here.
Everything below is derived from the baseline record (`runs/agent/improve-live-1/baseline_record.json`) and the design equations. Marked: [equation] = hand calculation from the formulas, [record] = value read from a saved record.

## Why I did not freeze one
The brief says: freeze only if a meaningful improvement is physically attainable with the allowed edits, argued from equations and existing measurements; otherwise stop. I looked at three objectives on ground policy v1 did not cover. None passes that bar under policy v1's rules reused verbatim.

Facts that bind (all [record] unless noted):
* Gain window: corners give 1.2434..1.3637 V/V against 1.235..1.365. Upper headroom is 0.0013 V/V; the policy reserve leaves **0.0008 V/V (0.06 %)**. Lower headroom is 0.0079.
* Flatness: worst-case corner 0.402 dB vs 0.5 (policy cap 0.48: 0.078 dB of room). Nominal flatness 0.098 dB.
* Nominal alias 4.53 dB of margin, alias corners 2.94 dB. Policy v1 rule H6 forbids ANY worsening of nominal alias.
* REQ-STAB (failed): policy v1 allows **zero** increase. In the v1 search its value rose for most edits (e.g. +0.011 dB for a 2.4 % resistor step, +0.85 dB for a larger capacitor step) and fell only for a smaller C3.
* Parts come from E96 (resistors) and E24 (C0G caps) grids: one step is about 2-2.5 % for resistors and about 9-10 % for capacitors. [equation]
* Stage 1 is a multiple-feedback low-pass: f0 = 1/(2*pi*sqrt(R2*R3*C1*C2)) = 1.72 kHz, Q = sqrt(C1/C2)*sqrt(R2*R3)/(R2+R3+R2*R3/R1) = 1.02 (a 3rd-order Butterworth together with the 1.72 kHz stage-2 pole). [equation]

## Objectives considered
1. **Alias corners again, edits on stage 1 (C1, C2).** Stage 1 gives 40 dB/decade, so a 5 % lower f0 would buy about 0.9 dB at 19 kHz. But with Q about 1, stage 1 peaks +0.24 dB at 400 Hz, and moving f0 down 5 % raises 400 Hz gain by about 0.26 % (0.0034 V/V). [equation] That is four times the 0.0008 V/V of headroom, so a capacitor-only edit cannot pass the gain constraint. A fix needs an extra gain trim of a few tenths of a percent, finer than one resistor step. Not shown attainable.
2. **Gain-window centering (minimise the worst-case gain maximum; the lower limit still binds).** Perfect centering would free at most about 0.0035 V/V. The gain shift needed (0.15-0.5 %) is smaller than one E96 step, so it needs coordinated changes of two or more parts. Each of those also moves a pole, and under v1 rules the nominal alias may not worsen and REQ-STAB may not rise. Whether any combination meets all of these is exactly what a candidate search would answer. I cannot argue it from equations.
3. **Flatness corners.** Reducing droop means raising a pole. That worsens nominal alias, which H6 forbids, and the loader has no way to express a replacement rule without code changes.

## What this means
Under v1's rules the feasible region around this design is narrow and mostly set by empirical sensitivities (REQ-STAB especially) that I cannot measure without evaluating candidates, which this task forbids. Freezing an objective here would be freezing an arbitrary unsolved task.

## Decisions that would unblock a fresh task (yours; I changed nothing)
* A different REQ-STAB treatment (the contract owner's call, tied to the undecided rev 3), or accepting that a fresh task may well be unattainable.
* Allowing a wider edit set (stage 1 plus gain trim resistors), which makes the space bigger but harder to justify as "bounded".
* Accepting a small, stated nominal-alias give-back (needs a policy change that the current loader cannot express; a code change).
* Or running a bounded scripted feasibility search first. That puts a known solution in the repo, which is the exposure this task was meant to avoid.

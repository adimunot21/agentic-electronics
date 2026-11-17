# Patch 0002: before / after against the UNCHANGED contract, and adoption record (2026-11-17)

**Status:** patch 0002 is adopted as the local **DEVELOPMENT** baseline only. This is not approval of REQ-STAB rev 3 or of any contract change (`requirements/fe-aaf-001.yaml` hash is identical before and after). The pre-patch baseline, with its failures, is preserved in `runs/ref/archive/baseline-pre-0002/`.

**Conditions (identical for both columns; script `runs/ref/review/before_after.py`, real TI model via the shim, ngspice 44.2):**
* Gain = |V(AOUT) / V(source EMF)| in V/V, source EMF behind Rs, at **400 Hz** (log-frequency interpolation between the 398.1 and 422.5 Hz sweep points; slope there is ~0 so the interpolation error is far below the digits shown). Limit **1.235 .. 1.365** (1.30 +-5 %).
* Alias = **absolute** gain (not relative to the passband), in dB, **at exactly 19 kHz** (dedicated single-frequency AC run) and the maximum over all sweep points to 1 MHz; limit **<= -55.8 dB** (0.5 Vpk interferer -> 1 LSB = 0.8057 mV; exact value -55.85 dB). The response falls monotonically, so 19 kHz is the binding point.
* Flatness = max |gain(f) - gain(400 Hz)| over 50 Hz-1 kHz, limit 0.5 dB.
* Signed margin = distance to the limit; **positive = inside the limit, negative = violation.**
* **Nominal** = nominal parts at Rs = 100 ohm and Rs = 0.01 ohm. **Corner** = all 1024 vertices of the contract tolerance box (R1-R5 +-1 %, C1/C2/C3 +-5 %, C4 x0.6-1.2, Rs 0.01 or 100 ohm), the extremes of which are the stated worst case, not a statistical result.

| Quantity (limit) | pre-0002 | adopted (0002) | margin pre | margin adopted |
|---|---|---|---|---|
| **Nominal, Rs=100** gain @400 Hz (1.235..1.365) | 1.3000638936 | 1.3011328036 | +0.0650638936 / +0.0649361064 | +0.0661328036 / +0.0638671964 |
| Nominal, Rs=0.01 gain @400 Hz | 1.3020557045 | 1.3033272614 | +0.0670557045 / +0.0629442955 | +0.0683272614 / +0.0616727386 |
| Nominal, Rs=100 alias @19 kHz (<= -55.8 dB) | -62.2389425044 dB | -60.3450027994 dB | +6.4389425044 dB | +4.5450027994 dB |
| Nominal, Rs=0.01 alias @19 kHz | -62.2255787501 dB | -60.3303052496 dB | +6.4255787501 dB | +4.5303052496 dB |
| Nominal, Rs=100 flatness (<= 0.5 dB) | 0.2461474053 dB | 0.0974813879 dB | +0.2538525947 dB | +0.4025186121 dB |
| **All 1024 corners** gain min (>= 1.235) | 1.2412084429 | 1.2433829438 | +0.0062084429 | +0.0083829438 |
| All corners gain max (<= 1.365) | 1.3634125750 | 1.3636736114 | +0.0015874250 | +0.0013263886 |
| All corners alias worst @19 kHz (<= -55.8 dB) | -60.6306637961 dB | -58.7352653667 dB | +4.8306637961 dB | +2.9352653667 dB |
| All corners flatness worst (<= 0.5 dB) | **0.5991400350 dB** | 0.4022427580 dB | **-0.0991400350 dB (FAIL)** | +0.0977572420 dB |
| Corners violating flat / gain / alias | 112 / 0 / 0 | 0 / 0 / 0 | | |

Reading it: the patch trades alias margin (corner 4.83 -> 2.94 dB; nominal 6.44 -> 4.55 dB) for flatness (corner -0.099 -> +0.098 dB). The upper gain-corner margin is thin in both (+0.0016, +0.0013 of 1.365): it is inherent to +-1 % resistor ratios against a +-5 % window and was not improved. Worst alias corner (adopted): R1-R4 lo, R5 hi, C1-C3 lo, C4 hi, Rs lo.

**Correction to my earlier numbers.** The suite used to evaluate alias at the first sweep point >= 19 kHz, which is **19.95 kHz** (grid has 18.84 and 19.95 kHz); on a ~60 dB/decade slope that flattered the result by 1.27 dB (pre-0002 nominal: -63.51 reported vs **-62.24** at exactly 19 kHz). The "7.7 dB margin" quoted earlier was therefore 1.3 dB too generous. All alias figures above and in the new records use the exact 19 kHz run (`sim_metrics.py`, test `test_alias_is_evaluated_at_exactly_19khz...`). The archived records keep the old (grid-based) numbers and are not comparable.

## What was refreshed (adopted baseline, `runs/ref/baseline/record.json`: 27 passed, 1 blocked, 2 failed, 3 not_tested)
Schematic regenerated through the ownership guard; KiCad netlist == intent (12 nets/50 pins); ERC 0; deck == netlist; board rebuilt, rerouted, zone-filled: **DRC 0 / unconnected 0 / parity 0**; BOM, Gerbers, drill, CPL regenerated; all simulation requirements, the 1024-corner check, `BOM_CONSISTENCY` and the model-coverage items rerun. Monte Carlo rerun (N = 2000, seed 20261117): **0 failures**, flatness mean 0.118 / p99 0.295 / max 0.334 dB, worst alias -58.95 dB, gain 1.2582-1.3506 (`runs/ref/mc`). REQ-SETTLE robustness rerun: worst **0.045 mV** over R6 +-1 %, C5 +-5 %, Cs 5/10/15 pF (`runs/ref/review/robust_settle_post-0002.txt`). Defect runs repeated on the new design (`runs/ref/defects`).

## What was invalidated or not carried over (machine check: `ae.evidence.stale_reasons` on the two records)
Changed inputs: schematic, netlist, board, components, DUT deck, **and the check code itself** (exact-19 kHz alias). Unchanged: contract, pin evidence, TI model + shim, footprints/placement, project rules, router jar, KiCad libs.
* **Old Monte Carlo yield (99.7 %, 6 flatness failures) is void for this design**: archived in `runs/ref/archive/mc-pre-0002`; replaced by the rerun above.
* **Old resistor-MPN evidence is not carried over**: the five old codes are kept as `superseded_by_patch_0002` in `evidence/bom-verification.yaml`; the four new codes (RC0603FR-0759KL, -0776K8L, -076K19L, -0716K5L) were searched afresh and are graded `listing_verified` (family spec + distributor-listing search summaries; same limits as before incl. the 50 V vs 75 V working-voltage conflict). No code-specific manufacturer document for any resistor.
* `analysis/filter_design.json` is the ORIGINAL derivation (superseded for R1-R5); `analysis/filter_design_0002.json` holds the new values.
* `runs/ref/repair-0001/` (R6/C5 sweep and its corner table) was computed on the old stage values; the settling corners were rerun (above). `runs/ref/review/` STAB case table (S0 = old baseline), corner scripts and before-patch evidence describe the pre-0002 design. `docs/review-extract.md` sections 0-10 describe the pre-0002 design unless stated.
* Defect records in `runs/ref/archive/defects-pre-0002` are for the old design and old check set.

## Still red / open (unchanged by adoption)
REQ-STAB (rev 2, in force) **FAILED**; rev 3 stays PROPOSED, AC-peaking only, passes. REQ-BOM FAILED (J1/J2 no MPN; resistors only family-spec+listing). BOM_CONSISTENCY blocked (J1/J2 unresolved; C6/C7 derating warning). MODEL_STARTUP / MODEL_SATURATION / MODEL_OVERLOAD_RECOVERY not_tested (shimmed model; linear agreement validates none). C4 DC-bias data missing.

# Op-amp selection (FE-AAF-001) - evidence matrix

Requirements that constrain the part (from requirements/fe-aaf-001.yaml): 5 V single supply, RRIO, dual (two filter stages),
total Iq <= 1.0 mA (REQ-IQ), output DC error <= 10 mV (REQ-BIAS), noise <= 0.2 mV rms (REQ-NOISE), stopband <= -55.8 dB (REQ-ALIAS),
and a SPICE model that ngspice 44.2 can run so the simulation claims are not vacuous.

| Candidate | Iq / amp (5 V) typ / max | GBW | Vos max | Datasheet (sha256 prefix) | SPICE model | Verdict |
|---|---|---|---|---|---|---|
| OPA2314AID (TI, SOIC-8) | 150 / 190 uA -> **0.38 mA max total** | 3 MHz, PM 65 deg | 2.5 mV | SBOS563G Jun 2015 (`99a95ba5`) | TI rev C, plaintext, **runs in ngspice 44.2** (psa mode) | **Selected** |
| MCP6002 (Microchip) | 100 / 170 uA -> 0.34 mA max total | 1 MHz, PM 90 deg | 4.5 mV | DS20001733L (`4f879ab3`) | **Blocked**: vendor site 403, model never obtained | Viable on paper; **cannot be simulated here -> not selected** (REQ-ALIAS cannot be evidenced) |
| TLV9062 (TI) | 538 / 750 uA -> **1.5 mA max total** | 10 MHz | (see datasheet) | SBOS839N Jul 2026 (`a77c6dbd`) | not fetched | **Rejected**: violates REQ-IQ on the datasheet alone (typical already 1.08 mA total) |

Datasheet page citations are in `evidence/pins/OPA2314AID.yaml` and `designs/fe-aaf-001/components.yaml`.
Not evaluated and why: other parts (OPA2376, AD8606, ...) were not researched in this slice; the matrix is a *selection under
evidence available now*, not a survey. Stock and price are deliberately not recorded: nothing here asserts availability beyond
"TI lists OPA2314AID as Active in the datasheet's orderable table (SBOS563G)".

## Margins the choice leaves (to be measured, not assumed)
* GBW 3 MHz vs fc 1.6 kHz: stage 1 needs loop gain at 19 kHz; ngspice with the TI model will show the real stopband floor (REQ-ALIAS).
* Vos max 2.5 mV + divider error -> REQ-BIAS margin is ~5 mV; checked as an explicit corner.
* Ib max 10 pA: negligible against the 65-85 kohm filter resistors (< 1 uV).

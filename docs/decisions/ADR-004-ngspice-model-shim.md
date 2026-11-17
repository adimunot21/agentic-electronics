# ADR-004 TI OPA2314 model needs a one-line shim for ngspice 44.2
**Symptom.** DC operating point never converges; ngspice falls back to a short "transient op"; with a 10 uF bias capacitor VMID stayed at 0.3 mV (should be 1.65 V) and every AC number
was computed around a wrong bias point. Options tried and rejected: nodeset/ic hints, gmin/source-stepping options, scaled capacitors (all in session logs; summarised in `models/make_ngspice_shim.py`).
**Decision.** Comment out the single instance `X_SW_OR` (overload-recovery clamp switch); verified Iq 150 uA/amp and Vos ~0.48 mV match the datasheet. Hashes in `models/shim-record.json`.
Any run whose log shows `Transient op` is marked **blocked**, never passed.
**Cost.** Overload recovery is no longer modelled; the design makes no claim about it. A different simulator or a different op-amp model would remove the shim. Model licence is unknown: never committed.

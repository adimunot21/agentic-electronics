# Frozen fixtures
`stab_frozen_failure_adopted_baseline_rs100.csv`: AOUT/source gain (dB, real TI model via the shim, Rs=100 ohm, `ac dec 40 10 10Meg`) and the ideal-op-amp reference (dB, same netlist values)
of the ADOPTED development baseline (patch 0002), frozen 2026-11-18 from `runs/ref/baseline/work/ac_rs100.csv`; netlist sha256 507552e3a4cfa29e3bfdeb6965c73038be75160aee61dd03e8a74863ac65ec73.
It is a frozen FAILING example for REQ-STAB rev 2 (measured excess 52.8 dB > 3.0 dB). Tests use it to verify the metric/version reporting; they must not require future designs to fail.

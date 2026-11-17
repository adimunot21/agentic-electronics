# ADR-006 First demonstrator: 3.3 V SAR-ADC anti-alias front end (50 Hz-1 kHz, fs = 20 kS/s)
**Why this circuit.** Real choices (order, topology, op-amp, E96/E12 values, bias/headroom, ADC load), measurable requirements, model coverage in ngspice, tractable 2-layer routing.
**Derived, not assumed (`analysis/derive_filter.py`).** An alias requirement of -55.8 dB absolute (0.5 Vpk interferer -> 1 LSB) with a 0.25 dB passband droop budget needs order 3:
2nd-order Butterworth gives 38.9 dB vs 53.6 dB needed (a "gain-2 Butterworth" would fail); Bessel fails up to order 5; Butterworth and Chebyshev pass at order 3. Chosen: Butterworth-3 =
inverting MFB (Q = 1, gain 1.3) + inverting 1st-order active stage; values on E96/E12; a symbolic-vs-closed-form check caught a capacitor swap in my own first formula.
**Representative of what?** One analog signal-chain board. It does not demonstrate switching power, RF, high-speed digital, or mixed-signal firmware; "other kinds of electronics" remain future work.

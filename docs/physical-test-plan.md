# Before any fabrication: human review and physical test plan (not executed)

## Human review (required, not done)
1. Re-verify `evidence/pins/OPA2314AID.yaml` against the datasheet (transcribed by the assistant; not yet checked by a person).
2. Select an MPN for the J1/J2 headers; get code-specific manufacturer documents for the six resistor lines; read the Kemet DC-bias plots for C4, C6, C7 (10 V parts at 5 V); check live stock.
3. Review the schematic (label-connected style) and the PCB: ground return paths, stitching vias, input/output connector pinout against the intended ADC board.
4. Fab-house DFM check on the Gerbers; confirm 0.15 mm / 0.3 mm capability and stackup.
5. Review contract revisions (esp. REQ-STAB rev 3) and decide whether to freeze.

## Bench tests (what the simulations cannot establish)
| Test | Instrument | Pass criterion (from contract) |
|---|---|---|
| Frequency response 20 Hz - 1 MHz, Rs = 100 ohm | network/audio analyser | gain 1.30 +-5 % @ 400 Hz; flatness <= 0.5 dB 50-1000 Hz; <= -55.8 dB absolute for >= 19 kHz |
| DC bias vs VREF and supply corners | DMM, bench supply | VMID error <= 10 mV (simulated margin only 0.35 mV: expect this to be the first failure) |
| Output noise 1 Hz-20 kHz | low-noise scope/analyser | <= 0.2 mV rms |
| ADC acquisition with the real ADC | the target ADC board | error <= 1/2 LSB at the real acquisition time; sweep R6/C5 because the simulated landscape is non-monotonic |
| Temperature (0-70 C), X7R bias/age | chamber | drift within budget (unmodelled) |
| Overvoltage on AIN (+-3.3 V analysis case), ESD | per lab practice | no damage; input clamp current < 10 mA |

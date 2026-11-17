# Status and known limitations (honest ledger)

## Implemented and executed (evidence in `runs/ref/baseline/record.json`)
* Pinned toolchain (KiCad 10.0.8 image, Temurin 25 JRE image, Freerouting 2.5.0 jar), `ae tools check`.
* Tiny-board proof netlist -> board -> DSN -> route -> SES -> zone fill -> DRC/parity, incl. parity negative control.
* Acceptance contract; filter derivation (order, topology, E96/E12 values, tolerance Monte Carlo on **ideal** op-amps).
* Editable KiCad schematic (generated, readable, ERC-clean), KiCad-exported netlist == stated intent.
* Datasheet pin audit (symbol embedded in the schematic / footprint / SPICE header / wiring) with negative controls.
* ngspice suite on the real TI model (via a one-line shim): DC corners (32), AC, transients (12), noise, switched-cap acquisition.
* One real failure -> diagnosis -> repair -> rerun -> stale-evidence report (`runs/ref/repair-0001`).
* Board: explicit placement, Freerouting, zone fill, DRC 0/0/0, BOM/Gerbers/drill/CPL, rendered and visually inspected.
* Four controlled defects vs predictions written beforehand (`runs/ref/defects`): 3 matched fully; 1 mismatch (D3: I predicted ERC silent; ERC flagged the edited embedded symbol vs the library copy).
* ngspice Monte Carlo, 2000 samples, real op-amp model (`runs/ref/mc`).

## Implemented but only exercised at unit level
* Patch/base-hash conflict handling (`ae.patch`), ownership guard (`ae.ownership`): unit-tested, used once for the real repair.

## Blocked / failed (stated, not hidden)
* **REQ-BOM: failed.** J1/J2 (headers) have no MPN. 8 lines verified against manufacturer documents for the exact MPN (U1, C1-C8); 6 resistor lines only to family-spec + distributor-listing level; see `evidence/bom-verification.yaml`. No stock/price recorded.
* **REQ-STAB: failed under revision 2 (in force).** Revision 3 (proposed, not approved) passes; see `docs/review-extract.md` section 2.
* **MCP6002** comparison part: SPICE model unobtainable (vendor 403) -> could not be simulated; not selected.
* Native `freerouting-cli` 2.5.0 unusable (diagnosed); jar used. Jar not covered by the publisher checksum file (own hash recorded).
* TI model licence not stated in the file: model never committed; reproduce by download + hash.

## Model/coverage limits (nothing below is claimed by simulation)
* Overload/saturation recovery is not modelled after the shim; ESD cells are modelled by TI but not exercised.
* Temperature (only Vos drift via TEMP), PSRR/CMRR vs frequency at the board level, supply sequencing, hot-plug.
* Tolerance yield: ngspice Monte Carlo with the real model, N=2000, seed 20261117: 99.7 % pass all AC requirements, flatness-limited (worst 0.514 dB vs 0.5). Covers AC requirements only; DC/transient/noise are corner/nominal. Distributions are assumptions (uniform), not lot data. `runs/ref/mc/mc_results.json`.
* REQ-STAB covers AC peaking only; time-domain ringing of the op-amp output is *not tested* as a requirement (it was observed in REQ-SETTLE).
* ADC is a generic switched-capacitor model: acquisition margin is sensitive (spiky landscape, `runs/ref/repair-0001`), so this is the highest-risk item for physical test.

## Layout/manufacturing limits
* DRC clean != electrically good. Ground return paths are via a few vias and THT pads (no stitching analysis); no EMC/ESD/creepage review; antenna/ground-loop effects untested.
* Gerbers exported != a manufactured working board. No fab-house DFM check was run.
* Placement is hand-made (`placement.yaml`); routing is Freerouting default behaviour; no placement/routing optimisation is claimed.

## Contract caveat
Revision 3 of REQ-STAB is PROPOSED after seeing a failing number; rev 2 stays in force and fails until a human approves rev 3 (`made_after_result: true`). The earlier "numerical noise" wording was retracted: the levels are solver-independent model behaviour.

## Not built (future)
Agent framework, specialist roles, evaluation harness execution, multi-part component database, patch locking across agents, cost/latency accounting of model calls. See `docs/agentic-roadmap.md`.

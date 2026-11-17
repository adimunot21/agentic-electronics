# agentic-electronics

Research/engineering project toward **agentic electronics design**: requirement -> component choice -> schematic -> simulation -> PCB placement/routing -> checks -> release outputs,
eventually with coordinated specialist agents. **This repository currently contains the tool-backed substrate and one manually authored reference slice. It contains no agents, and no
model has been called.** It is a test vehicle for the whole chain, not a claim that electronics design is automated.

## What exists and was executed
One analog board, **FE-AAF-001** (3.3 V SAR-ADC anti-alias front end, 50 Hz-1 kHz, 40 x 30 mm, 2 layers), taken through every stage with real tools in digest-pinned containers
(KiCad 10.0.8, ngspice 44.2, Freerouting 2.5.0). The reference design is **manual** (human-directed, assistant-authored), not an agent result.
Evidence: `runs/ref/baseline/record.json` (outcomes, commands, versions, input hashes, timings). Status/limits ledger: [`docs/limitations.md`](docs/limitations.md).

| Stage | Artifact | Where |
|---|---|---|
| Requirements/acceptance contract | 14 requirements, assumptions/derived/verify-method tagged | `requirements/fe-aaf-001.yaml` |
| Filter derivation | order 3 Butterworth (order 2 misses the alias spec by ~15 dB); E96/E12 values | `analysis/derive_filter.py`, `analysis/filter_design.json` |
| Component evidence | OPA2314AID selected over MCP6002 (model unobtainable) and TLV9062 (Iq too high) | `docs/component-selection.md`, `evidence/pins/`, `models/manifest.yaml` |
| Schematic (editable) | generated KiCad 10 schematic, ERC clean (figures: `docs/figures/`) | `runs/ref/baseline/fe_aaf_001.kicad_sch` |
| Simulation | ngspice, TI op-amp model, 32 DC corners + AC + transient + noise + ADC acquisition | `runs/ref/baseline/work/`, `record.json` |
| PCB | placed, autorouted, GND pour, DRC 0 / unconnected 0 / parity 0 | `runs/ref/baseline/fe_aaf_001.kicad_pcb` |
| Release | BOM, Gerbers, drill, CPL (generated, **not** fab-checked) | `runs/ref/baseline/release/` |
| Failure + repair | REQ-SETTLE failed (1.40 mV vs 0.403), diagnosed, repaired by patch 0001, rerun | `runs/ref/repair-0001/` |
| Controlled defects | 4 defects vs predictions committed beforehand | `designs/fe-aaf-001/defects.yaml`, `runs/ref/defects/` |

**Current local development baseline = pre-patch + patch 0002** (resistors R1-R5 rescaled after a worst-case corner failure of REQ-FLAT; see [`docs/before-after-0002.md`](docs/before-after-0002.md); the old baseline with its failures is archived in `runs/ref/archive/`). Baseline: 27 passed, 1 blocked, 2 failed, 3 not_tested. Failing: **REQ-STAB** under the in-force revision 2 (rev 3, AC-peaking only, passes and is NOT approved) and **REQ-BOM** (J1/J2 have no MPN). Model startup/saturation/overload behaviour is explicitly `not_tested`. See [`docs/review-extract.md`](docs/review-extract.md) and limitations.

## Run it
```bash
./scripts/bootstrap.sh        # docker images (pinned digests), Freerouting jar + TI model (sha256-checked), KiCad libs, uv env
make check                    # what is actually installed and capable
make test                     # offline unit tests
make integration              # real kicad-cli / ngspice / freerouting
make run                      # full reference run -> runs/ref/baseline (about 1-2 min)
make defects                  # controlled defects vs predictions
```
Needs docker, curl, uv. No cloud account, API key or paid service. TI's model licence is not stated in the file, so it is downloaded locally and never committed.

## Layout
`src/ae/` toolchain runner, S-expression/KiCad schematic emitter, netlist reader, SPICE deck + cross-check, sim suite, pin audit, board stages, release, evidence/ownership/patch, orchestrator, defects, CLI.
`tools_kicad/` scripts that run **inside** the KiCad image (`import pcbnew`). `designs/`, `requirements/`, `evidence/`, `models/`, `analysis/`, `docs/`, `runs/`.

## Docs
[Architecture](docs/architecture.md) - [Decisions](docs/decisions/) - [Data contract (observed tool formats)](docs/data-contract.md) - [Ownership](docs/ownership.md) -
[Toolchain capabilities](docs/toolchain-capabilities.md) - [Prior art + licences](docs/prior-art.md) - [Limitations](docs/limitations.md) -
[Physical test plan](docs/physical-test-plan.md) - [Agentic roadmap + evaluation protocol (not implemented)](docs/agentic-roadmap.md) - [Walkthrough](docs/walkthrough.md)

## Prior art, honestly
KiCad scripting, SKiDL, atopile, circuitron, etch and mcp-kicad already solve parts of this (see `docs/prior-art.md`). What this repository adds so far is narrow:
an evidence-bound run record, a datasheet-independent pin audit, a diagnosed ngspice/model shim, a bounded failure->repair loop, and mechanically checked defect predictions for one board.
No novelty beyond that is claimed, and no multi-agent benefit has been measured.

Licence: undecided (proposal: Apache-2.0, owner to confirm). KiCad libraries are CC-BY-SA-4.0 with design exception; Freerouting is GPL-3.0 and runs as a separate process.

# Architecture (what exists today)

```mermaid
flowchart LR
  REQ[requirements/fe-aaf-001.yaml<br/>contract, owned by evaluator] --> DER[analysis/derive_filter.py<br/>order, topology, E-series values]
  DER --> MAN[designs/fe-aaf-001/components.yaml<br/>+ evidence/pins + models/manifest]
  MAN --> GEN[ae.designs.fe_aaf_001<br/>schematic generator]
  GEN --> SCH[(editable .kicad_sch<br/>owns connectivity + values)]
  SCH -->|kicad-cli ERC| ERC[ERC]
  SCH -->|kicad-cli netlist| NET[(KiCad netlist = truth)]
  NET --> CMP[intent / datasheet pin audit / deck cross-check]
  NET --> DECK[ae.spice -> ngspice deck]
  DECK --> SIM[ngspice 44.2 suite<br/>DC corners, AC, tran, noise, acquisition]
  NET --> PCB[pcbnew build + placement.yaml]
  PCB -->|DSN| FR[Freerouting jar / Java 25]
  FR -->|SES| PCB2[(routed board + GND pour)]
  PCB2 --> DRC[kicad-cli DRC + schematic parity]
  PCB2 --> REL[BOM, Gerbers, drill, CPL]
  ERC & CMP & SIM & DRC & REL --> REC[(record.json: outcomes, versions, input hashes, timings)]
```
Contracts between stages are files (KiCad formats, YAML, JSON) plus the observed formats in `docs/data-contract.md`. Each external call goes through `ae.toolchain.run_in_image` / `run_router`
(named container, no network, kill on timeout, wall time + peak memory recorded). Failure modes: a stage returns `error`/`blocked` and dependants are marked `blocked`, not skipped silently.
**Not drawn because not built:** agents, orchestrator, patch review queue, evaluation harness (`docs/agentic-roadmap.md`).

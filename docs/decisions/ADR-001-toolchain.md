# ADR-001 Toolchain: KiCad 10 CLI + pcbnew API + ngspice + Freerouting, in digest-pinned containers
**Status:** accepted 2026-11-17. **Evidence:** `docs/toolchain-capabilities.md`, `docs/prior-art.md`.
**Context.** Nothing was installed (Ubuntu apt offers KiCad 7.0.11 / ngspice 42). Reference projects: mcp-kicad (PolyForm-NC: not a commercial foundation;
it now also has PCB code, so "schematic-only" was outdated), etch (no licence), circuitron (MIT, SKiDL route), atopile (MIT, own language, layout sync).
**Decision.** Use KiCad's own file formats and tools headlessly: `kicad-cli` (ERC, netlist, DRC, exports), `pcbnew` Python for board creation and
Specctra DSN/SES (absent from kicad-cli), ngspice 44.2 shipped in the same image, Freerouting as a separate GPL process. Images pinned by digest; no network at run time.
**Why not adopt atopile/SKiDL now.** They add a second source of truth (their language) between requirement and KiCad; this project's evidence chain
is simpler when KiCad's exported netlist is the single connectivity truth. Revisit atopile once the chain runs. **Cost:** we wrote an S-expression reader/emitter (`ae/sexpr.py`, `ae/kicad_sch.py`).
**Consequence.** The KiCad image shipping ngspice was *verified*, not assumed. Project code licence is undecided (proposal Apache-2.0, needs the owner's decision); scripts importing `pcbnew` run only in the container.

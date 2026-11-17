# Data contract: what the external tools really emit (observed 2026-11-17, KiCad 10.0.8, ngspice 44.2, Freerouting 2.5.0)

Everything here was read from real output (see `runs/probes/` and `runs/ref/baseline/`). Re-verify after any tool upgrade.

## KiCad netlist (`kicad-cli sch export netlist --format kicadsexpr`)
* `(comp (ref) (value) (footprint) (fields (field (name X) "v")...) (tstamps "<symbol uuid>") (sheetpath (tstamps "/")))`
* Net names: **local labels get a leading `/`** (`/N_INV`); power nets are plain (`GND`, `+5V`). Unconnected pins: `unconnected-(REF-PinX)`.
* `(pinfunction)` only appears when the library pin name is not `~`. Footprint link on the board = `/` + symbol uuid (`ae/netlist.py`).
* Multi-unit symbols: the component carries the first unit's uuid.

## ERC JSON (`sch erc --format json --severity-all`)
`sheets[].violations[] {type, severity, description, items[{description,pos,uuid}]}`. Exit code 0 even with violations (no `--exit-code-violations`).

## DRC JSON (`pcb drc --schematic-parity --format json --severity-all --exit-code-violations`)
Three sections: `violations`, `unconnected_items`, `schematic_parity`. **Parity entries have severity `warning`**, so a pass test must count *all three
sections* (a test that only counts `severity==error` in `violations` misses parity drift). Exit code 5 if any entry exists. Design rules come from
`<board>.kicad_pro`, **not** the board file; a `.kicad_pro` is auto-created with defaults (min track 0.2 mm) if absent.
Parity needs `<stem>.kicad_sch` next to `<stem>.kicad_pcb`.

## pcbnew (Python, in the pinned image only)
`ExportSpecctraDSN(board, path)`, `ImportSpecctraSES(board, path)` exist in 10.0.8 (kicad-cli has no DSN export). Footprint custom fields:
`fp.SetField(name, value)`; `fp.GetField(name).SetVisible(False)`. `AddField` does not exist.

## Freerouting 2.5.0
* **Use the jar on Java 25**; the GraalVM `freerouting-cli` native build hangs (evidence `docs/evidence/freerouting-native-cli-2.5.0-failure.log`).
* Headless: `java -jar freerouting-2.5.0.jar --gui.enabled=false -de X.dsn -do X.ses -mp N`. Tries to POST analytics to `api.freerouting.app`: run with `--network none`.
  With no network the container hostname must resolve (`--hostname aebox --add-host aebox:127.0.0.1`) or log4j errors.
* The final line `final score: S (U unrouted and V violations)` is parsed for the record; exit code is 0 even if boards are not fully routed: **always run DRC**.

## ngspice 44.2 with the TI OPA2314 model
* Needs `set ngbehavior=psa` read *before* the netlist (`.spiceinit` in the work dir). Inline comments need `;` in this mode (a trailing `* text` is an error).
* Unmodified model: **no DC operating point is found**; ngspice silently falls back to a short "transient op" (log line `Transient op started`) that does not settle slow RC nodes
  (VMID 0.3 mV instead of 1.65 V). The shim removes one instance (`models/make_ngspice_shim.py`); runs that still show `Transient op` are marked **blocked**.
* `wrdata`: per vector `[x re (im)]` repeated (`ae/sim_metrics.read_wrdata`). `noise`: `print onoise_total` (plot noise2). `.meas` is limited to tran/dc/sp/ac.

## SPICE model (TI OPA2314 rev C): see `models/manifest.yaml` for provenance, hashes, licence status (unknown) and limitations.

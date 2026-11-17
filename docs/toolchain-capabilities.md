# Toolchain capabilities (measured 2026-11-17)

Everything below was observed by running the tool, not read from documentation.
Re-check with `uv run python -m ae.toolchain` and `uv run python -m ae.probes.tiny_chain`.

## Images (both pinned by digest)
| Role | Image | Contents observed |
|---|---|---|
| EDA | `kicad/kicad@sha256:3f4a0178…ddc34` (tag 10.0.8) | Debian 13, kicad-cli 10.0.8, `pcbnew` Python 10.0.8 (Python 3.13.5), **ngspice 44.2 (Debian pkg 44.2+ds-1, preinstalled — not assumed, verified)**, KiCad symbol/footprint libs. **No Java, no `/usr/bin/time`, no curl/pip.** Runs as uid 1000. |
| Router JRE | `eclipse-temurin@sha256:945247f9…e6` (25-jre) | OpenJDK 25.0.5+8 |

KiCad library tree hash (symbols+footprints, copied to `vendor/kicad-libs-10.0.8/`, not committed): `1dd4a74c87dfda4d9f1f66c0536e3587406ca8f5af607d55ae1a4813d6c45222`.

## Capability matrix
| Capability | Result | How verified |
|---|---|---|
| Headless ERC (JSON) | works | `kicad-cli sch erc --format json` |
| Netlist export for connectivity | works (`kicadsexpr`) | read real file |
| Schematic → editable board via script | works (pcbnew SWIG: footprints from real libs, nets, outline, zone) | `tools_kicad/pcb_ops.py build` |
| **Specctra DSN export / SES import** | **works via pcbnew SWIG** (`ExportSpecctraDSN`, `ImportSpecctraSES`); **not available in kicad-cli** | tiny-board chain |
| Autorouting | works with **Freerouting 2.5.0 jar on Java 25** | tiny-board chain, 0.67 s routing job |
| Autorouting with `freerouting-cli` native 2.5.0 | **BROKEN — rejected** | see below |
| Zone fill + DRC with schematic parity (JSON) | works | tiny-board chain; negative control confirms parity is live |
| Headless SPICE | ngspice 44.2 present | version banner (measurement tests: see sim step) |
| Network use | tools run with `--network none`; Freerouting attempts analytics to api.freerouting.app (`UnknownHostException`, harmless) | logs |
| Claude execution without API key | `claude` CLI 2.1.325 logged in via claude.ai auth; headless `-p`, `--json-schema`, `--max-budget-usd`, `--allowedTools` exist. **Not exercised** (no model call made). Whether `--max-budget-usd` caps subscription usage is unverified. | `claude auth status`, `claude -p --help` |

## Diagnosed failure: native `freerouting-cli` 2.5.0
Symptom: process never exits (0 CPU time for 9+ minutes). Cause: the GraalVM native image's routing thread
throws `UnsupportedFeatureError: SerializationConstructorAccessor class not found for declaringClass:
app.freerouting.board.model.items.ComponentOutline` inside `BoardSnapshotManager.serialize`; the main thread
keeps waiting. Evidence: `docs/evidence/freerouting-native-cli-2.5.0-failure.log` vs the working
`docs/evidence/freerouting-jar-2.5.0-success.log`. Decision: use the jar. Note the jar is not covered by the
publisher's checksum file; our recorded SHA-256 is `f6f51bb0…de3c7` (a local record, not publisher verification).

## Tiny-board chain (probe circuit, not the demonstrator) — `runs/probes/tiny/stages.json`
| Stage | Wall s | Peak MB |
|---|---|---|
| ERC | 4.38 | 1239 |
| netlist export | 1.29 | 226 |
| build board (pcbnew) | 1.53 | 279 |
| DRC unrouted (4 unconnected found) | 4.18 | 1595 |
| DSN export | 0.92 | 158 |
| Freerouting (jar, container incl. JVM start) | 3.69 | 257 |
| SES import + zone fill | 1.08 | 264 |
| DRC routed (0 violations / 0 unconnected / 0 parity) | 4.49 | 1619 |
Peak memory for the router row comes from the container cgroup; others from `getrusage`.

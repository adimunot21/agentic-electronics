# Ownership of design data

| Data | Owner | Rule |
|---|---|---|
| Requirements, acceptance checks, expected results | **evaluator** (`requirements/`, `designs/*/defects.yaml`) | A design change must never edit these. Revisions are logged in `revisions:` with `made_after_result` and an independence caveat (see REQ-STAB rev 3). |
| Part identity (refdes, MPN, evidence refs, model refs), board constraints, placement | **manifest** (`designs/fe-aaf-001/components.yaml`, `placement.yaml`) | Changes go through reviewable patches (`designs/*/patches/`, `ae.patch`) with a base hash; a patch against a changed base is refused (`PatchConflict`). |
| **Connectivity and component values** | **the editable KiCad schematic** | A generator writes the *initial* schematic from the manifest/seed. After that, KiCad's own netlist export is the truth: SPICE decks, the board, parity and BOM are all derived from `kicad-cli sch export netlist`. |
| Routing, copper | the KiCad board (+ Freerouting session) | Derived from the netlist; never hand-edited by a generator. |

## Manual edits
`ae.ownership.guarded_write` refuses to overwrite a generated file whose hash differs from the one the generator last wrote
(`ManualEditConflict`). The author must merge the manual edit back into the manifest/seed or explicitly discard it. A hand edit made
in KiCad is picked up on the next run because everything downstream is re-derived from the exported netlist, and every stored result
carries the input hashes it was produced from (`record.json: inputs_sha256`), so evidence for the old file is visibly stale
(`ae.evidence.stale_reasons`; demonstrated in `runs/ref/repair-0001`).

## What is NOT enforced yet
Concurrent patches from several agents (a lock/merge protocol) are not built; only the base-hash conflict check is. See `docs/agentic-roadmap.md`.

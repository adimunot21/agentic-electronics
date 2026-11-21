# DRAFT - NOT APPROVED
# Q2: what does the agent submit? Three options (advice, not a decision)

Written 2026-11-20. Markers: **[V]** verified in source read this session, **[H]** estimate/hand reasoning, **[A]** assumption. No witness values appear here: all numbers in the examples are placeholders (`<RA>`, `<RB>` ...). Seam gaps (a)-(i) are the lettered list in `docs/worker-notes/h-lead.md` Batch 4 item 3 (a task kind, b artefact, c fixed check names, d corners_summary/H1-H6, e prompt, f brief baseline, g tolerance class and series, h inputs_sha256 key, i feedback filter) [V].

## 0. Facts every option builds on
* `ae.patch.apply_patch` is a regex rewrite of `components.yaml` value/MPN fields, base-hash guarded; it cannot add parts or nets [V `src/ae/patch.py`]. The harness `set_field` ops edit fields of existing refs under a `ScopePolicy` part regex [V per H-lead note].
* `kicad_sch.SchematicBuilder` draws one labelled wire stub per pin (so any placement is electrically explicit) from `Part(ref, lib_id, value, footprint, at, rotation, unit, props)` plus pin->net lists; real library symbols are embedded from `vendor/kicad-libs-10.0.8` [V `src/ae/kicad_sch.py` header and `designs/fe_aaf_001.py`]. Placement/`INTENDED_NETS` there are hand-written for one circuit [V].
* `bomcheck` reads `MPN`, `Tolerance`, `Dielectric`, `Voltage`, value and footprint from the exported netlist `fields` and decodes Kemet MLCC + Yageo RC/RT [V].
* KiCad's export is the truth for connectivity and values (`docs/ownership.md` [V]); every option must end in `kicad-cli sch export netlist`.
* Fixed by the task, not by the agent: connectors J1/J2, test points, U1 = OPA2314AID with symbol and pin evidence [A, per contract draft].

## Option A: whole design-spec file
**Schema sketch** (`design.yaml`, placeholders only):
```yaml
schema: isense-design/1
parts:
  U1:  {symbol: "Amplifier_Operational:LM358", mpn: "OPA2314AID", footprint: "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", unit_pins: {A: [1,2,3], B: [7,6,5], P: [8,4]}}
  R1:  {symbol: "Device:R", value: "<RA>", tolerance: "<pct>", mpn: "<yageo-mpn>", footprint: "Resistor_SMD:R_0603_1608Metric"}
  R2:  {symbol: "Device:R", value: "<RB>", tolerance: "<pct>", mpn: "<mpn>", footprint: "Resistor_SMD:R_0603_1608Metric"}
  C1:  {symbol: "Device:C", value: "<C>", tolerance: "5%", dielectric: C0G, voltage: "50V", mpn: "<kemet-mpn>", footprint: "Capacitor_SMD:C_0805_2012Metric"}
nets:                                   # pin lists; connector/power nets pre-exist in the template
  SENSE_P: [J1.1, R1.1]
  N_P:     [R1.2, R2.1, U1.3]
  AOUT:    [U1.1, J2.1, "..."]
```
**Validator rules:** schema/unknown keys; refdes pattern and uniqueness (`^[RCU][0-9]+$`, J/TP reserved); symbol in an allow-list (Device:R, Device:C, the one op-amp symbol); footprint in allow-list per class; value parses (`spice.parse_si`) and is in the declared series (`harness/eseries.py` E96 for R, E24 for C0G; **0.1 % class is E96/E192 by MPN: seam gap g**); `tolerance` in an allowed set and not tighter than the MPN decode (`bomcheck.decode`); every net pin exists on that part; no pin on two nets; every pin of U1 listed (explicit `nc` allowed only with a reason); reserved nets cannot be removed; part count <= N (anti-bloat); no part classes outside R, C, U.
**Evaluator ingestion:** validator -> generator (new, about 150 lines: `Part` list + auto grid placement + `SchematicBuilder`) -> `.kicad_sch` -> `kicad_erc` -> `kicad_netlist` -> bomcheck/pin_audit/deck/sim. The spec is NOT the truth after generation; a post-generation check compares KiCad's netlist with the spec's nets (`compare_netlist_to_intent` [V], consistency only).
**Cheat/leak surfaces:** the agent controls everything including free text fields (`role`, notes): strip or ignore them; hidden extra parts (limit count, class); `tolerance` lie (caught by bomcheck); value strings like `1e3` vs `1k` (canonicalise numerically); symbol property injection (`Sim.*` properties could change a KiCad simulation but we do not use KiCad's simulator [V ngspice deck is built from netlist]; still reject unknown properties). Error feedback must say which rule failed, not how to fix the circuit (gap i).
**Canonicalisation / duplicates:** sort parts by ref, nets by name, pins within nets; numeric values to a canonical SI float; drop free text; hash. Refdes renaming creates distinct hashes for electrically identical designs: add an optional **isomorphism hash** (graph canonical form over (class, value, tolerance, pin-role)) for duplicate detection; cost small for about 12 parts [H].
**Agent ergonomics [H]:** about 1.0-1.6k tokens for a 12-part design; whole-file rewrites are easy for an LLM but every resubmission restates everything (token cost scales with iterations, small here); typical errors: pin typos, a pin on two nets, forgetting the second op-amp channel. Strong for first-draft authoring.
**Effort [H]:** schema + validator 1-1.5 d, generator with auto-placement 1.5-2 d (the main cost), canonicalisation/isomorphism 0.5 d, tests 1 d. Total about 4-5 d on top of the evaluator work already estimated.
**Seam gaps triggered:** all of a, c, d, e, f, i (common); **b fully** (new artefact + patch protocol + state canonicalisation); **h** (artefact key `design` in `inputs_sha256`); **g** (tolerance as an editable field plus series rule for 0.1 % parts).

## Option B: agent edits the KiCad schematic directly
**Schema sketch:** the artefact is `design.kicad_sch` (S-expression), starting from a template containing connectors, test points, U1 and the power/ref nets.
```
(symbol (lib_id "Device:R") (at <x> <y> 90) (uuid ...)
  (property "Reference" "R1") (property "Value" "<RA>") (property "Footprint" "Resistor_SMD:R_0603_1608Metric")
  (property "MPN" "<mpn>") (property "Tolerance" "<pct>") ...)
(wire (pts (xy ..) (xy ..)))  (label "N_P" (at ..))
```
**Validator rules:** parse with `ae.sexpr` [V]; allow-list of lib_ids, properties and footprints; no unknown top-level forms; template items (U1, J1, J2, TPs) byte-identical (hash by uuid); embedded `lib_symbols` must equal the library's own (otherwise the agent could ship a modified symbol pin map: D3-style defect [V `designs/*/defects.yaml`]); series/tolerance rules as in A, but on property values; ERC clean; no off-grid or overlapping geometry rules needed (KiCad decides).
**Evaluator ingestion:** simplest: schematic goes straight to `kicad_erc` and `kicad_netlist`; no generator. `pin_audit` already audits the EMBEDDED symbol [V]. This is the closest to the ownership rule ("after generation KiCad is the truth").
**Cheat/leak surfaces:** the largest. S-expression is a big attack surface: modified embedded symbols, extra sheets/hierarchical labels, `no_connect` markers hiding pins, text items with instructions, power flags/labels creating unintended nets by name collision (a global label "AOUT" merges nets), hidden fields. Needs a strict whitelist parser and a "re-emit canonical schematic from the parsed intent" step, which is effectively Option A internally.
**Canonicalisation / duplicates:** raw files differ by coordinates and uuids; canonicalise by ignoring geometry and uuids and hashing from the exported netlist (components + nets) [V export exists]; refdes isomorphism as in A.
**Agent ergonomics [H]:** poor for an LLM: coordinates, uuids and label/wire geometry; 5-10x the tokens of A for the same circuit (a 20-part schematic is tens of kB; the existing generated schematic for 25 parts is large [A]); frequent error modes are dangling wires, off-grid pins and label typos; an agent with a KiCad-GUI tool would be a different task. Good only if agents are given a schematic-editing tool API.
**Effort [H]:** parser whitelist/validator 2-3 d, template + hash-guard 1 d, no generator (saves 1.5-2 d) but requires a tool layer (get/put schematic text or helper ops) 1-2 d, adversarial tests 1.5 d. About 5-7 d, with the highest security risk.
**Seam gaps triggered:** common set (a, c, d, e, f, i); **b** (new artefact and workspace file type, canonical state from netlist); **h** (key = schematic sha); g reduced (tolerance is a property checked by bomcheck) but series rule must read properties.

## Option C: patch against a seed template (extend today's set_field ops)
**Schema sketch** (patch ops, placeholders):
```yaml
base: {seed: designs/isense/seed.yaml, sha256: <hash>}
ops:
  - {op: add_part, ref: R1, symbol: "Device:R", footprint: "Resistor_SMD:R_0603_1608Metric"}
  - {op: set_field, ref: R1, field: value, to: "<RA>"}
  - {op: set_field, ref: R1, field: tolerance, to: "<pct>"}
  - {op: set_field, ref: R1, field: mpn_candidate, to: "<mpn>"}
  - {op: connect, ref: R1, pin: "1", net: SENSE_P}
  - {op: connect, ref: R1, pin: "2", net: N_P}
  - {op: disconnect, ref: R1, pin: "2"}
  - {op: remove_part, ref: R1}
```
**Validator rules:** per-op: `add_part` only allowed classes and a free refdes within a pool; `set_field` fields restricted (value, tolerance, mpn_candidate, dielectric, voltage); `connect` only to existing or new net names matching `^N_[A-Z0-9_]+$` or reserved nets; a pin may be connected once; after applying ops the resulting design is validated by A's whole-design rules (so C = A + a patch layer); op count <= 40; base-hash conflict as `ae.patch` [V pattern].
**Evaluator ingestion:** apply ops to the seed -> a design spec identical to A -> same generator path. So C reuses everything A builds, plus the op applier.
**Cheat/leak surfaces:** the seed is visible (it contains the fixed part U1, connectors, reserved nets) which is acceptable if the seed contains no solution; the same hazards as A after expansion; op sequences can be redundant (add then remove) so validation must be on the resulting state, not the op list; `connect` onto reserved nets is the injection point (rule: only listed reserved nets, and U1 pins are fixed by pin role).
**Canonicalisation / duplicates:** canonical form = resulting state (same as A), hash of state is the duplicate key; the improvement-mode machinery `designstate.canonical_state`/cycle protection already works on states [V per `docs/agent-improvement-mode.md` section 10], so C slots into it better than A or B (it keeps the "edit a state" mental model and the base-hash conflict check).
**Agent ergonomics [H]:** incremental edits are token-cheap on later iterations (tens of tokens per op) but the first build needs 40-60 ops for 12 parts (about the same tokens as A); order dependence and "pin already connected" errors are common LLM slips; easier for local repairs (change one value) than A; harder to see the whole circuit (the harness should expose `get_design` as a rendered netlist [A]).
**Effort [H]:** extend `patch.py`/harness ops (add/remove/connect/disconnect, validators, state canonicalisation) 2-3 d on top of A's schema/validator/generator (4-5 d) = 6-8 d total; lower risk than B.
**Seam gaps triggered:** common (a, c, d, e, f, i); **b** (extended ops, new canonical state, new `get_design`); **h** (artefact key); **g** (tolerance as an editable field + 0.1 % series); the improvement-mode refusal "no_change_from_baseline" must become "no change from the previous submission" [A].

## Comparison
| Criterion | A design spec | B edited schematic | C seed + patch ops |
|---|---|---|---|
| Fits "requirements to schematic" | yes | yes | yes (from a seed) |
| Schema/validator clarity | high | low (raw S-expression) | high (A rules + op rules) |
| Evaluator ingestion | generator (new, auto-placement) | direct (no generator) | generator + op applier |
| Reuse of existing code | SchematicBuilder, bomcheck, netlist, pin_audit | netlist, ERC, pin_audit; bomcheck | all of A + patch/state protocol |
| Cheat/leak surface | medium (free text, tolerance lie) | high (symbol edits, labels, hidden items) | medium (A's, plus op-order games) |
| State canonicalisation / duplicates | easy (sorted spec; isomorphism hash optional) | needs netlist-based hash | easy and matches improvement-mode state machinery |
| LLM token cost / error modes | moderate; whole-file; pin typos | high; geometry and uuid errors | low per edit, high first build; ordering errors |
| Effort (days, on top of evaluator) [H] | 4-5 | 5-7 | 6-8 |
| Seam gaps | a b c d e f g h i | a b c d e f h i (g reduced) | a b c d e f g h i (b largest harness change) |
| Main risk | auto-placement quality and spec/KiCad drift | adversarial schematics | complexity of the op language |

## Recommendation (advice for the owner, not a decision)
Start with **A** for the first ISENSE evaluator and task: it has the smallest security surface for its clarity, makes the validator rules explicit, and gives the generator that C and any later GUI tool would need anyway. Design the validator so that its input is a canonical *state* (parts + nets), then add **C** later as a thin op layer if multi-iteration repair work (the improvement-mode style loop) shows whole-file rewrites to be wasteful. Avoid **B** unless agents get a schematic-editing tool API; its hazards (embedded-symbol edits, label collisions) mostly force a re-emission step that turns it back into A. Whatever is chosen, keep evaluation behavioural (not a netlist match) and keep the feedback filter (gap i) decided before any live run.

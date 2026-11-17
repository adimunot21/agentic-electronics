# ADR-002 Design representation: KiCad files + a thin versioned manifest, not a new IR
**Status:** accepted. **Decision.** Part identity/evidence/constraints live in YAML (`components.yaml`, `placement.yaml`, `requirements/*.yaml`, `evidence/pins/*.yaml`, `schema_version` fields);
**connectivity and values live in the editable KiCad schematic** (see `docs/ownership.md`). SPICE decks, boards, BOM are *derived from KiCad's exported netlist* and each run
records sha256 of every input (schematic, netlist, board, deck, models, symbol libs, check code, tool images/versions) in `record.json`.
**Why.** An invented language would be a third place to disagree with the tools. Consistency is *checked* (intent vs netlist, deck vs netlist, DRC parity), and the independent
reference for physical meaning is the transcribed datasheet (`ae.pin_audit`), because netlist/parity agreement cannot detect a consistently wrong symbol (defect D3).
**Limit.** The YAML files are not yet validated by a published JSON Schema; that is future work.

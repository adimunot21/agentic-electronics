# S-lead review note (sim/research area)  -  signed: S-lead, 2026-11-20

## Who did what
- S-engineer (subagent): src/ae/harness/sanity.py, tests/unit/test_sanity.py (backlog 3). Design/contract by S-lead.
- R-engineer (subagent): docs/research/orderability.md, src/ae/parts/{__init__,orderability}.py, src/ae/parts/data/orderability.yaml, tests/unit/test_orderability.py (backlog 4).
- S-lead: grounding in improve-live-8, contracts, review, follow-up edits (functools cache, docstring labels, one extra test).
- H-lead cross-reviewed sanity.py (read-only); items 1,2,3 applied, 4/5 noted below.

## Grounding (improve-live-8)
Eval 1 design: R4 16.2k / R5 16.5k (ratio 1.0185 compensating C1 18n->20n and R2/R3 changes). Agent reverted R4 only -> ratio 1.000, corner gain min 1.2196 < 1.2355 (H2), rejected, no preview budget left.
Ideal op-amp cascade predicts corner gain within 0.0005 V/V, flatness within 0.016 dB, alias 0.05 dB (conservative) on baseline/eval1/eval2.

## What review caught
- My brief assumed STAB ratio mismatch fires on eval2; false: eval2 has R5/R4 = 1.000. The real signal is GAIN_CORNER_MIN (warn) and, with previous=eval1, PARTIAL_REVERT_VS_PREVIOUS. Test re-targeted accordingly (partial-pair wording is tested on eval1).
- H-lead: docstring policy labels wrong (fixed); baseline re-predicted each call (lru_cache added, warm call ~35 ms); MPN-only edit (already quiet since comparison is on parsed values; test added).
- Open: GAIN_NOMINAL has no borderline band; limits are hardcoded (no limits_from_policy); nominal predictions not validated against real nominal data; flatness margin only ~0.0045 dB against 0.02 error band.
- R-engineer: capacitor side not grounded (Kemet cert expired / distributor fetches denied), correctly left out. Resistor range statement rests on interleaved PDF text; "listing_evidenced" for RC parts relies on earlier search summaries. Never returns in_stock.

## Caveats for lead
- Any new src/ae/**/*.py changes the trusted digest (sanity.py, src/ae/parts/*.py): needs supersession note. YAML data is outside the glob.
- Advisory only: nothing changes acceptance. Orderability cache is not wired anywhere.

## Tests
uv run pytest tests/unit -q: 345 passed (after follow-ups). Not run: integration/docker.

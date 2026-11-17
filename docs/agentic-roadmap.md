# Agentic layer: design intent and evaluation protocol (NOT IMPLEMENTED; no model call has been made)

The long-term goal stands: specialists (component evidence, circuit alternatives, simulation, layout, independent review) working through typed tool contracts on this chain.
The slice built so far is the *tool-backed substrate* those agents would use. What exists that agents can call today: `run_reference`, the stage functions in `ae.stages`/`ae.pcb`/`ae.sim`,
`ae.patch.apply_patch` (base-hash conflicts), `ae.evidence.stale_reasons`, `ae.pin_audit`. What does not exist: any agent, prompt, retry policy or role.

## Supported Claude execution (checked, not exercised)
`claude` CLI 2.1.325 is logged in with claude.ai auth; headless `claude -p` offers `--json-schema`, `--allowedTools`, `--max-budget-usd`. Whether `--max-budget-usd`
caps subscription usage and what a run costs are **unverified**. No paid or live run is authorised or started. Decision pending: API key vs headless CLI for a first budgeted run.

## Evaluation modes (to be implemented; what changes between them)
| Mode | Model | Tools | Harness | Specialists |
|---|---|---|---|---|
| A baseline single agent | same model/version | same EDA tool functions | minimal loop: prompt + tool results, no ledger | no |
| B engineered single agent | same | same | + evidence ledger, staleness invalidation, typed patches, bounded retry with progress/stop rule, independent evaluator owns requirements | no |
| C coordinated specialists | same | same | B + role separation (evidence, circuit, simulation, layout, reviewer) and patch ownership/conflict protocol | yes |
| (separate) no-tool model | same | none | reported separately; cannot isolate coordination benefit | n/a |
Same task set, same tool capabilities, recorded budgets (tokens, tool seconds, wall time). Fixed development cases vs **exposed** regression/generalisation variants
(fc 2 kHz / gain 5; 3.3 V supply; high-pass; 4th order) - labelled exposed, not hidden holdouts. Repeated independent attempts; failures stay in the denominator.
Metrics: independently checked requirement satisfaction, complete usable outputs, simulation margins, unresolved ERC/DRC/parity items, human interventions, latency, actual model/tool cost.
Guards: the design agent cannot edit `requirements/` or expected results; unsupported model behaviour stays `not_tested`.

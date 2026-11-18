# Agent orchestration harness (first closed loop) - 2026-11-18

**What it is.** `src/ae/harness/`: a controller that lets a model act on the FE-AAF-001 design only through typed tools, and an independent evaluator that accepts or rejects the result from protected requirements and fresh full-pipeline evidence. The ADC front end is the first test vehicle; nothing here is specific to filters except the task definitions and the two fast checks.

## Verified commands (all run in this environment)
```bash
./scripts/bootstrap.sh                                   # setup (pinned images, jar, TI model, KiCad libs); `uv run ae tools check` verifies
uv run pytest tests/unit -q                              # deterministic tests, fixtures only: 94 passed
uv run pytest tests/integration/test_harness_real.py tests/integration/test_measurements.py -q -m integration   # REAL tools: 3 passed (~13 s)
uv run python -m ae.harness.run --probe                  # is live model access available? (one tiny call) -> ok: true, claude-sonnet-5-5
uv run python -m ae.harness.run --task dev-c1-value --provider claude-cli --run-id <id>             # LIVE model-driven run (~4 min incl. 2 full-pipeline runs)
uv run python -m ae.harness.run --task dev-c1-value --provider scripted-fixture --evaluate-defect  # FIXTURE run of the machinery with real tools (~5 min)
uv run python scripts/harness_wrong_fix_fixture.py       # FIXTURE: tempting wrong repair, real evaluator
```
Live model access = the logged-in `claude` CLI in headless mode with all built-in tools disabled (`--tools ""`), no session persistence, no settings/MCP, empty temp cwd, per-call `--max-budget-usd` cap. No API key, no new account, no cloud job. It is the project's only runtime model interface; nothing about my interactive edits is used by the harness.

## The controlled-defect task (`dev-c1-value`, split: dev)
* **Defect (test setup, `ae.harness.tasks.create_defect`; exact record `runs/agent/*/defect.json`):** manifest `C1.value` 18n -> **1n** (its verified MPN C0805C183J5GACTU still decodes to 18 nF). Adopted manifest sha256 `e6970b1f...`, defective `adc412c3...`.
* **Real detection before repair:** `bom_consistency` **failed** ("C1: VALUE: schematic 1n but MPN C0805C183J5GACTU decodes to 1.8e-08"); `ac_response` failed (gain 1.2375, flatness 1.94 dB vs 0.5, alias -40.4 dB vs -55.8). The full pipeline rejects the defect with 16 violations (`runs/agent/dev-c1-fixture-real/defect_evaluation.json`): flatness, alias and all three corner checks regress, plus raw-measurement drift.
* **What the agent receives:** the protected requirements (read-only tool), the measured failure report above, and tools over design evidence. It is NOT told the answer or which field to edit; the failure report names C1 because the real check does.

## Result: live agent run (`runs/agent/dev-c1-live-1/`) - n = 1, an easy task
Trace (`trace.jsonl`, 25 events): `get_design(C1)` -> `get_design_evidence(C1)` (ledger: MPN verified vs the Kemet sheet at 18 nF; design-calculation record 1.8e-08) -> `propose_patch` {C1.value: 1n -> 18n} (applied, new hash `e6970b1f...`) -> `run_check(bom_consistency)` (0 mismatches; J1/J2 unresolved as before) -> `run_check(ac_response)` (all passed) -> `submit`. Evaluator: fresh full pipeline (91.6 s), **accepted, 0 violations**, 6 unresolved baseline items still listed, `release_ready: false`.
Accounting: 6 model calls (30.3 s), 5 tool calls (3.7 s), evaluation 91.6 s, wall 224.6 s incl. baseline capture; 12 input / 1068 output tokens as reported (cache tokens are in the per-call trace, not summed); **cost reported by the CLI $0.082 as a list-basis estimate; actual billing unknown.** One run says the loop works end to end once; it says nothing about reliability or about harder tasks.
Output (isolated, reference untouched): `output/components.yaml`, `output/agent_patch_vs_defect.diff`, `output/candidate_vs_adopted_reference.diff` (**empty: exact restoration**), `output/patches.json`, `output/evaluator_decision.json`.
Observations: in one reply the model emitted stray `<invoke>` text before its JSON action; the parser extracted the JSON (recorded in the trace). The evidence tools made the answer fairly direct.

## Failure/repair on real evidence (fixture, `runs/agent/dev-c1-wrongfix-fixture/`)
A scripted agent first edits C1's MPN to the 1 nF code (so MPN and value agree). The agent-facing check then reports `bom_consistency` back at the baseline state (0 mismatches). The independent evaluator **rejects** it on fresh evidence: REQ-FLAT 0.098 -> 1.94 dB, REQ-ALIAS -60.3 -> -40.4 dB, flatness/gain/alias corner checks fail, and raw-evidence drift on unresolved REQ-STAB (52.8446 -> 52.8442 dB, flagged because a candidate that moves an unresolved check's measurement cannot be assessed safely). The second patch (restore MPN and value) is accepted. This is a fixture (scripted answers), not agent success; it demonstrates the evaluator's independence.

## Protected boundary (enforced, with tests)
* No file access for the model; its only design effect is `propose_patch`. Patch language has no paths: only `set_field` on `value`/`mpn_candidate` of discrete R/C parts (scope policy), <= 4 ops, format-checked values; unknown keys/ops, out-of-scope parts/fields (U1, `REQ-*`, `mpn_status`, thresholds), no-op and shell-looking values are rejected before application; stale `base_sha256` is rejected, not merged; an MPN edit forces `mpn_status` to `unverified_candidate` (evidence is not agent-writable).
* Evaluator: trusted code/config digest (42 files: requirements, `src/ae/**`, `tools_kicad`, evidence, placement, rules, model shim) taken at start and checked before and after every evaluation; a change => `blocked`. Candidate manifest is read as data only.
* Evidence cache keys include the design hash and trusted digest: any edit or code change makes old evidence unreachable (tested).
* Missing tool, timeout, unreadable result, parser failure => explicit `ok:false`/`blocked`/`error`, never success.
## Acceptance policy (fixed before any repair attempt; `evaluator.py`)
Accept only if the fresh record contains every baseline check, none is worse than the trusted pre-defect baseline, **none is better without review** (e.g. REQ-STAB failed -> passed is `needs_review`, rejected), and every numeric measurement matches baseline within rel 1e-6 / abs 1e-9; trusted digest unchanged. No blanket exemptions. Unresolved baseline failures (REQ-STAB, REQ-BOM, BOM_CONSISTENCY, MODEL_STARTUP/SATURATION/OVERLOAD_RECOVERY) are listed in every decision and `release_ready` is always false. The strictness suits an exact-restoration task; a design task with legitimately different designs would need a margin-based policy chosen in advance (the `allow_improvements` knob exists; nothing relaxes automatically).
## Stops and state
States INIT -> BASELINE_CAPTURED -> DEFECT_VERIFIED -> AGENT_TURN <-> PATCH_APPLIED -> EVALUATING -> ACCEPTED | REJECTED_CONTINUE | STOPPED(reason). Limits: iterations, model calls, tool calls, wall time, reported cost (enforced only when the provider reports it), identical patches, no-progress (no violation-count improvement or an identical design resubmitted), invalid replies. `state.json` + `trace.jsonl` allow `Controller.resume` (tested: stop at max_iterations, resume with a higher limit, accept). Credentials are redacted from the trace (tested). Unknown cost stays `None`.

## Status table
| Item | State |
|---|---|
| Typed tools, constrained patches, workspace, evaluator + policy, controller, providers | **implemented and executed** (unit + real runs) |
| Live model-driven repair of the dev task | **executed once, accepted** (n = 1) |
| Real-tool defect detection before / clean after repair | **executed** (integration test + runs) |
| Wrong-fix rejection with the real evaluator | **executed (fixture)** |
| Held-out tasks `heldout-r2-value`, `heldout-c3-value` | defined, **unexecuted**, refused without `--allow-heldout` (tested) |
| Agent tools for component choice, simulation setup, PCB placement/routing | **future**: only `bom_consistency` and nominal `ac_response` exist; the full pipeline is evaluator-only |
| Mode `basic_single_agent_tools`, `coordinated_specialists` | **not implemented** (controller raises `NotImplementedError`; registry says so) |
| Cross-mode comparison, repeated attempts, cost comparison | **not run**; needs separate approval; no specialist advantage is claimed |
| Release-readiness | **not claimed**: unresolved baseline failures remain |
Attempts and defects found while building: the tamper-block test exposed a crash recording a blocked decision (missing directory) and a duplicate-`reason` argument bug in the stop path (both fixed, tests kept); one test assumed the wrong defect state (my error). The live run needed no retries.

## Previews (existing files; the design artifacts did not change in this milestone)
`docs/figures/schematic_full.png`, `docs/figures/pcb_front_back.png`, `docs/figures/response.png`, `docs/figures/stability_evidence.png` (also `schematic_left_stage1.png`). Source of truth: `runs/ref/baseline/`.

## Path to the research comparisons (shared by construction)
`Task` (defect + split) -> `ToolRegistry` (same tools for any mode) -> `TrustedEvaluator`/`decide` (same protected judge) -> `Usage` accounting (model/tool/eval seconds, tokens, reported cost) in `summary.json`. A basic mode = same tools without patch scope/evidence cache/stop rules; specialists = role-split tool subsets plus a patch-ownership/conflict layer on top of `Workspace.propose`'s base-hash check. Next meaningful tasks: a connectivity defect and a component-substitution task, then held-out variants, with equal budgets and repeated independent attempts, after you approve the spend.

## Walkthrough and a prediction exercise
Controller: `controller.py` (read `Controller._loop` then `_submit`). Boundary: `patch.py::validate_patch`. Policy: `evaluator.py::decide`. One engineering choice: the evaluator re-runs the whole trusted pipeline and compares raw numbers to a pre-defect baseline instead of trusting the agent-facing checks, because a scoped check can be satisfied by the wrong edit (MPN) - see the wrong-fix run.
**Predict before looking** (answers in `runs/agent/dev-c1-wrongfix-fixture/evaluations/decision_01.json`): if the agent edits C1's *MPN* to the 1 nF code instead of the value, (a) does `bom_consistency` still fail, (b) which of REQ-GAIN, REQ-FLAT, REQ-ALIAS, REQ-STAB would the evaluator flag, and (c) would REQ-STAB's status change? Then predict what the validator says to a patch `{part: "REQ-GAIN", field: "max", to: "2"}` (see `tests/unit/test_harness.py::test_out_of_scope_and_malformed_patches...`).

# Improvement mode (separate from restoration) - 2026-11-19

Restoration (`dev-c1-value`, `evaluator.decide`) demands numeric equality with the baseline and is untouched (its 94 unit tests and integration tests still pass). Improvement mode is a
**separate task kind** with its own frozen, typed policy (`src/ae/harness/improvement.py`, `src/ae/harness/tasks/improve_stage2_alias.yaml`) because equality cannot judge a changed design.
Everything below was produced by **scripted fixture candidates against the REAL pipeline. No model proposed any of it; nothing here shows an agent designing an improvement.**

## 1. Objective (chosen from real coverage) and why
Coverage on the adopted reference: worst-case (1024 tolerance corners) flatness 0.402 dB vs 0.5, worst-case gain 1.2434..1.3637 vs 1.235..1.365 (only 0.0013 V/V headroom above), worst-case alias rejection -58.74 dB vs -55.8.
So the gain window is nearly saturated, flatness margin is thin, alias margin is the roomiest: a real **passband flatness vs alias rejection tradeoff** on the stage-2 pole `1/(2*pi*R5*C3)`.
**Objective:** minimise `REQ-ALIAS-CORNERS` (dB), improvement >= **0.25 dB**. Editable: R4, R5, C3 `value` and `mpn_candidate` only (max 6 ops). Not chosen: supply/ripple (no source in the contract), value/MPN restoration.

## 2. Frozen policy (committed before any candidate: `815427d`, syntax-only fix `e01c130`; file sha256 `d278109a50e8a9f161a41f07bbdd9d5157f0d757ab5f37aea62e7599f88aabdc`)
* **Baseline hashes:** components `e6970b1f206274d7898b3290f7d70f1806281e4c34a04904e345cd56518d6c6d`, requirements `4767130a0f72f059b0cc002354c8527ba7aedc4fc1ef512380e78d2751e64b25`. `load_policy` refuses (verdict `blocked`) if either differs, or if a contract limit/reserve in the policy disagrees with the protected requirements.
* **Jitter:** rel 1e-6 / abs 1e-9 (two independent full evaluations of one design agreed with max |diff| = 0). Threshold 0.25 dB is ~10^5 above that. *Limit found later:* a textually different but numerically identical design (`5.60n`) moved `REQ-SETTLE` by 0.001 mV (its 0.40 mV margin is untouched) while every other value matched exactly, so SETTLE is only repeatable for byte-identical input; it is governed by the 10 %-of-margin rule, which absorbs that.
* **Hard constraints (reserves only tighten the contract):** worst-case flatness <= 0.48 dB (H1); gain min over corners >= 1.2355 (H2) and max <= 1.3645 (H3), read from `corners_summary.json`; nominal flatness <= 0.48 (H4); nominal gain in [1.2355, 1.3645] (H5); nominal alias not worse than baseline + jitter (H6); every baseline-passed check stays passed (H7).
* **Non-regression of everything else:** other numeric metrics may consume at most 10 % of their baseline margin (engineering convention, stated). Traded metrics (flatness, gain) are governed only by H1-H5.
* **Failed/blocked/not-tested baseline items:** `REQ-STAB` (failed, rev 2) value must not rise above baseline + jitter - "still failed" is **not** treated as "not degraded"; failed->passed is `needs_review` (rejected as a claim). `REQ-BOM` must stay failed and `release/bom.csv` rows may change only on edited refs and only to `unverified_candidate`. `BOM_CONSISTENCY` stays blocked with 0 mismatches. `MODEL_*` stay `not_tested`. Structural checks must keep their status.
* **Verdicts:** `accepted_for_objective` (scope: this objective only), `rejected`, `blocked` (digest/policy/baseline drift, missing/error/incomplete-corner evidence, stale evidence). Decisions list `policy_violations` separately from `circuit_requirement_failures` (flagged `pre_existing`), always `release_ready: false`, and `blocked_claims` (new MPNs unverified; MODEL_* unvalidated for the new time constant; REQ-STAB untouched).

## 3. Code changes (restoration paths unchanged)
`improvement.py` (policy dataclass, loader/verifier, pure `decide_improvement`) · `evaluator.py` (`_guarded_run` shared digest guard, `evaluate_improvement`; `evaluate` behaviour unchanged) · `controller.py` (task kind, improvement prompt/brief/finalize/resume, **strict `parse_action`**) · `workspace.py` (task-scoped `ScopePolicy`; trusted digest now also covers `tasks/*.yaml` and `evidence/bom-verification.yaml`) · `tasks.py`, `run.py` · `scripts/improvement_search_fixture.py` · tests `tests/unit/test_improvement.py` (40), `tests/integration/test_improvement_real.py` (2).

## 4. Candidates, real full-pipeline measurements, decisions
Trusted digest `5b9f609ef134f5e9a34936861a9245c4b12cff5398d6e1f9b729f2793604142a` (unchanged since). Baseline captured once (99.6 s). Per candidate ~99-104 s wall (eval ~94-97 s). Total candidate wall 802 s. All values from the evaluator's fresh record; each decision file is `runs/agent/improve-stage2-alias-fixture/<label>/evaluations/decision_01.json`, raw evidence in `<label>/evidence/`.

| label | verdict | alias corners dB | flat corners dB | alias nom dB | flat nom dB | gain nom | STAB rev2 dB | gain corners | policy violations |
|---|---|---|---|---|---|---|---|---|---|
| BASELINE | - | -58.7353 | 0.4022 | -60.3303 | 0.0980 | 1.3011 | 52.8446 | 1.2434..1.3637 | - |
| A R4=R5 16.9k | rejected | -58.9414 | 0.4483 | -60.5366 | 0.1405 | 1.2995 | 52.8557 | 1.2417..1.3621 | objective below 0.25 (+0.206); STAB +0.011 |
| B R4=R5 17.4k | rejected | -59.1924 | **0.5063** | -60.7877 | 0.1942 | 1.2996 | 52.8690 | 1.2396..1.3601 | H1, H7 (REQ-FLAT-CORNERS now FAILED, 12 corners), STAB +0.024 |
| C C3 6.2n, R4=R5 15.4k | rejected | -59.0160 | 0.4656 | -60.6111 | 0.1565 | 1.3011 | 53.6957 | 1.2411..1.3615 | **only** STAB +0.851 (objective +0.281 met, all hard constraints held) |
| D C3 6.2n | rejected | -59.6101 | **0.6080** | -61.2057 | 0.2885 | 1.2959 | 53.7292 | 1.2357..1.3564 | H1, H7 (128 corners fail), STAB +0.885 |
| X regression C3 5.1n | rejected | -57.9326 | 0.3284 | -59.5271 | 0.0913 | 1.3069 | 52.0317 | 1.2493..1.3692 | objective regression -0.80, H3 (gain max), H6, H7 |
| X no-op C3 `5.60n` | rejected | -58.7353 | 0.4022 | -60.3303 | 0.0980 | 1.3011 | 52.8446 | 1.2434..1.3637 | improvement 0.0 (`no_change_within_jitter`) |
| X out-of-scope R1 | validator `out_of_scope`; then the script (my fixture design) submitted the unchanged design, evaluated as a no-op: rejected | = baseline | | | | | | | improvement 0.0 |
| **E** C3 5.1n, R4=R5 18.7k | **accepted_for_objective** | **-59.0087** | 0.4634 | -60.6041 | 0.1545 | 1.2990 | 52.0872 | 1.2411..1.3616 | none |

E: improvement **+0.2735 dB** (worst-case alias margin 2.94 -> 3.21 dB), manifest sha256 `eb824be5cd3625b1573bf6ebbd8183099b9fe1a2167715585c082365c3260109`, patch `E_.../output/patches.json` (6 ops), diff vs adopted reference `output/candidate_vs_adopted_reference.diff`; adopted `designs/fe-aaf-001/components.yaml` not modified. `REQ-STAB` is 52.09 dB: lower than baseline but **still failed**; no resolution is claimed.

**Honest caveats.** (1) The frozen order A-D found nothing: no pre-registered candidate was accepted. (2) E was added *after* seeing A-D (feedback-driven: raising C3 raises the STAB value, raising R4=R5 raises it a little; so lower C3 and raise R). It is in a separate file (`scripts/improvement_extension_1.yaml`), uses the identical, unchanged policy and the frozen cap (5th and last full evaluation). It is a search over 5 candidates, not a pre-registered single shot. (3) The gain is modest and costs 62 % of the worst-case flatness margin (0.098 -> 0.037 dB left). (4) First-order pole estimates over-predicted flatness margin (B was predicted fine; it failed REQ-FLAT-CORNERS) - the 1024-corner evidence is what protected the decision. (5) New MPNs `C0805C512J5GACTU`, `RC0603FR-0718K7L` decode consistently but are `unverified_candidate`; nothing checked existence/orderability.

## 5. One tradeoff worth understanding
Zero-tolerance non-regression on `REQ-STAB` is the **binding** rule: C met the objective and every hard constraint but was rejected, because the rev-2 STAB value (a model comparison dominated by levels far below -90 dB, per the contract log) rises when C3 rises. I did not relax it after seeing C: relaxing it is exactly the unapproved rev-3 argument, and choosing a tolerance post hoc would make acceptance depend on the result. The cost: the policy forced a more roundabout candidate (E) and would reject an engineering-sound change for a metric a human may later rule irrelevant. The decision belongs to the contract owner (approve rev 3 or a STAB tolerance), not this task.

## 6. Action boundary (what is actually enforced)
`parse_action` accepts exactly one JSON object with an `action` key (prose around it is tolerated, as seen in the live run); rejects two or more (also inside an array), a missing/unknown key, non-string tool, non-object args, `NaN`/`Infinity`/`1e999`. Rejection dispatches nothing: tested that tool calls, evaluations, patch files and the manifest hash are unchanged (`test_rejected_actions_dispatch_nothing_and_change_nothing`). Unknown tool -> `unknown_tool`; unknown op / non-string or non-finite `to` -> `malformed_patch`; out-of-scope part/field -> `out_of_scope` (all no-side-effect, tested). **CLI:** `ClaudeCliProvider.command` is asserted to carry `--tools ""`, `--setting-sources ""`, `--strict-mcp-config`, `--no-session-persistence`, `--max-budget-usd`, and no `--mcp-config`/`--allowedTools`/permission-bypass flag; `claude --help` (installed 2.1.325, no inference) documents those flags. **Not verified:** the CLI's runtime tool list (needs an inference call) and managed settings, which the help says still apply. This is flag-level confinement of a text-in/text-out subprocess in an empty cwd, not a hardened sandbox; the controller process itself has normal file permissions.

## 7. Verified commands
```bash
uv run pytest tests/unit -q                                             # 134 passed
uv run pytest tests/integration/test_improvement_real.py tests/integration/test_harness_real.py tests/integration/test_measurements.py -q -m integration   # 5 passed (real EDA, ~25 s)
uv run python scripts/improvement_search_fixture.py                    # frozen search A-D + controls (baseline + 7 full evaluations, ~15 min)
uv run python scripts/improvement_search_fixture.py --extension scripts/improvement_extension_1.yaml --labels E_c3_5n1_r45_18k7 --prior-full-evals 4 --reuse-baseline   # ~2 min
uv run python -m ae.harness.run --list-tasks                           # shows improve-stage2-alias (kind improvement)
```
## 8. Later live trial - **NOT RUN, needs budget approval** (no model call was made for any of this)
```bash
uv run python -m ae.harness.run --task improve-stage2-alias --provider claude-cli --run-id improve-live-1 --max-iterations 3 --max-model-calls 30 --max-budget-usd 0.5
```
Verified without inference: the task loads and its policy verifies against the frozen hashes; `--provider scripted-fixture` is refused for this task. Expect ~100 s baseline + ~100 s per submission. Fast checks show only nominal behaviour, so the agent cannot see corner results before submitting; a live run needs repeats and a held-out improvement task before any conclusion.

## 9. Status
| | |
|---|---|
| Implemented **and executed** | policy, evaluator, controller path, strict action parsing, 8 real full-pipeline evaluations + baseline, 134 unit + 5 integration tests |
| Executed as fixtures only | every candidate (A-D, E, controls); the accept path on real evidence is E alone |
| Implemented, unexercised | improvement resume (code path written; no test), improvement mode with `claude-cli` |
| Blocked / limits | live trial (budget); STAB nonregression vs rev-3 decision; new-MPN verification; MODEL_* behaviour; corner results unavailable to the agent before submit |
| Future | held-out improvement task; coordinated-specialist and basic-mode comparison on this task; component-selection/PCB-layout objectives |

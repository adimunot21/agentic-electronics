# Erratum 1: task `improve-wide-retune-alias` (policy v2)

Written 2026-11-19. The frozen policy file is NOT edited. File: `src/ae/harness/tasks/improve_wide_retune_alias.yaml`, sha256 `7a02c5479daeb2a36cb8496c3b5a2cff8dfa7e648e708ebfa272a62386d9d660` (frozen in `d6313db`, unchanged since).

## 1. Out-of-scope control: what actually happened
The frozen file lists `X2_out_of_scope_r7` with `expected_verdict: no_evaluation`. That label is wrong for how the control script behaves.
* The patch validator refused the R7 edit with `out_of_scope`, as intended (`runs/agent/improve-wide-retune-controls/X2_out_of_scope_r7/trace.jsonl`: `propose_patch` -> `ok: false, out_of_scope`).
* The fixture script does not stop after a refused patch. It went on to `run_check` and `submit` with the unchanged design. That design was evaluated in full and rejected as a no-op (`objective_improvement`, improvement 0).
* Corrected expectation for this control as scripted: validator refusal `out_of_scope` followed by an unchanged-design submission with verdict `rejected`. The script's own `as_expected: false` for this row comes from the wrong frozen label, not from a plumbing failure.
* Same behaviour as the v1 control `X_out_of_scope_r1`.

## 2. Cosmetic wording
The evaluator's blocked-claim text for MODEL_* checks says "modified stage-2 time constant". This task also allows stage-1 edits. The text is hard-coded in `improvement.py`; it is wording only and was not changed.

## 3. Trusted-code digest: accepted
Adding the policy to `src/ae/harness/tasks/` changes the combined trusted digest, because that directory is inside the trusted set on purpose (an agent or an edit must not be able to change a policy).
* Previous digest `9e86499f119dae3a35e29187cfba922e06e9081bc8c7585f4683c6e8fec57370` (53 files).
* New trusted baseline `04acfb2534ec304b83a239bdab64b8e1559c1dae04bc2e3d3e4c7cafd0c68b7d` (54 files). Accepted by the project owner.
* Verified by command: the 53 existing files hash, without the new policy, to exactly `9e86499f…`. Only the new policy was added.

# Harness seam for a second circuit (H-lead design note, 2026-11-20)

Goal: a second circuit/task is added BY DATA (a circuit profile + task/policy yaml + requirements file + a checks module), with FE-AAF-001 behaviour byte-identical. Frozen policies v1-v4, `requirements/`, and the existing 347 unit + replay tests are not edited.

## 1. Audit: what is hard-wired to FE-AAF-001 today
| Where | Literal | Seam |
|---|---|---|
| improvement.load_policy | `requirements/fe-aaf-001.yaml` (hash check + contract-limit check); `check.replace("-CORNERS","")` | `profile.requirements_path`, `profile.corner_suffix` |
| improvement.decide_improvement | corner ids `REQ-{ALIAS,FLAT,GAIN}-CORNERS` (blocked detection); H6 nominal `REQ-ALIAS`; special rule for `REQ-STAB`; `_blocked_claims` text (MODEL_*, REQ-STAB) | `profile.corner_check_ids`, `profile.nominal_objective_check`, `profile.stability_check_id` (None disables), `profile.standing_claims` |
| controller | `designs/fe-aaf-001/components.yaml` pristine path; brief `keys` list; diff labels/README text; `corners_summary.json` | `profile.baseline_design`, `profile.brief_keys`, `profile.labels` |
| controller default / run.py | `checks.run_fast` (netlist, ngspice, `fe_aaf_001.net`, contract limits all inside checks.py) | `profile.checks` = dotted `module:function` with the existing `(names, manifest, workdir) -> {name: result}` contract |
| evaluator.run_pipeline | `ae.reference_run.run_reference` | `profile.pipeline` dotted path, same `(components_path, out_dir) -> record` contract |
| workspace.TRUSTED_GLOBS | FE requirement, placement, kicad_pro, model files | base list unchanged; `profile.extra_trusted_globs` appended ONLY for that circuit, so the FE digest is unchanged |
| tools | `requirements/fe-aaf-001.yaml`; `evidence/bom-verification.yaml`; `analysis/filter_design_0002.json`; identity ref map | `profile.requirements_path`, `profile.evidence_ledger`, `profile.derivation_record` |
| checks.assess_guards / guard_context | traded ids `REQ-ALIAS/GAIN/FLAT`, `REQ-STAB` rule, H1-H3 meanings | take ids from `profile` |
| sanity (S lane) | filter-specific ideal model | `profile.edit_sanity: bool` (False for circuits without a model; hook then returns None) |
| designstate, patch, workspace scope | generic already (parts/fields from the policy's `allowed_edits`) | none |

## 2. Contract
`ae.harness.circuit.CircuitProfile` (frozen dataclass) with the fields above. `FE_AAF_001` is defined IN CODE with exactly today's literals and is the default, so a policy file without a `circuit:` key (all of v1-v4) resolves to it and every code path computes what it computed before. A policy may name `circuit: <id>`; profiles for other circuits load from `src/ae/harness/circuits/<id>.yaml` (data, validated: unknown keys and missing files are errors, dotted paths must import).
`ImprovementPolicy.profile` (new trailing field with default) carries it; `Controller` takes it from the policy; `TrustedEvaluator(profile=...)` extends the digest globs; `load_policy` verifies the profile's requirements hash exactly like today.
Not generalised (honest limits): the three-metric corner summary shape (`corners_summary.json` gain_min/gain_max) and H1-H6 hard-constraint semantics stay as they are; a second circuit whose objective is not a corner-swept metric needs a new hard-constraint id handler, which is data only for the ids already defined.

## 3. Requirements on the second circuit (for S-lead)
Provide: requirements yaml with `check` min/max per id; baseline design manifest of the same `parts:` form; a `checks` function and `pipeline` function with the contracts above (record = `{checks:[{id,outcome,value,limit,margin}], summary, inputs_sha256:{components}}`); `corners_summary.json` if corner hard constraints are used; list of paths to trust. Ref names are free; the policy's `allowed_edits.parts` regex is the scope.

## 4. Test plan
FE regression: all existing tests unchanged; new test pins `FE_AAF_001` literals and `trusted_digest` equality with and without a profile object. Second-circuit toy profile in `tests/unit/test_circuit_profile.py` (tmp repo, fake pipeline/checks): policy loads with `circuit:`, hash mismatch is refused, a corner id/stability id from the profile drives blocked/regression decisions, extra trusted glob changes the digest only for that circuit.

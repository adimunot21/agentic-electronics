# agentic-electronics

An experiment in letting an AI agent change an electronic circuit design, while an independent checker decides whether each change is accepted.

The agent proposes small edits to a parts list. It never judges its own work. A separate evaluator re-runs the real simulations (ngspice, KiCad) on the edited design and accepts or rejects the change against a policy written beforehand. The test circuit is an analog anti-aliasing filter in front of a 12-bit ADC.

This is research code. Nothing here is a board ready to manufacture.

## Status

Newest first. New entries are added on top; old entries are not rewritten.

* **2026-11-19. Feedback loop fixed; third task version frozen, not yet run.** After the second run, the harness now shows the agent a rejection's failing values and limits before it can stop the run, refuses unchanged or repeated designs without spending an evaluation, and judges progress by objective movement plus feasibility instead of counting violations. Task `improve-wide-retune-alias-v3` is the same task and rules as before, with one added sentence in what the agent is told: the score is the worst case over all tolerance corners. Plumbing controls pass under the new trusted digest. No model has run on it.
* **2026-11-19. Second live run finished, nothing accepted** (`runs/agent/improve-live-2`, task `improve-wide-retune-alias`). The run stopped on the harness's no-progress rule after 2 of 3 allowed evaluations. The first submission was an unchanged design (rejected, no improvement). The second changed one capacitor and cut worst-case stopband gain by 0.92 dB, which beats the 0.5 dB target, but pushed the highest corner gain to 1.373 against a limit of 1.3645, so the evaluator rejected it. One run, one task. No claim beyond that.
* **2026-11-19. Second task frozen, not yet run** (`improve-wide-retune-alias`, policy sha256 `7a02c547…`). Same objective and rules as the first task, but all eight filter parts of both stages may be edited and the target is a 0.5 dB gain in worst-case stopband rejection. No scripted solution exists for it. Plumbing controls only have been run; no model has touched it.
* **2026-11-19. Second task not frozen.** I tried to design a second improvement task on different parts of the circuit. I could not argue from the equations that a meaningful improvement is reachable under the current rules, so no task was frozen. The reasoning is in [`docs/second-task-attainability.md`](docs/second-task-attainability.md). The two part numbers from the accepted change were looked up at distributors and look orderable; see [`evidence/candidate-verification-improve-live-1.yaml`](evidence/candidate-verification-improve-live-1.yaml). First public push of this repository.
* **2026-11-19. First live improvement run accepted** (`runs/agent/improve-live-1`). One run, one task. Details below.
* **2026-11-18. First live repair run accepted** (`runs/agent/dev-c1-live-1`). One run, one task. Details below.
* **Earlier.** Tool chain, reference design, evaluator and harness built. See the git history.

## What works today

1. **The harness and the evaluator.** A controller gives the model a small set of typed tools (read the design, run quick checks, propose a patch, submit). Patches may only edit allowed fields of allowed parts. On submit, the evaluator runs the full pipeline itself and compares against the policy. There are two modes: *restoration* (the repaired design must match the original measurements) and *improvement* (a changed design is accepted only if it beats a target fixed in advance and breaks no hard limit). The unit tests (172) and the real-tool integration tests pass at the current tree. A wrong repair, written as a scripted test case, is rejected by the evaluator on real simulation evidence.
2. **A live repair.** A component value in the design was deliberately corrupted (C1 written as 1 nF against a part number for 18 nF). The model found the mismatch with the tools, patched it, and the evaluator accepted the result: one evaluation, 6 model calls (`runs/agent/dev-c1-live-1/summary.json`).
3. **A live improvement.** The target was fixed in a policy file before any candidate was evaluated: lower the worst-case stopband gain from 19 kHz to 1 MHz by at least 0.25 dB, over all 1024 corners of the tolerance box the evaluator uses, without breaking the passband, gain or other limits. The model changed R4, R5 and C3. The evaluator measured an improvement of 0.27 dB (-58.74 dB to -59.01 dB) and accepted it for that objective: 10 model calls, one evaluation (`runs/agent/improve-live-1/`, decision in `evaluations/decision_01.json`).

The intended action boundary: the model has no file or shell access (the CLI is started with its tools off, with no settings sources or MCP servers, from an empty directory), it can only emit one JSON action per reply, and the controller rejects anything malformed or out of scope before it does anything. This is how it is meant to work and how the tests exercise it. I have not shown that it is isolated or secure.

## Known limits

* One accepted change on one task, once. This is not evidence of a general design ability.
* The final design in the improvement run is the same as one candidate I had written in a scripted search earlier in the repo, which the model could not see. I cannot tell whether that is independent derivation or coincidence.
* The stability check (REQ-STAB) fails on the baseline design. A revised version of it is proposed but not approved. The policy lets a candidate leave it failing but never worse.
* The parts list has unresolved items: the two connectors have no part number, and the model for startup, saturation and overload behaviour is not tested.
* The new part numbers (`RC0603FR-0718K7L`, `C0805C512J5GACTU`) are unverified candidates. A distributor lookup found matching listings but I read no datasheet and placed no order.
* Costs are the CLI's list-price estimates, not billing. The harness has no cumulative dollar cap.
* The Python harness and its evaluator are trusted code. Any change to them changes the "trusted-code digest" recorded in each decision.

## Run the tests

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest tests/unit -q                       # offline unit tests
./scripts/bootstrap.sh                            # pulls pinned docker images, downloads the TI op-amp model (hash-checked)
uv run pytest tests/integration -q -m integration # needs docker; runs real KiCad, ngspice and Freerouting
```

The integration tests need docker and the bootstrap step. The TI op-amp model is not in this repository (its licence is not stated), so a fresh clone cannot run the full pipeline until it has been downloaded. Running a model against the harness (`python -m ae.harness.run --provider claude-cli`) spends model usage: read [`docs/agent-improvement-mode.md`](docs/agent-improvement-mode.md) first.

## Layout

* `src/ae/` toolchain runner, KiCad schematic and netlist handling, SPICE suite, checks, release outputs.
* `src/ae/harness/` the agent harness: controller, tools, patch validation, evaluator, frozen task policies (`tasks/`).
* `designs/`, `requirements/`, `evidence/`, `models/`, `analysis/` the circuit, its contract, part evidence and model provenance.
* `tests/` unit and integration tests. `scripts/` helper scripts (including fixture runs; they are scripted, not model output).
* `runs/` saved evidence: reference runs and agent runs. Only small files are kept (trace, summary, decisions, patches, diffs). Bulk simulation output is ignored.
* `docs/` architecture, harness and improvement-mode notes, limitations ledger, decisions.

## More reading

[Harness](docs/agent-harness.md) - [Improvement mode, accounting and launch notes](docs/agent-improvement-mode.md) - [Limitations ledger](docs/limitations.md) - [Architecture](docs/architecture.md) - [Prior art](docs/prior-art.md) - [Roadmap](docs/agentic-roadmap.md)

## Licence and third-party material

No licence has been chosen yet, so by default all rights are reserved. KiCad libraries are CC-BY-SA-4.0 with a design exception, and Freerouting (GPL-3.0) is run as a separate process. The TI op-amp model is downloaded locally and never committed.

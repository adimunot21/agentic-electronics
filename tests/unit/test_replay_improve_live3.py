"""Deterministic regression replay of the real improve-live-3 run (runs/agent/improve-live-3/trace.jsonl): the model's exact replies are fed to a Controller with a stub pipeline.

improve-live-3 spent a ~100 s evaluation on a design that was electrically the baseline (C3 value+MPN reverted, but mpn_status had been forced to unverified_candidate, so the bytes differed).
Stub pipeline / no model / no docker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ae.harness.controller import Controller, Limits, Mode
from ae.harness.designstate import canonical_state
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.patch import sha256_text
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task

from tests.unit.test_improvement import PRISTINE, synth_pipeline

TRACE = Path(__file__).resolve().parents[2] / "runs/agent/improve-live-3/trace.jsonl"
pytestmark = pytest.mark.skipif(not TRACE.exists(), reason=f"{TRACE} (committed live-run trace) is absent; nothing to replay")
TASK_ID = "improve-wide-retune-alias-v3"


def replies() -> list[str]:
    events = [json.loads(line) for line in TRACE.read_text().splitlines() if line.strip()]
    return [e["text"] for e in events if e["type"] == "model_reply"]


class Rig:
    def __init__(self, tmp_path):
        self.calls, self.root, self.snapshots = [], tmp_path, []
        self.base_dir = tmp_path / "base"
        self.base = synth_pipeline(PRISTINE, self.base_dir)

    def _pipe(self, components_path, out_dir):
        self.calls.append(Path(out_dir).name)
        return synth_pipeline(components_path, out_dir)

    def run(self, limits):
        ev = TrustedEvaluator()
        ev.run_pipeline = self._pipe

        def snap(_transcript):                                   # records the workspace + refusal counter at every model call, then plays the next scripted reply
            self.snapshots.append((self.ctl.state["refused_submits"], self.ctl.ws.text()))
            return self._script.pop(0)
        self._script = replies()
        script = [snap] * len(self._script)
        self.provider = ScriptedProvider(script)
        self.ctl = Controller(self.root / "run", load_task(TASK_ID), self.provider, ev, limits, Mode.ENGINEERED_SINGLE, baseline_record=self.base, baseline_dir=self.base_dir,
                              checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names})
        self.summary = self.ctl.run()
        self.events = [json.loads(l) for l in (self.root / "run/trace.jsonl").read_text().splitlines()]
        return self.ctl


def test_replay_refuses_the_electrically_identical_resubmission_and_never_cycles(tmp_path):
    rig = Rig(tmp_path)
    ctl = rig.run(Limits(max_iterations=3, max_model_calls=30))
    s = ctl.state
    print("OBSERVED", rig.summary["stop_reason"], {k: s[k] for k in ("iteration", "evaluations", "refused_submits", "state_revisits", "model_calls", "invalid_replies")}, rig.calls)
    patches = [e for e in rig.events if e["type"] == "tool_call" and e["tool"] == "propose_patch"]
    assert len(patches) == 7 and all(e["result"]["ok"] and e["result"]["result"]["applied"] for e in patches)      # every base_sha256 lined up: none rejected as stale
    refused = [e for e in rig.events if e["type"] == "submit_refused"]
    assert len(refused) == 1 and s["refused_submits"] == 1
    assert "no_change_from_baseline" in json.dumps(refused[0])
    assert s["evaluations"] == 0 and s["iteration"] == 0 and rig.calls == []
    assert rig.summary["stop_reason"] not in ("repeated_identical_patch", "exploration_cycling")
    assert s["state_revisits"] < Limits().max_state_revisits


def test_replay_ends_only_because_the_script_runs_out(tmp_path):
    rig = Rig(tmp_path)
    rig.run(Limits(max_iterations=3, max_model_calls=30))
    # The live run was stopped externally after its last reply; with no model left the ScriptedProvider returns error 'script_exhausted', which the controller reports as provider_error.
    assert rig.provider.i == len(replies())
    assert rig.summary["stop_reason"] == "provider_error"


def test_replay_with_tight_revisit_bound_stops_with_exploration_cycling(tmp_path):
    rig = Rig(tmp_path)
    ctl = rig.run(Limits(max_iterations=3, max_model_calls=30, max_state_revisits=2))
    print("OBSERVED tight", rig.summary["stop_reason"], ctl.state["state_revisits"], ctl.state["model_calls"])
    assert rig.summary["stop_reason"] == "exploration_cycling"
    assert ctl.state["state_revisits"] == 2 and rig.calls == []


def test_old_byte_hash_would_have_evaluated_the_refused_submission(tmp_path):
    rig = Rig(tmp_path)
    ctl = rig.run(Limits(max_iterations=3, max_model_calls=30))
    baseline_text = PRISTINE.read_text()
    at_refusal = next(text for refused, text in rig.snapshots if refused == 1)          # first model call after the refusal: workspace is exactly what was refused
    assert sha256_text(at_refusal) != sha256_text(baseline_text)                          # byte hash: "new design" -> the old harness would have run the pipeline
    assert canonical_state(at_refusal) == canonical_state(baseline_text) == ctl.state["baseline_state"]

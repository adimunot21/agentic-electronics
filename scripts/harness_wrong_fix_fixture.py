"""FIXTURE (scripted agent, real evaluator): the tempting WRONG repair (edit the MPN to match the corrupted value) must be rejected by the real full pipeline; then the correct repair is accepted.
Reuses a previously captured baseline ONLY if the trusted-code digest matches (checked)."""
import json, re, sys
from pathlib import Path
from ae.harness.controller import Controller, Limits, Mode
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ScriptedProvider
from ae.harness.tasks import load_task
from ae.harness.workspace import REPO_ROOT

def A(tool, **args): return {"reasoning": "fixture", "action": {"tool": tool, "args": args}}
def patch(part, field, to):
    def f(transcript):
        shas = re.findall(r'"(?:design_manifest_sha256|manifest_sha256|new_base_sha256)": "([0-9a-f]{64})"', transcript)
        return json.dumps(A("propose_patch", patch={"summary": "fixture", "base_sha256": shas[-1], "ops": [{"op": "set_field", "part": part, "field": field, "to": to}]}))
    return f
live = REPO_ROOT / "runs/agent/dev-c1-live-1"
ev = TrustedEvaluator()
assert json.loads((live / "task.json").read_text())["trusted_digest"] == ev.digest_at_start["combined"], "trusted code changed since the baseline was captured: not reusing it"
baseline = json.loads((live / "baseline_record.json").read_text())
script = [A("get_design"), patch("C1", "mpn_candidate", "C0805C102J5GACTU"), A("run_check", check="bom_consistency"), A("submit", summary="WRONG: match MPN to the corrupted value"),
          patch("C1", "mpn_candidate", "C0805C183J5GACTU"), patch("C1", "value", "18n"), A("submit", summary="restore both")]
ctl = Controller(REPO_ROOT / "runs/agent/dev-c1-wrongfix-fixture", load_task("dev-c1-value"), ScriptedProvider(script), ev, Limits(max_iterations=3), Mode.ENGINEERED_SINGLE, baseline_record=baseline)
print(json.dumps(ctl.run(), indent=1, default=str))

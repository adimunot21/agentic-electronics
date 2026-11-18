"""REAL-tool harness evidence (docker + KiCad + ngspice + shimmed TI model). No fakes: these call the same adapters the evaluator uses."""
import shutil
from pathlib import Path

import pytest
import yaml

from ae.harness.checks import run_fast
from ae.harness.patch import sha256_text
from ae.harness.tasks import create_defect, load_task
from ae.harness.workspace import REPO_ROOT, Workspace

pytestmark = pytest.mark.integration
ready = shutil.which("docker") and (REPO_ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib").exists()
PRISTINE = REPO_ROOT / "designs/fe-aaf-001/components.yaml"


@pytest.mark.skipif(not ready, reason="needs docker + shimmed model")
def test_real_checks_fail_on_the_defect_and_clear_after_a_valid_repair(tmp_path):
    task = load_task("dev-c1-value")
    defect_path = tmp_path / "defect.yaml"
    rec = create_defect(task, PRISTINE, defect_path)
    assert rec["from"] == "18n" and rec["to"] == "1n"
    before = run_fast(["bom_consistency", "ac_response"], defect_path, tmp_path / "d")
    assert before["bom_consistency"]["outcome"] == "failed" and "VALUE" in before["bom_consistency"]["detail"]
    assert before["ac_response"]["outcome"] == "failed"                                      # the circuit really changed: gain/flatness limits violated
    ws = Workspace(tmp_path / "ws")
    ws.init(defect_path)
    ws.propose({"summary": "restore", "base_sha256": ws.sha256(), "ops": [{"op": "set_field", "part": "C1", "field": "value", "to": "18n"}]})
    assert ws.sha256() == sha256_text(PRISTINE.read_text())                                  # exact restoration of the adopted manifest
    after = run_fast(["bom_consistency", "ac_response"], ws.manifest, tmp_path / "a")
    ref = run_fast(["bom_consistency", "ac_response"], PRISTINE, tmp_path / "r")
    assert after["bom_consistency"]["outcome"] == ref["bom_consistency"]["outcome"] == "blocked"    # blocked (J1/J2 unresolved) - honest, NOT passed
    assert after["ac_response"]["outcome"] == "passed" and after["ac_response"]["measurements"] == ref["ac_response"]["measurements"]

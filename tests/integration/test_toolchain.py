"""Integration tests: invoke the REAL tools in the pinned images (needs docker; run `make setup` first).
These are evidence about the toolchain, not mocks. Marked `integration` so `make test` stays offline."""
import json
import shutil
from pathlib import Path

import pytest

from ae.toolchain import REPO_ROOT, run_in_image

pytestmark = pytest.mark.integration
docker_missing = shutil.which("docker") is None


@pytest.mark.skipif(docker_missing, reason="docker not installed")
class TestPinnedTools:
    def test_kicad_cli_version_is_pinned(self):
        r = run_in_image("v", ["kicad-cli", "version"], REPO_ROOT)
        assert r.exit_code == 0 and r.stdout.startswith("10.0.8")

    def test_ngspice_is_present_in_the_kicad_image(self):
        r = run_in_image("v", ["bash", "-c", "ngspice --version | grep -m1 -o 'ngspice-[0-9.]*'"], REPO_ROOT)
        assert r.stdout.strip() == "ngspice-44.2"

    def test_pcbnew_exposes_specctra_dsn_and_ses(self):
        r = run_in_image("v", ["python3", "/opt/ae/pcb_ops.py", "selftest"], REPO_ROOT)
        assert "dsn export: True" in r.stdout

    def test_tiny_board_chain_netlist_to_routed_drc_clean(self):
        from ae.probes import tiny_chain
        assert tiny_chain.main() == 0
        stages = {s["name"]: s for s in json.loads((tiny_chain.WORK / "stages.json").read_text())}
        assert stages["drc_unrouted"]["exit_code"] == 5          # DRC sees the missing routes
        assert stages["drc_routed"]["exit_code"] == 0            # ... and is clean after routing

    def test_parity_check_is_live_negative_control(self, tmp_path):
        from ae.probes import tiny_chain
        src = tiny_chain.WORK
        if not (src / "tiny.kicad_pcb").exists():
            pytest.skip("run the tiny chain first")
        shutil.copy(src / "tiny.kicad_sch", tmp_path / "tiny.kicad_sch")
        (tmp_path / "tiny.kicad_pcb").write_text((src / "tiny.kicad_pcb").read_text().replace('"R2"', '"R9"'))
        r = run_in_image("drc", ["kicad-cli", "pcb", "drc", "--schematic-parity", "--format", "json", "--severity-all", "-o", "d.json", "tiny.kicad_pcb"], tmp_path)
        report = json.loads((tmp_path / "d.json").read_text())
        assert len(report["schematic_parity"]) >= 2 and report["violations"] == []


@pytest.mark.skipif(docker_missing or not (REPO_ROOT / "vendor/kicad-libs-10.0.8/symbols").exists(), reason="needs docker + extracted KiCad libs")
def test_generated_schematic_erc_clean_and_netlist_matches_intent(tmp_path):
    from ae.designs import fe_aaf_001 as design
    from ae.stages import compare_netlist_to_intent, kicad_erc, kicad_netlist
    (tmp_path / "s.kicad_sch").write_text(design.build().to_text())
    erc, rep = kicad_erc(tmp_path, "s.kicad_sch")
    assert erc.outcome == "passed", rep
    assert kicad_netlist(tmp_path, "s.kicad_sch", "s.net").outcome == "passed"
    assert compare_netlist_to_intent(tmp_path / "s.net", design.intended_nets())[0] == "passed"


@pytest.mark.skipif(docker_missing or not (REPO_ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib").exists(), reason="TI model not fetched (make setup)")
def test_shim_model_reaches_true_dc_operating_point(tmp_path):
    """Regression test for the diagnosed ngspice 44.2 / OPA2314 DC-convergence failure (docs/decisions/ADR-004)."""
    shutil.copy(REPO_ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib", tmp_path)
    (tmp_path / ".spiceinit").write_text("set ngbehavior=psa\n")
    (tmp_path / "t.cir").write_text(".include opa2314_ngspice_shim.lib\nVcc vcc 0 5\nVin in 0 2.5\nX1 in out vcc 0 out OPA2314\nRl out 0 10k\n.control\nop\nprint v(out)\n.endc\n.end\n")
    r = run_in_image("ng", ["ngspice", "-b", "-o", "t.log", "t.cir"], tmp_path)
    log = (tmp_path / "t.log").read_text()
    assert "Transient op" not in log
    assert abs(float(log.split("v(out) =")[1].split()[0]) - 2.5) < 0.005

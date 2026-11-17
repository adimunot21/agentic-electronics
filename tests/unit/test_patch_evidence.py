import shutil

import pytest
import yaml

from ae.evidence import collect_inputs, stale_reasons
from ae.evidence import sha256_path
from ae.patch import PatchConflict, apply_patch

COMPONENTS = '''parts:
  RISO: {role: "x", value: "100", tol: "1%", mpn_candidate: "OLD1"}
  CF: {role: "y", value: "1n", mpn_candidate: "OLD2"}
'''


def make(tmp_path, base_hash=None, from_value="100"):
    (tmp_path / "designs").mkdir()
    comp = tmp_path / "designs/components.yaml"
    comp.write_text(COMPONENTS)
    patch = {"id": "p1", "base": {"file": "designs/components.yaml", "sha256": base_hash or sha256_path(comp)},
             "changes": [{"part": "RISO", "field": "value", "from": from_value, "to": "475", "also": {"mpn_candidate": "NEW1"}}],
             "invalidates": ["sim"]}
    pf = tmp_path / "p1.yaml"
    pf.write_text(yaml.safe_dump(patch))
    return comp, pf


def test_patch_applies_and_reports_new_hash(tmp_path):
    comp, pf = make(tmp_path)
    old = sha256_path(comp)
    res = apply_patch(pf, tmp_path)
    assert yaml.safe_load(comp.read_text())["parts"]["RISO"]["value"] == "475" and res["old_sha256"] == old != res["new_sha256"]
    assert yaml.safe_load(comp.read_text())["parts"]["CF"]["value"] == "1n"        # untouched part unchanged


def test_patch_on_changed_base_is_refused_not_merged(tmp_path):
    comp, pf = make(tmp_path)
    comp.write_text(COMPONENTS.replace("OLD2", "someone-else-edited"))
    with pytest.raises(PatchConflict):
        apply_patch(pf, tmp_path)
    assert "someone-else-edited" in comp.read_text()


def test_patch_with_wrong_from_value_is_refused(tmp_path):
    comp, pf = make(tmp_path, from_value="999")
    with pytest.raises(PatchConflict):
        apply_patch(pf, tmp_path)


def test_replacing_a_part_makes_dependent_evidence_stale(tmp_path):
    comp, pf = make(tmp_path)
    inputs_before = collect_inputs({"components": comp})
    apply_patch(pf, tmp_path)
    reasons = stale_reasons(inputs_before, collect_inputs({"components": comp}))
    assert reasons and reasons[0].startswith("components")

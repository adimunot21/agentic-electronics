import pytest

from ae.ownership import ManualEditConflict, guarded_write


def test_first_write_then_regeneration_allowed(tmp_path):
    f, led = tmp_path / "x.kicad_sch", tmp_path / "l.json"
    guarded_write(f, "a", led)
    guarded_write(f, "b", led)
    assert f.read_text() == "b"


def test_manual_edit_is_never_silently_overwritten(tmp_path):
    f, led = tmp_path / "x.kicad_sch", tmp_path / "l.json"
    guarded_write(f, "a", led)
    f.write_text("a edited in KiCad")
    with pytest.raises(ManualEditConflict):
        guarded_write(f, "b", led)
    assert f.read_text() == "a edited in KiCad"
    guarded_write(f, "b", led, discard_manual_edits=True)     # explicit decision only
    assert f.read_text() == "b"


def test_unknown_existing_file_is_refused(tmp_path):
    f = tmp_path / "x.kicad_sch"
    f.write_text("someone else's file")
    with pytest.raises(ManualEditConflict):
        guarded_write(f, "b", tmp_path / "l.json")

"""Regression for the CI-only failure: containers run as uid 1000, a CI runner's work directories belong to uid 1001 (see ae.toolchain.open_for_container)."""
import os
import stat

from ae import toolchain


def test_open_for_container_makes_the_tree_writable_when_the_host_is_not_uid_1000(tmp_path, monkeypatch):
    work = tmp_path / "w"
    (work / "sub").mkdir(parents=True)
    (work / "a.txt").write_text("x")
    (work / "sub" / "b.txt").write_text("y")
    for p in (work, work / "sub"):
        os.chmod(p, 0o700)                                         # pytest tmp dirs are 0700
    os.chmod(work / "a.txt", 0o600)
    monkeypatch.setattr(os, "getuid", lambda: 1001)
    toolchain.open_for_container(work)
    for d in (work, work / "sub"):
        assert stat.S_IMODE(d.stat().st_mode) & 0o777 == 0o777
    assert stat.S_IMODE((work / "a.txt").stat().st_mode) & 0o666 == 0o666 and stat.S_IMODE((work / "sub" / "b.txt").stat().st_mode) & 0o666 == 0o666


def test_open_for_container_is_a_noop_for_uid_1000(tmp_path, monkeypatch):
    work = tmp_path / "w"
    work.mkdir()
    os.chmod(work, 0o700)
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    toolchain.open_for_container(work)
    assert stat.S_IMODE(work.stat().st_mode) == 0o700

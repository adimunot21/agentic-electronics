"""Generated-file ownership guard (see docs/ownership.md).

Rule: a generator may (re)write an editable KiCad file only if the file is absent or byte-identical to what
the generator wrote last time (hash in a ledger). If a human (or another agent) edited it since, the write is
refused with ManualEditConflict: the edit must be merged or the generator output explicitly discarded.
Never a silent overwrite.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ManualEditConflict(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def guarded_write(path: Path, text: str, ledger_path: Path, *, discard_manual_edits: bool = False) -> str:
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    key = path.name
    if path.exists() and not discard_manual_edits:
        current = sha256_file(path)
        if key not in ledger:
            raise ManualEditConflict(f"{path} exists but was never written by the generator; refusing to overwrite")
        if current != ledger[key]:
            raise ManualEditConflict(f"{path} was modified since the generator last wrote it (ledger {ledger[key][:12]} != file {current[:12]})")
    path.write_text(text)
    ledger[key] = sha256_text(text)
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True))
    return ledger[key]


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        f, led = Path(tmp) / "a.kicad_sch", Path(tmp) / "ledger.json"
        guarded_write(f, "v1", led)
        guarded_write(f, "v2", led)                      # unchanged by humans -> allowed
        f.write_text("v2 + manual edit")
        try:
            guarded_write(f, "v3", led)
            raise SystemExit("expected conflict")
        except ManualEditConflict as exc:
            print("conflict raised as expected:", str(exc)[:70])
        guarded_write(f, "v3", led, discard_manual_edits=True)
        print("ownership ok")

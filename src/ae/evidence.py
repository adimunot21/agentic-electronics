"""Evidence is bound to the exact inputs that produced it (sha256 of every input file).

A result is *stale* when any recorded input hash differs from the current file. Stale evidence must not be
reported as a pass for the current design: callers get the list of changed inputs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_inputs(files: dict[str, Path]) -> dict[str, str | None]:
    """name -> sha256 (None if the file does not exist: a missing input is visible, not silently skipped)."""
    return {name: (sha256_path(p) if p.exists() else None) for name, p in sorted(files.items())}


def stale_reasons(recorded: dict[str, str | None], current: dict[str, str | None], relevant: set[str] | None = None) -> list[str]:
    names = set(recorded) | set(current)
    if relevant is not None:
        names &= relevant
    return [f"{n}: {str(recorded.get(n))[:10]} -> {str(current.get(n))[:10]}" for n in sorted(names) if recorded.get(n) != current.get(n)]


def save(path: Path, inputs: dict, extra: dict | None = None) -> None:
    path.write_text(json.dumps({"inputs": inputs, **(extra or {})}, indent=2, sort_keys=True))


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "a.txt"
        f.write_text("1")
        old = collect_inputs({"a": f})
        assert stale_reasons(old, collect_inputs({"a": f})) == []
        f.write_text("2")
        assert stale_reasons(old, collect_inputs({"a": f}))
        print("evidence ok")

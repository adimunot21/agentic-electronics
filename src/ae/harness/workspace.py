"""Isolated run workspace: the ONLY design state the agent can change, and only through validated patches applied by the controller."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

from ae.harness.patch import ValidatedPatch, apply_validated, sha256_text, validate_patch
from ae.harness.contracts import ToolError

REPO_ROOT = Path(__file__).resolve().parents[3]

# Everything the evaluator and checks trust. Hashed at run start and again before/after every evaluation: any change => evaluation BLOCKED.
TRUSTED_GLOBS = ["requirements/fe-aaf-001.yaml", "src/ae/**/*.py", "tools_kicad/*.py", "analysis/derive_filter.py", "evidence/pins/*.yaml",
                 "designs/fe-aaf-001/placement.yaml", "designs/fe-aaf-001/fe_aaf_001.kicad_pro", "models/shim-record.json", "models/cache/OPA2314/opa2314_ngspice_shim.lib"]


def trusted_digest(repo: Path = REPO_ROOT) -> dict:
    files = {}
    for pattern in TRUSTED_GLOBS:
        for path in sorted(repo.glob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                files[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
    combined = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"combined": combined, "n_files": len(files), "files": files}


class Workspace:
    def __init__(self, root: Path):
        self.root = root
        self.manifest = root / "components.yaml"
        self.history: list[dict] = []

    def init(self, source_manifest: Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "patches").mkdir(exist_ok=True)
        self._write(source_manifest.read_text())

    def _write(self, text: str) -> None:
        if self.manifest.exists():
            os.chmod(self.manifest, stat.S_IWUSR | stat.S_IRUSR)
        self.manifest.write_text(text)
        os.chmod(self.manifest, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)          # read-only between controller writes

    def text(self) -> str:
        return self.manifest.read_text()

    def sha256(self) -> str:
        return sha256_text(self.text())

    def propose(self, patch: object) -> dict:
        """Validate (scope, shape, stale base) then apply. Rejection leaves the workspace untouched."""
        before = self.text()
        vp: ValidatedPatch = validate_patch(patch, before)
        after = apply_validated(vp, before)
        record = {"seq": len(self.history) + 1, "summary": vp.summary, "ops": vp.ops, "fingerprint": vp.fingerprint, "forced_side_effects": vp.forced,
                  "before_sha256": sha256_text(before), "after_sha256": sha256_text(after)}
        self._write(after)
        (self.root / "patches" / f"{record['seq']:03d}.json").write_text(json.dumps(record, indent=1))
        self.history.append(record)
        return record

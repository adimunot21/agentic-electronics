"""Task registry (dev vs held-out) and controlled-defect creation by a SEPARATE test setup (not by the agent, not by the evaluator)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ae.harness.patch import sha256_text

TASK_DIR = Path(__file__).parent / "tasks"


@dataclass(frozen=True)
class Task:
    id: str
    split: str
    description: str
    defect: dict


def list_tasks() -> list[dict]:
    return [{"id": y["id"], "split": y["split"]} for y in (yaml.safe_load(p.read_text()) for p in sorted(TASK_DIR.glob("*.yaml")))]


def load_task(task_id: str, allow_heldout: bool = False) -> Task:
    for p in TASK_DIR.glob("*.yaml"):
        y = yaml.safe_load(p.read_text())
        if y["id"] == task_id:
            if y["split"] == "heldout" and not allow_heldout:
                raise PermissionError(f"task {task_id!r} is HELD OUT: refuse to run during development (pass allow_heldout=True for an evaluation run)")
            return Task(y["id"], y["split"], y["description"], y["defect"])
    raise KeyError(task_id)


def create_defect(task: Task, pristine_manifest: Path, out_manifest: Path) -> dict:
    """Apply the task's exact change to a COPY of the pristine manifest. Returns the exact record (also the proof of what was done)."""
    text = pristine_manifest.read_text()
    part, field, to = task.defect["part"], task.defect["field"], task.defect["to"]
    parts = yaml.safe_load(text)["parts"]
    old = str(parts[part][field])
    if old == to:
        raise ValueError("defect would not change anything")
    pat = re.compile(rf'^(  {re.escape(part)}:.*?{re.escape(field)}: )"[^"]*"', re.M)
    new_text, n = pat.subn(lambda m: f'{m.group(1)}"{to}"', text, count=1)
    if n != 1:
        raise ValueError("defect target not found")
    out_manifest.write_text(new_text)
    return {"task": task.id, "part": part, "field": field, "from": old, "to": to, "pristine_sha256": sha256_text(text), "defective_sha256": sha256_text(new_text),
            "created_by": "test setup (ae.harness.tasks.create_defect), not by the agent"}

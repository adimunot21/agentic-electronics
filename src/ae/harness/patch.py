"""Constrained design patches for the harness. The agent can change ONLY manifest fields inside an explicit scope; everything else is rejected before application.

Patch (JSON):  {"summary": str, "base_sha256": "<hash of the manifest the agent read>", "ops": [{"op": "set_field", "part": "C1", "field": "value", "to": "18n"}]}
* base_sha256 must equal the CURRENT manifest hash (stale patches are rejected, never merged).
* unknown keys/ops are malformed; `from` is read from the manifest by the validator (the agent cannot misstate it).
* Changing an MPN FORCES mpn_status to `unverified_candidate`: the agent cannot assert manufacturer verification (evidence is not agent-writable).
* Requirements, thresholds, evaluator code, fixtures, reports and evidence files are not addressable at all: there is no path or file operation in the language.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ae.harness.contracts import ToolError


@dataclass(frozen=True)
class ScopePolicy:
    part_pattern: str = r"^[RC]\d+$"                    # discrete passives only; U1, connectors and test points are out of scope
    fields: frozenset = frozenset({"value", "mpn_candidate"})
    max_ops: int = 4
    value_pattern: str = r"^\d+(\.\d+)?[pnumkM]?$"
    mpn_pattern: str = r"^[A-Za-z0-9\-]{6,32}$"


DEFAULT_SCOPE = ScopePolicy()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class ValidatedPatch:
    summary: str
    base_sha256: str
    ops: list[dict]
    fingerprint: str = ""
    forced: list[str] = field(default_factory=list)


def _parts(text: str) -> dict:
    return yaml.safe_load(text)["parts"]


def validate_patch(patch: object, manifest_text: str, scope: ScopePolicy = DEFAULT_SCOPE) -> ValidatedPatch:
    if not isinstance(patch, dict):
        raise ToolError("malformed_patch", "patch must be an object")
    extra = set(patch) - {"summary", "base_sha256", "ops"}
    if extra:
        raise ToolError("malformed_patch", f"unknown patch key(s) {sorted(extra)}: only summary, base_sha256, ops exist", {"keys": sorted(extra)})
    if not isinstance(patch.get("summary"), str) or not isinstance(patch.get("base_sha256"), str) or not isinstance(patch.get("ops"), list):
        raise ToolError("malformed_patch", "summary (str), base_sha256 (str) and ops (list) are required")
    current = sha256_text(manifest_text)
    if patch["base_sha256"] != current:
        raise ToolError("stale_base", "patch was written against a different manifest version; re-read the design and rebase", {"patch_base": patch["base_sha256"][:12], "current": current[:12]})
    ops = patch["ops"]
    if not ops or len(ops) > scope.max_ops:
        raise ToolError("malformed_patch", f"ops must contain 1..{scope.max_ops} operations", {"count": len(ops)})
    parts = _parts(manifest_text)
    clean, forced = [], []
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or set(op) != {"op", "part", "field", "to"}:
            raise ToolError("malformed_patch", f"op {i}: exactly the keys op, part, field, to are allowed", {"got": sorted(op) if isinstance(op, dict) else type(op).__name__})
        if op["op"] != "set_field":
            raise ToolError("malformed_patch", f"op {i}: only 'set_field' exists (got {op['op']!r})")
        if not all(isinstance(op[k], str) for k in ("part", "field", "to")):
            raise ToolError("malformed_patch", f"op {i}: part, field, to must be strings")
        if not re.fullmatch(scope.part_pattern, op["part"]):
            raise ToolError("out_of_scope", f"op {i}: part {op['part']!r} is outside the writable scope (discrete R/C parts only)", {"part": op["part"]})
        if op["field"] not in scope.fields:
            raise ToolError("out_of_scope", f"op {i}: field {op['field']!r} is not writable (allowed: {sorted(scope.fields)})", {"field": op["field"]})
        if op["part"] not in parts:
            raise ToolError("malformed_patch", f"op {i}: unknown part {op['part']!r}", {"parts": sorted(parts)})
        pattern = scope.value_pattern if op["field"] == "value" else scope.mpn_pattern
        if not re.fullmatch(pattern, op["to"]):
            raise ToolError("malformed_patch", f"op {i}: {op['field']} {op['to']!r} does not match the allowed format")
        old = str(parts[op["part"]].get(op["field"]))
        if old == op["to"]:
            raise ToolError("malformed_patch", f"op {i}: no change ({op['part']}.{op['field']} is already {old!r})")
        clean.append({**op, "from": old})
        if op["field"] == "mpn_candidate":
            forced.append(f"{op['part']}.mpn_status -> unverified_candidate (agent cannot assert manufacturer verification)")
    fp = sha256_text(yaml.safe_dump([{k: v for k, v in o.items()} for o in clean], sort_keys=True))
    return ValidatedPatch(patch["summary"], patch["base_sha256"], clean, fp, forced)


def apply_validated(vp: ValidatedPatch, manifest_text: str) -> str:
    text = manifest_text
    for op in vp.ops:
        text = _set(text, op["part"], op["field"], op["to"])
        if op["field"] == "mpn_candidate":
            text = _set(text, op["part"], "mpn_status", "unverified_candidate", quoted=False)
    yaml.safe_load(text)                                  # must still parse
    return text


def _set(text: str, part: str, field_name: str, to: str, quoted: bool = True) -> str:
    q = '"' if quoted else ""
    pat = re.compile(rf'^(  {re.escape(part)}:.*?{re.escape(field_name)}: ){q}[^",}}]*{q}', re.M)
    new, n = pat.subn(lambda m: f"{m.group(1)}{q}{to}{q}", text, count=1)
    if n != 1:
        raise ToolError("internal", f"could not apply {part}.{field_name}: pattern not found in manifest text")
    return new

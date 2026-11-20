"""Circuit profile: everything the harness knows about WHICH circuit it is improving, as data, so a second circuit is added by a profile + policy + requirements + checks module.

FE_AAF_001 is defined here IN CODE with exactly the literals the harness used before the seam existed, and it is the default everywhere: a policy without a `circuit:` key resolves to it, so
every FE code path computes what it computed before (see tests/unit/test_circuit_profile.py, which pins the literals and the trusted digest).
Other circuits load from src/ae/harness/circuits/<id>.yaml with STRICT validation (unknown keys, wrong types, missing files, non-importable dotted paths are errors).
Not generalised (see docs/second-circuit/harness-seam.md): the corners_summary.json gain_min/gain_max shape and the H1-H6 hard-constraint semantics.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Callable

import yaml

CIRCUITS_DIR = Path(__file__).parent / "circuits"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")                      # no path separators: the id names a file


class ProfileError(Exception):
    """Invalid or unknown circuit profile (improvement.load_policy re-raises it as PolicyError)."""


@dataclass(frozen=True)
class CircuitProfile:
    id: str
    requirements_path: str                       # protected requirements file (hash-pinned by the policy)
    baseline_design: str                         # adopted reference manifest (default pristine design)
    corner_suffix: str                           # objective/hard-constraint check id = requirement id + suffix
    corner_check_ids: tuple                      # corner-swept check ids: a `blocked` one blocks the verdict
    nominal_objective_check: str                 # nominal counterpart of the objective (H6 non-regression)
    stability_check_id: str | None               # check with the status/non-regression special rule (None disables it)
    guard_traded_ids: tuple                      # nominal metrics governed by hard constraints, skipped by the preview margin projection
    standing_claims: tuple                       # claims every accepted decision blocks: ({"claim":..,"why":..}, ...)
    brief_keys: tuple                            # baseline checks shown in the task brief, after the objective check
    labels: dict                                 # text used in output diffs / READMEs
    checks: str                                  # dotted "module:function", contract (names, manifest, workdir) -> {name: result}
    pipeline: str | None                         # dotted "module:function" (components_path, out_dir) -> record; None = the FE reference run
    evidence_ledger: str | None                  # BOM verification ledger (get_design_evidence); None = none
    derivation_record: str | None                # design-time derivation json (get_design_evidence); None = none
    derivation_refs: tuple                       # refs that have an entry in the derivation record's params (identity map)
    extra_trusted_globs: tuple = ()              # appended to the trusted digest ONLY for this circuit
    edit_sanity: bool = True                     # the ideal-model edit_sanity hook exists for this circuit


FE_AAF_001 = CircuitProfile(
    id="fe-aaf-001",
    requirements_path="requirements/fe-aaf-001.yaml",
    baseline_design="designs/fe-aaf-001/components.yaml",
    corner_suffix="-CORNERS",
    corner_check_ids=("REQ-ALIAS-CORNERS", "REQ-FLAT-CORNERS", "REQ-GAIN-CORNERS"),
    nominal_objective_check="REQ-ALIAS",
    stability_check_id="REQ-STAB",
    guard_traded_ids=("REQ-ALIAS", "REQ-GAIN", "REQ-FLAT"),
    standing_claims=({"claim": "MODEL_STARTUP/SATURATION/OVERLOAD_RECOVERY", "why": "checks stay not_tested; behaviour of the modified stage-2 time constant under startup/saturation/overload is unvalidated"},
                     {"claim": "REQ-STAB", "why": "rev 2 stays FAILED, rev 3 stays PROPOSED/NOT APPROVED; this task never resolves it"}),
    brief_keys=("REQ-FLAT-CORNERS", "REQ-GAIN-CORNERS", "REQ-FLAT", "REQ-GAIN", "REQ-ALIAS", "REQ-STAB", "REQ-BIAS", "REQ-IQ", "REQ-ZIN", "REQ-HEADROOM", "REQ-SETTLE", "REQ-NOISE"),
    labels={"reference_path": "designs/fe-aaf-001/components.yaml",
            "improvement_readme_task": "improve-stage2-alias",
            "improvement_readme_caveats": "NOT a released or validated board: release_ready is false; REQ-STAB stays failed; new MPNs are unverified_candidate; MODEL_* stay not_tested. Blocked claims: "},
    checks="ae.harness.checks:run_fast",
    pipeline=None,
    evidence_ledger="evidence/bom-verification.yaml",
    derivation_record="analysis/filter_design_0002.json",
    derivation_refs=("C1", "C2", "C3", "R1", "R2", "R3", "R4", "R5"),
)

_LIST_FIELDS = {"corner_check_ids", "guard_traded_ids", "brief_keys", "derivation_refs", "extra_trusted_globs", "standing_claims"}
_STR_FIELDS = {"id", "requirements_path", "baseline_design", "corner_suffix", "nominal_objective_check", "checks"}
_OPT_STR_FIELDS = {"stability_check_id", "pipeline", "evidence_ledger", "derivation_record"}
_REQUIRED = {"id", "requirements_path", "baseline_design", "corner_suffix", "corner_check_ids", "nominal_objective_check", "checks", "labels"}


def resolve_callable(dotted: str) -> Callable:
    """'package.module:function' -> the function. ProfileError when it cannot be imported or is not callable."""
    module_name, sep, attr = dotted.partition(":")
    if not sep or not module_name or not attr:
        raise ProfileError(f"{dotted!r}: expected 'module:function'")
    try:
        fn = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ProfileError(f"{dotted!r} is not importable: {type(exc).__name__}: {exc}") from exc
    if not callable(fn):
        raise ProfileError(f"{dotted!r} is not callable")
    return fn


def load_profile(circuit_id: str, circuits_dir: Path | None = None) -> CircuitProfile:
    """FE_AAF_001 for its own id, else src/ae/harness/circuits/<id>.yaml (strictly validated; dotted paths must import)."""
    if circuit_id == FE_AAF_001.id:
        return FE_AAF_001
    if not isinstance(circuit_id, str) or not _ID.match(circuit_id):
        raise ProfileError(f"invalid circuit id {circuit_id!r}")
    path = (circuits_dir or CIRCUITS_DIR) / f"{circuit_id}.yaml"
    if not path.is_file():
        raise ProfileError(f"unknown circuit {circuit_id!r}: {path} does not exist")
    y = yaml.safe_load(path.read_text())
    if not isinstance(y, dict):
        raise ProfileError(f"{path.name}: not a mapping")
    known = {f.name for f in fields(CircuitProfile)}
    if set(y) - known:
        raise ProfileError(f"{path.name}: unknown key(s) {sorted(set(y) - known)}")
    if _REQUIRED - set(y):
        raise ProfileError(f"{path.name}: missing key(s) {sorted(_REQUIRED - set(y))}")
    if y["id"] != circuit_id:
        raise ProfileError(f"{path.name}: id {y['id']!r} != file name {circuit_id!r}")
    kw: dict = {}
    for name, value in y.items():
        if name in _STR_FIELDS and not (isinstance(value, str) and value):
            raise ProfileError(f"{path.name}: {name} must be a non-empty string")
        if name in _OPT_STR_FIELDS and value is not None and not (isinstance(value, str) and value):
            raise ProfileError(f"{path.name}: {name} must be a string or null")
        if name in _LIST_FIELDS:
            if not isinstance(value, list):
                raise ProfileError(f"{path.name}: {name} must be a list")
            if name == "standing_claims":
                if not all(isinstance(c, dict) and set(c) == {"claim", "why"} for c in value):
                    raise ProfileError(f"{path.name}: standing_claims entries must be {{claim, why}}")
                value = tuple(dict(c) for c in value)
            else:
                if not all(isinstance(x, str) for x in value):
                    raise ProfileError(f"{path.name}: {name} must be a list of strings")
                value = tuple(value)
        if name == "labels" and not (isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())):
            raise ProfileError(f"{path.name}: labels must be a mapping of strings")
        if name == "edit_sanity" and not isinstance(value, bool):
            raise ProfileError(f"{path.name}: edit_sanity must be a boolean")
        kw[name] = value
    for name in _OPT_STR_FIELDS | {"standing_claims", "brief_keys", "guard_traded_ids", "derivation_refs"}:
        kw.setdefault(name, () if name in _LIST_FIELDS else None)
    kw["labels"] = {**{k: "" for k in FE_AAF_001.labels}, **kw["labels"]}                 # every label key exists, so call sites never KeyError
    # a profile's extra trusted globs always cover its own profile file (the harness trusts it)
    kw["extra_trusted_globs"] = tuple(kw.get("extra_trusted_globs", ())) + (str(Path("src/ae/harness/circuits") / f"{circuit_id}.yaml"),)
    profile = CircuitProfile(**kw)
    resolve_callable(profile.checks)
    if profile.pipeline:
        resolve_callable(profile.pipeline)
    return profile

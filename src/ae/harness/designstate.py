"""Canonical ELECTRICAL design state, used to tell a genuinely different design from the same design written differently.

Why: improve-live-3 reverted a part to its original value AND original part number, but the harness had forced `mpn_status -> unverified_candidate` on the first edit, so the manifest text
differed from the baseline by one bookkeeping field. A byte hash called it a new design and a full ~100 s evaluation was spent on an electrically unchanged circuit.

Rules (this is the contract; tests in tests/unit/test_designstate.py):
* The state is every part's fields EXCEPT derived/descriptive bookkeeping (`mpn_status`, `role`, `note`).
* `value` is compared numerically (`5.6n` == `5.60n`), everything else as written.
* `mpn_candidate` IS part of the state. A different part number is a different design even at the same value: it needs its own independent part validation and is never merged into a duplicate.
* Nothing here reuses or skips a verification: it only decides whether a submission would evaluate something new.
"""
from __future__ import annotations

import hashlib
import json

import yaml

from ae.spice import parse_si

DERIVED_FIELDS = frozenset({"mpn_status", "role", "note"})


def _canon_value(text) -> str:
    try:
        return format(parse_si(str(text)), ".12g")
    except ValueError:
        return str(text)


def canonical_parts(manifest_text: str) -> dict:
    parts = yaml.safe_load(manifest_text)["parts"]
    out = {}
    for ref in sorted(parts):
        fields = {k: (_canon_value(v) if k == "value" else v) for k, v in parts[ref].items() if k not in DERIVED_FIELDS}
        out[ref] = dict(sorted(fields.items(), key=lambda kv: kv[0]))
    return out


def canonical_state(manifest_text: str) -> str:
    """sha256 of the canonical electrical state of a manifest."""
    return hashlib.sha256(json.dumps(canonical_parts(manifest_text), sort_keys=True, default=str).encode()).hexdigest()

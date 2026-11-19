"""canonical_state contract: electrical-state identity ignores bookkeeping, compares value numerically, but never merges a different MPN."""
from __future__ import annotations

from pathlib import Path

from ae.harness.designstate import canonical_parts, canonical_state

MANIFEST = Path(__file__).resolve().parents[2] / "designs/fe-aaf-001/components.yaml"
BASE = MANIFEST.read_text()

C3_LINE = 'C3: {role: "stage-2 feedback capacitor", value: "5.6n", tol: "5%", dielectric: C0G, package: "0805", mpn_candidate: "C0805C562J5GACTU", mpn_status: verified, manufacturer_candidate: "Kemet"}'


def variant(old: str, new: str) -> str:
    assert BASE.count(old) == 1, old
    return BASE.replace(old, new)


def test_identical_text_is_equal():
    assert canonical_state(BASE) == canonical_state(BASE)


def test_mpn_status_difference_is_ignored():
    text = variant(C3_LINE, C3_LINE.replace("mpn_status: verified", "mpn_status: unverified_candidate"))
    assert text != BASE and canonical_state(text) == canonical_state(BASE)


def test_value_is_compared_numerically():
    text = variant('value: "5.6n"', 'value: "5.60n"')
    assert text != BASE and canonical_state(text) == canonical_state(BASE)


def test_role_and_note_text_are_ignored():
    text = variant('role: "stage-2 feedback capacitor"', 'role: "something else entirely"').replace("UNVERIFIED availability assumption", "reworded note")
    assert text != BASE and canonical_state(text) == canonical_state(BASE)


def test_different_value_is_a_different_state():
    assert canonical_state(variant('value: "5.6n"', 'value: "5.1n"')) != canonical_state(BASE)


def test_different_mpn_candidate_at_same_value_is_a_different_state():
    text = variant(C3_LINE, C3_LINE.replace("C0805C562J5GACTU", "C0805C562J5GACTA"))
    assert canonical_parts(text)["C3"]["value"] == canonical_parts(BASE)["C3"]["value"]
    assert canonical_state(text) != canonical_state(BASE)


def test_key_order_within_a_part_is_irrelevant():
    reordered = 'C3: {mpn_candidate: "C0805C562J5GACTU", manufacturer_candidate: "Kemet", package: "0805", dielectric: C0G, tol: "5%", value: "5.6n", mpn_status: verified, role: "stage-2 feedback capacitor"}'
    text = variant(C3_LINE, reordered)
    assert text != BASE and canonical_state(text) == canonical_state(BASE)

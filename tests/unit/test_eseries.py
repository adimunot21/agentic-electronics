"""E-series membership for the patch validator (policy v4 enforce_series)."""
import pytest

from ae.harness.eseries import E12, E24, E96, in_series, mantissa_int


def test_table_sizes_and_nesting():
    assert (len(E12), len(E24), len(E96)) == (12, 24, 96) and len(set(E96)) == 96 and list(E96) == sorted(E96)
    assert set(E12) <= set(E24)                                                                    # E12 is a subset of E24
    assert E96[0] == 100 and E96[-1] == 976


@pytest.mark.parametrize("value,series,expected", [("59k", "E96", True), ("59.6k", "E96", False), ("60.4k", "E96", True), ("16.5k", "E96", True), ("76.8k", "E96", True), ("6.19k", "E96", True),
                                                   ("475", "E96", True), ("1M", "E96", True), ("976k", "E96", True), ("18n", "E24", True), ("20n", "E24", True), ("21n", "E24", False),
                                                   ("5.6n", "E24", True), ("5.1n", "E24", True), ("2.2u", "E24", True), ("330p", "E24", True), ("20n", "E12", False), ("10.4n", "E24", False), ("10.5n", "E24", False), ("10.6k", "E24", False), ("10.7n", "E24", False), ("1.04n", "E24", False), ("11n", "E24", True), ("18n", "E12", True)])
def test_membership_across_decades(value, series, expected):
    assert in_series(value, series) is expected


def test_the_manifest_baseline_values_belong_to_their_declared_series():
    import yaml
    from pathlib import Path
    parts = yaml.safe_load(Path("designs/fe-aaf-001/components.yaml").read_text())["parts"]
    for ref, p in parts.items():
        if p.get("series") == "E96":
            assert in_series(str(p["value"]), "E96"), ref


def test_unsupported_series_and_mantissa_rounding():
    with pytest.raises(ValueError):
        in_series("1k", "E192")
    assert mantissa_int(59000.0) == 590 and mantissa_int(5.9e-9) == 590 and mantissa_int(1.0) == 100

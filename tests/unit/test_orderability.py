"""Offline orderability cache: provenance validation, range coverage, and the 'never in stock' guarantee."""
import copy
import datetime as dt
from pathlib import Path

import pytest
import yaml

from ae.harness.eseries import E96
from ae.parts.orderability import (ALLOWED_STATUSES, CacheError, DEFAULT_CACHE_PATH, encode_resistance_code, load_cache, orderability_of, parse_cache)

FAMILY = "yageo_rc_l_0603_1pct"
CACHE = load_cache()


def _raw() -> dict:
    return yaml.safe_load(DEFAULT_CACHE_PATH.read_text())


def _every_e96_value_in_range():
    for exponent, scale in enumerate((1.0, 10.0, 100.0, 1e3, 1e4, 1e5, 1e6)):          # decades 1 ohm .. 1 Mohm (9.76 M last)
        for mantissa in E96:
            yield mantissa / 100.0 * scale
    yield 1.0e7                                                                         # 10 Mohm: stated upper limit, inclusive


def test_every_e96_value_in_range_is_catalogue_range_covered_by_value_and_by_mpn():
    values = list(_every_e96_value_in_range())
    assert len(values) == 7 * 96 + 1
    for ohms in values:
        by_value = orderability_of((FAMILY, repr(ohms)), CACHE)
        assert by_value.status == "catalogue_range_covered", ohms
        by_mpn = orderability_of(f"RC0603FR-07{encode_resistance_code(ohms)}L", CACHE)
        assert by_mpn.status in ("catalogue_range_covered", "listing_evidenced"), ohms


@pytest.mark.parametrize("value", ["0.976", "0.1", "10.2M", "11M", "100M", "59.6k", "10.4k", "1.01k", "0"])
def test_out_of_range_or_non_e96_is_unknown(value):
    assert orderability_of((FAMILY, value), CACHE).status == "unknown"


def test_improve_live_6_value_is_not_covered():
    assert orderability_of("RC0603FR-0759K6L", CACHE).status == "unknown"                 # 59.6k: not E96


@pytest.mark.parametrize("mpn", ["RC0603JR-0710KL", "RC0805FR-0710KL", "RC0603FR-07010KL", "RC0603FR-07XXL", "NOT_A_PART", "C0805C474J5GACTU", ""])
def test_unrecognised_or_non_canonical_mpns_are_unknown(mpn):
    assert orderability_of(mpn, CACHE).status == "unknown"


def test_unknown_family_and_bad_query_types():
    assert orderability_of(("no_such_family", "10k"), CACHE).status == "unknown"
    with pytest.raises(TypeError):
        orderability_of(42, CACHE)                                                          # type: ignore[arg-type]


def test_listing_evidenced_for_exactly_the_mpns_in_the_evidence_ledger():
    ledger = yaml.safe_load(Path("evidence/bom-verification.yaml").read_text())
    ledger_mpns = {line["mpn"] for line in ledger["lines"]}
    assert set(CACHE.listings) <= {m.upper() for m in ledger_mpns}, "a cached listing has no ledger evidence"
    for mpn in ("RC0603FR-0759KL", "RC0603FR-0776K8L", "RC0603FR-076K19L", "RC0603FR-0716K5L", "C0805C183J5GACTU", "C0805C102J5GACTU", "C0805C562J5GACTU"):
        assert orderability_of(mpn, CACHE).status == "listing_evidenced"


def test_unevidenced_capacitor_is_unknown_not_covered():
    assert orderability_of("C0805C472J5GACTU", CACHE).status == "unknown"                  # no grounded Kemet range statement exists
    assert CACHE.known_gaps


def test_never_returns_in_stock_or_any_unlisted_status():
    assert "in_stock" not in ALLOWED_STATUSES
    for query in ("RC0603FR-0759KL", "RC0603FR-0760K4L", "nonsense", (FAMILY, "10k"), (FAMILY, "7")):
        assert orderability_of(query, CACHE).status in ALLOWED_STATUSES


def test_results_carry_provenance_and_never_claim_stock():
    for query in ("RC0603FR-0759KL", (FAMILY, "60.4k"), "C0805C183J5GACTU"):
        result = orderability_of(query, CACHE)
        assert result.source and result.source["retrieved"] and result.source["doc_id"] and result.source["revision"]
        assert any("stock" in item for item in result.does_not_prove)


def test_shipped_cache_entries_all_have_provenance():
    for entry in [*CACHE.families.values(), *CACHE.listings.values()]:
        assert entry["source"]["retrieved"] <= dt.date(2026, 11, 20)
        assert entry["proves"] and entry["does_not_prove"] and entry["caveats"]


@pytest.mark.parametrize("path,missing", [(("families", 0, "source", "retrieved"), "retrieved"), (("families", 0, "source", "doc_id"), "doc_id"), (("families", 0, "source", "revision"), "revision"),
                                          (("families", 0, "source"), "source"), (("listing_seen", 0, "source"), "source"), (("listing_seen", 3, "source", "retrieved"), "retrieved"),
                                          (("families", 0, "does_not_prove"), "does_not_prove")])
def test_loader_rejects_missing_provenance(path, missing):
    raw = copy.deepcopy(_raw())
    node = raw
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    with pytest.raises(CacheError):
        parse_cache(raw)


def test_loader_rejects_missing_url_and_evidence_ref():
    raw = copy.deepcopy(_raw())
    raw["families"][0]["source"].pop("url")
    with pytest.raises(CacheError, match="url or an evidence_ref"):
        parse_cache(raw)


def test_loader_rejects_bad_schema_version_future_date_and_forbidden_keys():
    for mutate, message in ((lambda r: r.update(schema_version=2), "schema_version"),
                            (lambda r: r["families"][0]["source"].update(retrieved=dt.date.today() + dt.timedelta(days=3)), "future"),
                            (lambda r: r["listing_seen"][0].update(stock=1200), "forbidden key"),
                            (lambda r: r["families"][0].update(price=0.01), "forbidden key"),
                            (lambda r: r["families"][0].update(value_series="E3"), "value_series")):
        raw = copy.deepcopy(_raw())
        mutate(raw)
        with pytest.raises(CacheError, match=message):
            parse_cache(raw)


def test_loader_rejects_unparseable_file(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: [unclosed")
    with pytest.raises(CacheError):
        load_cache(bad)
    with pytest.raises(CacheError):
        load_cache(tmp_path / "missing.yaml")


def test_encode_resistance_code_examples():
    assert [encode_resistance_code(v) for v in (59e3, 76.8e3, 6190, 16.5e3, 475, 1.0, 1e7, 100e3)] == ["59K", "76K8", "6K19", "16K5", "475R", "1R", "10M", "100K"]

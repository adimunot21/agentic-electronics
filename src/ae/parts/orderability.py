"""Offline orderability cache: loader, validation and `orderability_of` lookup.

Scope (deliberately narrow): CATALOGUE EXISTENCE only. This module can say that a manufacturer's data sheet states a value range for a part family
(`catalogue_range_covered`), that an exact ordering code was seen in evidence we already hold (`listing_evidenced`), or that we know nothing (`unknown`).
It can never say a part is in stock, in production or cheap: no such data is legitimately obtainable offline, and the status vocabulary has no such value.

Design / contracts
  orderability.yaml --load_cache()--> OrderabilityCache (validated, immutable) --orderability_of(query)--> OrderabilityResult
  * load fails closed: unknown schema version, missing provenance (source, retrieval date, proves/does_not_prove), forbidden keys (stock/price/...) -> CacheError.
  * lookup never raises for unknown parts; it returns status 'unknown'. Precedence: listing_evidenced > catalogue_range_covered > unknown.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Union

import yaml

from ae.harness.eseries import SERIES, in_series
from ae.spice import parse_si

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_CACHE_PATH = Path(__file__).parent / "data" / "orderability.yaml"
Status = Literal["catalogue_range_covered", "listing_evidenced", "unknown"]
ALLOWED_STATUSES: tuple[str, ...] = ("catalogue_range_covered", "listing_evidenced", "unknown")
# Any of these keys anywhere in the cache means someone tried to store volatile commercial data: refuse the whole file.
FORBIDDEN_KEYS = frozenset({"stock", "in_stock", "price", "unit_price", "availability", "lead_time", "quantity_available", "lifecycle", "lifecycle_status"})
FAMILY_REQUIRED = ("family_id", "manufacturer", "mpn_template", "kind", "tolerance_pct", "package", "basis", "source", "proves", "does_not_prove", "caveats")
# A family states coverage in ONE of two honest ways: a continuous series+range statement (resistors) or an explicit per-value table (capacitors).
KEMET_MPN = re.compile(r"C0805C(\d{2})(\d)J5GACTU")
KEMET_TEMPLATE = "C0805C{code}J5GACTU"
LISTING_REQUIRED = ("mpn", "basis", "source", "proves", "does_not_prove", "caveats")
SOURCE_REQUIRED = ("doc_id", "revision", "retrieved")
RC_MPN = re.compile(r"RC(\d{4})([FJ])R-07(\w+?)L")


class CacheError(ValueError):
    """The cache file violates the schema or lacks provenance."""


@dataclass(frozen=True)
class OrderabilityResult:
    status: Status
    basis: str
    caveats: tuple[str, ...] = field(default_factory=tuple)
    source: dict[str, Any] | None = None
    does_not_prove: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OrderabilityCache:
    families: dict[str, dict[str, Any]]
    listings: dict[str, dict[str, Any]]
    known_gaps: tuple[dict[str, Any], ...]
    generated: dt.date


def _as_date(value: Any, where: str) -> dt.date:
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, str):
        try:
            value = dt.date.fromisoformat(value)
        except ValueError as exc:
            raise CacheError(f"{where}: bad date {value!r}") from exc
    if not isinstance(value, dt.date):
        raise CacheError(f"{where}: date expected, got {value!r}")
    if value > dt.date.today():
        raise CacheError(f"{where}: retrieval date {value} is in the future")
    return value


def _reject_forbidden(node: Any, where: str) -> None:
    if isinstance(node, dict):
        for key, child in node.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise CacheError(f"{where}: forbidden key {key!r} (the cache holds catalogue existence only)")
            _reject_forbidden(child, f"{where}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            _reject_forbidden(child, f"{where}[{index}]")


def _check_entry(entry: Any, required: tuple[str, ...], where: str) -> None:
    if not isinstance(entry, dict):
        raise CacheError(f"{where}: mapping expected")
    missing = [key for key in required if key not in entry or entry[key] in (None, "", [])]
    if missing:
        raise CacheError(f"{where}: missing required field(s) {missing}")
    source = entry["source"]
    if not isinstance(source, dict):
        raise CacheError(f"{where}.source: mapping expected")
    missing_source = [key for key in SOURCE_REQUIRED if source.get(key) in (None, "")]
    if missing_source:
        raise CacheError(f"{where}.source: missing provenance field(s) {missing_source}")
    if not (source.get("url") or source.get("evidence_ref")):
        raise CacheError(f"{where}.source: needs a url or an evidence_ref")
    _as_date(source["retrieved"], f"{where}.source.retrieved")
    for list_key in ("proves", "does_not_prove", "caveats"):
        if not isinstance(entry[list_key], list) or not all(isinstance(item, str) and item for item in entry[list_key]):
            raise CacheError(f"{where}.{list_key}: non-empty list of strings expected")


def parse_cache(raw: Any) -> OrderabilityCache:
    """Validate an already-parsed YAML document and return the immutable cache."""
    if not isinstance(raw, dict):
        raise CacheError("cache root must be a mapping")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise CacheError(f"unsupported schema_version {raw.get('schema_version')!r} (expected {SCHEMA_VERSION})")
    _reject_forbidden(raw, "cache")
    families: dict[str, dict[str, Any]] = {}
    for index, family in enumerate(raw.get("families") or []):
        _check_entry(family, FAMILY_REQUIRED, f"families[{index}]")
        if family.get("listed_values_pf") is not None:
            listed = family["listed_values_pf"]
            if not (isinstance(listed, list) and listed and all(isinstance(v, (int, float)) and v > 0 for v in listed)):
                raise CacheError(f"families[{index}]: listed_values_pf must be a non-empty list of positive numbers")
            if family["kind"] != "capacitor" or family.get("voltage_v") in (None, "") or not family.get("dielectric"):
                raise CacheError(f"families[{index}]: a listed_values_pf family needs kind=capacitor, voltage_v and dielectric")
            family["listed_values_pf"] = sorted(float(v) for v in listed)
            if family["family_id"] in families:
                raise CacheError(f"duplicate family_id {family['family_id']!r}")
            families[family["family_id"]] = family
            continue
        for key in ("value_series", "value_min", "value_max"):
            if family.get(key) in (None, ""):
                raise CacheError(f"families[{index}]: missing required field(s) {[key]}")
        if family["value_series"] not in SERIES:
            raise CacheError(f"families[{index}]: unsupported value_series {family['value_series']!r}")
        try:
            low, high = float(family["value_min"]), float(family["value_max"])
        except (TypeError, ValueError) as exc:
            raise CacheError(f"families[{index}]: value_min/value_max must be numbers") from exc
        family["value_min"], family["value_max"] = low, high          # PyYAML reads '1.0e7' as a string; store floats
        if not (0 < low < high):
            raise CacheError(f"families[{index}]: need 0 < value_min < value_max, got {low}, {high}")
        if family["family_id"] in families:
            raise CacheError(f"duplicate family_id {family['family_id']!r}")
        families[family["family_id"]] = family
    listings: dict[str, dict[str, Any]] = {}
    for index, listing in enumerate(raw.get("listing_seen") or []):
        _check_entry(listing, LISTING_REQUIRED, f"listing_seen[{index}]")
        if listing.get("family_id") is not None and listing["family_id"] not in families:
            raise CacheError(f"listing_seen[{index}]: unknown family_id {listing['family_id']!r}")
        mpn = str(listing["mpn"]).upper()
        if mpn in listings:
            raise CacheError(f"duplicate listing for {mpn}")
        listings[mpn] = listing
    gaps = tuple(raw.get("known_gaps") or [])
    return OrderabilityCache(families, listings, gaps, _as_date(raw.get("generated"), "generated"))


def load_cache(path: Path | str = DEFAULT_CACHE_PATH) -> OrderabilityCache:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CacheError(f"cannot read orderability cache {path}: {exc}") from exc
    cache = parse_cache(raw)
    logger.debug("orderability cache loaded: %d families, %d listings", len(cache.families), len(cache.listings))
    return cache


def encode_resistance_code(ohms: float) -> str:
    """Canonical Yageo value code: 16500 -> '16K5', 59000 -> '59K', 475 -> '475R', 6190 -> '6K19', 1e7 -> '10M'."""
    if ohms <= 0:
        raise ValueError("resistance must be positive")
    unit, scale = ("M", 1e6) if ohms >= 1e6 else ("K", 1e3) if ohms >= 1e3 else ("R", 1.0)
    digits = f"{ohms / scale:.3f}".rstrip("0").rstrip(".")
    whole, _, fraction = digits.partition(".")
    return f"{whole}{unit}{fraction}"


def _in_range_and_series(family: dict[str, Any], ohms: float) -> bool:
    low, high = float(family["value_min"]), float(family["value_max"])
    if not (low * (1 - 1e-9) <= ohms <= high * (1 + 1e-9)):
        return False
    try:
        return in_series(repr(ohms), family["value_series"])
    except ValueError:
        return False


def _result(status: Status, entry: dict[str, Any], basis: str) -> OrderabilityResult:
    assert status in ALLOWED_STATUSES
    return OrderabilityResult(status, basis, tuple(entry["caveats"]), dict(entry["source"]), tuple(entry["does_not_prove"]))


def _unknown(reason: str, cache: OrderabilityCache) -> OrderabilityResult:
    gap_notes = tuple(gap.get("reason", "") for gap in cache.known_gaps)
    return OrderabilityResult("unknown", reason, caveats=gap_notes, does_not_prove=("anything about this part",))


def _listed_capacitor_result(family: dict[str, Any], picofarads: float, cache: OrderabilityCache) -> OrderabilityResult:
    if any(math.isclose(picofarads, listed, rel_tol=1e-6) for listed in family["listed_values_pf"]):
        return _result("catalogue_range_covered", family, f"{family['basis']}: {picofarads:g} pF is a listed value for {family['family_id']}")
    return _unknown(f"{picofarads:g} pF is not a value listed in the cached table for {family['family_id']} (unlisted is not the same as non-existent)", cache)


Query = Union[str, tuple[str, str]]


def orderability_of(query: Query, cache: OrderabilityCache | None = None) -> OrderabilityResult:
    """Look up a part by MPN string or by (family_id, value_text), e.g. ("yageo_rc_l_0603_1pct", "59k").

    Returns 'listing_evidenced' for an MPN already in the evidence ledger, 'catalogue_range_covered' when the value is a series member inside the
    family's stated manufacturer range, otherwise 'unknown'. Never returns stock/price/lifecycle claims.
    """
    cache = cache or load_cache()
    if isinstance(query, tuple):
        if len(query) != 2 or not all(isinstance(item, str) for item in query):
            raise TypeError("tuple query must be (family_id, value_text)")
        family_id, value_text = query
        family = cache.families.get(family_id)
        if family is None:
            return _unknown(f"no cached family {family_id!r}", cache)
        try:
            ohms = parse_si(value_text)
        except (ValueError, TypeError):
            return _unknown(f"cannot parse value {value_text!r}", cache)
        if "listed_values_pf" in family:
            return _listed_capacitor_result(family, ohms * 1e12, cache)
        if not _in_range_and_series(family, ohms):
            return _unknown(f"{value_text} is outside the stated range or not a {family['value_series']} value for {family_id}", cache)
        return _result("catalogue_range_covered", family, f"{family['basis']}: {family['value_series']} value inside {family['value_min']:g}-{family['value_max']:g} (ordering scheme {family['mpn_template']})")
    if not isinstance(query, str):
        raise TypeError("query must be an MPN string or (family_id, value_text)")
    mpn = query.strip().upper()
    listing = cache.listings.get(mpn)
    if listing is not None:
        return _result("listing_evidenced", listing, f"exact code recorded in held evidence: {listing['basis']}")
    kemet = KEMET_MPN.fullmatch(mpn)
    if kemet:
        significant, zeros = kemet.groups()
        if int(zeros) > 5:                                                    # 8/9 are the sub-10 pF codes, 6+ is beyond the table: not decoded here
            return _unknown(f"capacitance multiplier digit {zeros} is not in the cached table", cache)
        picofarads = int(significant) * 10 ** int(zeros)
        for family in cache.families.values():
            if family["mpn_template"] == KEMET_TEMPLATE and "listed_values_pf" in family:
                return _listed_capacitor_result(family, float(picofarads), cache)
    match = RC_MPN.fullmatch(mpn)
    if match:
        size, tolerance_letter, code = match.groups()
        for family in cache.families.values():
            if family["mpn_template"] == "RC0603FR-07{code}L" and size == "0603" and tolerance_letter == "F":
                try:
                    ohms = _code_to_ohms(code)
                except (ValueError, TypeError):
                    return _unknown(f"cannot decode value code {code!r}", cache)
                if encode_resistance_code(ohms) != code:
                    return _unknown(f"value code {code!r} is not the canonical code for its value", cache)
                if _in_range_and_series(family, ohms):
                    return _result("catalogue_range_covered", family, f"{family['basis']}: decoded {ohms:g} ohm is an {family['value_series']} value inside the stated range")
                return _unknown(f"decoded {ohms:g} ohm is outside the stated range or not an {family['value_series']} value", cache)
    return _unknown("MPN not in the cache and no cached family covers it", cache)


def _code_to_ohms(code: str) -> float:
    from ae.bomcheck import resistance_from_code
    value = resistance_from_code(code)
    if not math.isfinite(value):
        raise ValueError(code)
    return value


def _sanity_report() -> int:
    """Coverage of every edit-scope part of the v4 task (R1-R5 as E96 0603 Yageo RC, C1-C3 as E24 C0G 0805 Kemet)."""
    import yaml as _yaml
    parts = _yaml.safe_load(Path("designs/fe-aaf-001/components.yaml").read_text())["parts"]
    cache = load_cache()
    print(f"cache: {len(cache.families)} family, {len(cache.listings)} listings, {len(cache.known_gaps)} known gap(s)")
    for ref in ("R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3"):
        mpn = parts[ref]["mpn_candidate"]
        by_mpn = orderability_of(mpn, cache)
        family_id = "yageo_rc_l_0603_1pct" if ref.startswith("R") else "kemet_c0805c_c0g_50v_5pct"
        by_value = orderability_of((family_id, str(parts[ref]["value"])), cache)
        print(f"{ref}: value={parts[ref]['value']:>6} mpn={mpn:<18} by_mpn={by_mpn.status:<24} by_value={by_value.status}")
    assert orderability_of("RC0603FR-0759K6L", cache).status == "unknown"        # 59.6k is not E96 (the improve-live-6 case)
    assert orderability_of("RC0603FR-0760K4L", cache).status == "catalogue_range_covered"
    assert orderability_of(("kemet_c0805c_c0g_50v_5pct", "21n"), cache).status == "unknown"  # not an E24 value
    print("sanity ok: 59.6k -> unknown, 60.4k -> catalogue_range_covered; no 'in_stock' status exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(_sanity_report())

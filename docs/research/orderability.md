# Part orderability: what can legitimately be known offline (backlog item 4)

Date: 2026-11-20. Scope: the part families of the v4 edit scope (R1-R5: Yageo RC 0603 1 % E96; C1-C3: Kemet C0805C...J5GACTU, C0G, 0805, 50 V, 5 %, E24).
Rule followed: no API keys, no accounts, no distributor scraping; manufacturer documents read read-only.

## Bottom line
* Obtainable and grounded: for the **resistors**, the Yageo RC_L data sheet states the catalogue range for RC0603 1 % (E24/E96): 1 ohm to 10 Mohm. Every E96 value in that range therefore falls inside the series' stated catalogue. This is "catalogue existence by range statement".
* Not obtainable offline and legitimately: **stock, price, lead time, current lifecycle**. They are volatile and API-gated or licence-restricted, so nothing of this kind is cached.
* **Capacitors: not grounded.** I could not read a current, legible Kemet statement of the C0G / 0805 / 50 V capacitance range. So no capacitor range is cached; only the four exact codes already evidenced are marked `listing_evidenced`. Unseen values return `unknown`.

## (a) Catalogue existence
### Yageo RC_L, RC0603, 1 %
* Source: Yageo RC_L series data sheet "Product specification - November 14, 2025 V.14", https://www.yageogroup.com/content/datasheet/asset/file/PYU-RC_GROUP_51_ROHS_L, retrieved 2026-11-20, fetched bytes sha256 `7b6f0ae72708ca4b38126f84b5407a3e970a94cb60bc1a0afdaeb25a23d839bb` (11 pages, PDF creation date 2025-11-14).
* Table 2, RC0603, 1/10 W: 5 % (E24) 1 ohm-22 Mohm; **1 % (E24/E96) 1 ohm-10 Mohm**; 0.5 % (E24/E96) 1 ohm-1 Mohm; 0.1 % (E24/E96) 10 ohm-1 Mohm. Ordering scheme `RC0603 F R 07 <value> L` as in the ledger. The two-column text layout is interleaved, so the row-to-range assignment above was read from the extracted text and agrees with the V.11 (July 2020) copy and the V.4 (2009) copy in `evidence/datasheets/yageo_rc/` (sha256 `419181287881...`), which both give 1 % 1 ohm-10 Mohm.
* Corroboration (third-party mirror, not used as the cited source): V.11, https://www.rcscomponents.kiev.ua/datasheets/Yageo_03_18_2021_PYu_RC_Group_51.pdf, sha256 `6b39ec0f684d3204922ac8587f7a3cc6b9751bb693659a24d5253fbda9c3a197`.
* Exceptions: the data sheet's "E24 exception values 10/11/13/15/20/75" note belongs to the marking-code section, not to orderability. Range limits: 1 ohm minimum, 10 Mohm maximum (inclusive).
* What this proves: the series' catalogue statement covers every E96 value 1 ohm-10 Mohm in 1 %. It does **not** prove that a given value is in production, Active, or stocked: a range statement is not a per-value list.
* A reproducible side effect: R1 = 59.6k (improve-live-6) is not E96 and is `unknown`; 59k, 76.8k, 6.19k, 16.5k are `catalogue_range_covered` and, being in the ledger, `listing_evidenced`.

### Kemet C0G 0805 50 V
* Wanted: Kemet C0G data sheet (C1002 family), 0805 / 50 V min/max capacitance and offered E24 values.
* Result: **not obtained.** The current datasheet host (`content.kemet.com`) returned an expired TLS certificate (I did not bypass it). Distributor pages were not fetched (terms; the harness also refused). The only legible-at-all catalogue copy reached was a CERN-hosted 2005/2006 Kemet catalogue (PDF created 2005-10-04), whose extracted C0G table is garbled and out of date, so it is not cited as evidence.
* Evidence we do have, from the earlier session: exact-code Kemet spec sheets (generated 2026-11-17 through search.kemet.com, public tool, sha256s in `evidence/bom-verification.yaml`) for C0805C183J5GACTU, C0805C102J5GACTU, C0805C562J5GACTU, C0805C331J5GACTU. They prove Kemet's tool produced a sheet for those codes; they carry no lifecycle field.
* Next step to close the gap (human, outside this tool): obtain the current Kemet C0G SMD data sheet (C1002 C0G_SMD, any readable copy with revision/date), read the 0805 / 50 V column, and add a `families` entry with limits and any E24 gaps.

## (b) Stock, lifecycle, price: candidate sources, assessed honestly
| Source | Usable offline? | Verdict |
|---|---|---|
| Distributor APIs (DigiKey, Mouser, Octopart/Nexar, LCSC) | need keys/accounts | excluded by constraint; also terms restrict caching and redistribution |
| Distributor web pages | scraping prohibited by their terms; pages returned 403 earlier | excluded |
| Search-engine summaries of listings | snippets only, undated at source | already used as weak `family_spec+listing` evidence; not stock |
| Manufacturer lifecycle/PCN pages (Yageo, Kemet) | web-only, per-part, no public bulk dataset found | not cached; per-part lookup could be done by a human |
| Open datasets of stock/price | none found; stock is commercially licensed and changes daily | none |
| Manufacturer data sheet range statements | yes | used (resistors only) |
Stock and price are volatile (hours) and non-redistributable; a cache of them would be wrong by construction. The manifest's own statement stands: "availability must be re-checked at order time".

## (c) Cache schema and lookup (implemented)
Files: `src/ae/parts/data/orderability.yaml`, `src/ae/parts/orderability.py`, `tests/unit/test_orderability.py`.
* `families[]`: family_id, manufacturer, mpn_template, kind, value_series, value_min, value_max, tolerance_pct, package, basis, source{url, doc_id, revision, sha256, retrieved}, proves[], does_not_prove[], caveats[].
* `listing_seen[]`: only MPNs present in `evidence/bom-verification.yaml` (R1-R6 RC codes, four 0805 C0G codes); same provenance fields (`evidence_ref` replaces `url` where the evidence is a ledger line).
* `known_gaps[]`: records the missing Kemet range.
* Loader fails closed: schema_version, required provenance, future dates, forbidden keys (stock/price/availability/lead_time/lifecycle) anywhere in the file, unknown series.
* `orderability_of(mpn | (family_id, value))` returns `{status, basis, caveats, source, does_not_prove}`, status in `catalogue_range_covered | listing_evidenced | unknown`. There is no `in_stock` value. MPN input is decoded, re-encoded to the canonical Yageo code (so `RC0603FR-07010KL` is rejected), then range and E96 membership are checked.
* Tests: 35 new in `tests/unit/test_orderability.py` (672+1 E96 values by value and by MPN, out-of-range, non-E96, provenance rejection, no in_stock). Full unit suite: 340 passed.

## Integration caveats (not done here)
* `src/ae/**/*.py` is inside the evaluator's trusted-digest globs (`TRUSTED_GLOBS` in `src/ae/harness/workspace.py`), so adding `src/ae/parts/*.py` changes the trusted digest and needs the usual supersession note. The YAML data file is outside the listed `.py` glob, but check the remaining globs before assuming it is untracked.
* Nothing calls the cache yet. Wiring it into the validator (e.g. as a post-acceptance report) would be a policy change, so it was left alone.
* Never present `catalogue_range_covered` as "buildable": it means only "inside the manufacturer's stated catalogue range".

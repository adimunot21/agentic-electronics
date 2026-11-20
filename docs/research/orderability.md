# Part orderability: what can legitimately be known offline (backlog item 4)

Date: 2026-11-20. Scope: the part families of the v4 edit scope (R1-R5: Yageo RC 0603 1 % E96; C1-C3: Kemet C0805C...J5GACTU, C0G, 0805, 50 V, 5 %, E24).
Rule followed: no API keys, no accounts, no distributor scraping; manufacturer documents read read-only.

## Bottom line
* Obtainable and grounded: for the **resistors**, the Yageo RC_L data sheet states the catalogue range for RC0603 1 % (E24/E96): 1 ohm to 10 Mohm. Every E96 value in that range therefore falls inside the series' stated catalogue. This is "catalogue existence by range statement".
* Not obtainable offline and legitimately: **stock, price, lead time, current lifecycle**. They are volatile and API-gated or licence-restricted, so nothing of this kind is cached.
* **Capacitors: grounded only to a 2016 revision.** A legible per-value table (KEMET C1003_C0G, 7/21/2016) lists 77 values for C0805C J5G 50 V; the current revision could not be read. Listed values map to `catalogue_range_covered`; unlisted values return `unknown`.

## (a) Catalogue existence
### Yageo RC_L, RC0603, 1 %
* Source: Yageo RC_L series data sheet "Product specification - November 14, 2025 V.14", https://www.yageogroup.com/content/datasheet/asset/file/PYU-RC_GROUP_51_ROHS_L, retrieved 2026-11-20, fetched bytes sha256 `7b6f0ae72708ca4b38126f84b5407a3e970a94cb60bc1a0afdaeb25a23d839bb` (11 pages, PDF creation date 2025-11-14).
* Table 2, RC0603, 1/10 W: 5 % (E24) 1 ohm-22 Mohm; **1 % (E24/E96) 1 ohm-10 Mohm**; 0.5 % (E24/E96) 1 ohm-1 Mohm; 0.1 % (E24/E96) 10 ohm-1 Mohm. Ordering scheme `RC0603 F R 07 <value> L` as in the ledger. The two-column text layout is interleaved, so the row-to-range assignment above was read from the extracted text and agrees with the V.11 (July 2020) copy and the V.4 (2009) copy in `evidence/datasheets/yageo_rc/` (sha256 `419181287881...`), which both give 1 % 1 ohm-10 Mohm.
* Corroboration (third-party mirror, not used as the cited source): V.11, https://www.rcscomponents.kiev.ua/datasheets/Yageo_03_18_2021_PYu_RC_Group_51.pdf, sha256 `6b39ec0f684d3204922ac8587f7a3cc6b9751bb693659a24d5253fbda9c3a197`.
* Exceptions: the data sheet's "E24 exception values 10/11/13/15/20/75" note belongs to the marking-code section, not to orderability. Range limits: 1 ohm minimum, 10 Mohm maximum (inclusive).
* What this proves: the series' catalogue statement covers every E96 value 1 ohm-10 Mohm in 1 %. It does **not** prove that a given value is in production, Active, or stocked: a range statement is not a per-value list.
* A reproducible side effect: R1 = 59.6k (improve-live-6) is not E96 and is `unknown`; 59k, 76.8k, 6.19k, 16.5k are `catalogue_range_covered` and, being in the ledger, `listing_evidenced`.

### Kemet C0G 0805 50 V (updated, second pass 2026-11-20)
* Source: KEMET `C1003_C0G` "C0G Dielectric, 10 - 250 VDC (Commercial Grade)", footer date **7/21/2016** (PDF created 2016-10-14), https://extranet.mandieselturbo.com/electronics/datasheets/Kemet/MLCC_SMD/C1003_C0G_2016.pdf, sha256 `75db98383c378259270ea98a0e74f33e37a5fc315de897df8858ae654d5b2d34`. It is a third-party-hosted copy of the manufacturer document and **ten years old**, not the current revision. The current host `content.kemet.com` still had an expired TLS certificate (not bypassed); Mouser/DigiKey mirrors were not fetched (harness refused); a Farnell mirror timed out twice.
* Read: Table 1A (0201-1206), pages 5-6 of the PDF. Extracted by word coordinates (the 0805 / 50 V column is the 4th of 7 cells in the 0805 group, x about 446), cross-checked against the layout text. Footnote: "Capacitance range Includes E24 decade values only." (KEMET C1003_C0G).
* Result for C0805C...J5GACTU (0805, 50 V, +-5 % J, C0G): J tolerance is offered from 10 pF up (below 10 pF only B/C/D tolerances). Listed values (77): **every E24 value from 10 pF to 9.1 nF (72 values)**, plus **10, 12, 15, 18, 22 nF** in the 10 nF decade. **11, 13, 16, 20, 24 nF are NOT listed**, nor anything from 27 nF up (the 0805 / 50 V cell is empty). So the table range is 10 pF - 22 nF with gaps, not a clean min/max.
* Consistency check: the four codes with 2026-11-17 Kemet spec sheets (330 pF, 1 nF, 5.6 nF, 18 nF) are all in the table. That supports the table but does not prove 2026 coverage; a newer revision may list more (unlisted = `unknown`, never "does not exist").
* Implemented as a second family `kemet_c0805c_c0g_50v_5pct` with an explicit `listed_values_pf` list (same provenance rules). The v4 task's C1-C3 baseline values (18n, 1n, 5.6n) map to `catalogue_range_covered`; E24 values such as 11n, 13n, 16n, 20n, 24n, 27n return `unknown`.
* Read vs inferred: READ = the table cells, tolerance column, footnote. INFERRED = that an empty 0805 / 50 V cell means "not offered in the 2016 revision" (a layout-based reading of the PDF, plus the thickness-code cell content, which is not an availability statement).

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
* Capacitor families use `listed_values_pf` (explicit per-value table) instead of series+range; `known_gaps[]` records that only the 2016 revision was readable.
* Loader fails closed: schema_version, required provenance, future dates, forbidden keys (stock/price/availability/lead_time/lifecycle) anywhere in the file, unknown series.
* `orderability_of(mpn | (family_id, value))` returns `{status, basis, caveats, source, does_not_prove}`, status in `catalogue_range_covered | listing_evidenced | unknown`. There is no `in_stock` value. MPN input is decoded, re-encoded to the canonical Yageo code (so `RC0603FR-07010KL` is rejected), then range and E96 membership are checked.
* Tests: 71 in `tests/unit/test_orderability.py` (all E96 values by value and by MPN, all 72 E24 values 10 pF-9.1 nF plus 10-22 nF by value and MPN, unlisted and non-E24 values unknown, provenance rejection, no in_stock). Last full unit run: 401 passed, 2 failed in `test_circuit_profile.py` (not my files; concurrent work by another lane).

## Integration caveats (not done here)
* `src/ae/**/*.py` is inside the evaluator's trusted-digest globs (`TRUSTED_GLOBS` in `src/ae/harness/workspace.py`), so adding `src/ae/parts/*.py` changes the trusted digest and needs the usual supersession note. The YAML data file is outside the listed `.py` glob, but check the remaining globs before assuming it is untracked.
* Nothing calls the cache yet. Wiring it into the validator (e.g. as a post-acceptance report) would be a policy change, so it was left alone.
* Never present `catalogue_range_covered` as "buildable": it means only "inside the manufacturer's stated catalogue range".

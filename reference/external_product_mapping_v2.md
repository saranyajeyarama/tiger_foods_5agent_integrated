# External Product Mapping — v2

**What this is:** the resolution layer between external retailer data feeds and Tiger Foods SAP material numbers. Built on top of the CMIR foundation in `cmir_and_crosswalk_v2.md`.

**Scope:** US sold-to KUNNRs only. Three CDM product mapping archetypes: A, B, C. Top 7 customers (each with one sold-to that maps directly to a retailer). Country dimension deliberately omitted from the schema — a single ALTER VIEW adds it when international scale is needed.

---

## The three supported archetypes

| Archetype | Key shape | Typical feed |
|---|---|---|
| **A** | customer_kunnr + retailer_item_number + uom | Walmart Retail Link direct extract; retailer-specific EDI 852 with item number only |
| **B** | customer_kunnr + ean_upc + uom | POS scan logs; retailer feeds that only carry the barcode |
| **C** | customer_kunnr + retailer_item_number + ean_upc + uom | Richest feeds — most modern retailer data exchange includes both keys |

The archetype describes the **shape of the inbound data**, not a property of the crosswalk row. Each crosswalk row knows all keys it has; the query tells us which subset the inbound feed populated.

**Out of scope for this build:** archetypes D-H (sub-channel, syndicated panel, attribute-array). When/if Tiger Foods needs to consume Nielsen / Circana panel data at the UPC level (archetype G), the resolution path is the same as archetype B; the extra panel-level granularity gets handled at the velocity-fact level, not the crosswalk.

## The crosswalk view

`tiger_semantic.dim_external_product_crosswalk` — one row per `(customer_kunnr, material_matnr, uom, ean_upc)` combination. The `retailer_item_number` is an attribute on the row (one value per customer-material pair, repeated across UoM rows). Each row carries flags indicating which archetypes it supports:

| Column | Meaning |
|---|---|
| `customer_kunnr` | Sold-to KUNNR — the retailer key |
| `material_matnr` | Tiger Foods internal material number (the resolution target) |
| `retailer_item_number` | KDMAT from KNMT — retailer's own item ID |
| `ean_upc` | Barcode at this UoM level (consumer / case / pallet) |
| `ean_upc_type` | UC = UPC-A, IA = GTIN-14, IB = SSCC-18, HE = EAN-13 |
| `uom` | EA, CS, or PAL |
| `supports_archetype_a` | TRUE iff retailer_item_number is populated |
| `supports_archetype_b` | TRUE iff ean_upc is populated |
| `supports_archetype_c` | TRUE iff both are populated |
| `is_active` | FALSE if the underlying CMIR/MEAN row is blocked or deletion-flagged |

A typical Tiger Foods SKU with full CMIR + MEAN coverage produces **three rows** in the crosswalk for one customer-material pair: one for EA, one for CS, one for PAL. Each row has the same `retailer_item_number` but a different `ean_upc`. A SKU with missing CMIR data (e.g., a newly-onboarded customer) might have rows where `retailer_item_number IS NULL` and only `ean_upc` resolves — that's archetype B-only resolution, still valid.

## The resolution function

`tiger_semantic.resolve_external_to_internal(in_customer_kunnr, in_retailer_item_number, in_ean_upc, in_uom)` is a BigQuery table-valued function that the agent tools call. It returns one row with the matched MATNR plus provenance metadata.

**Resolution priority: C > A > B.**

| Priority | Match condition | Why this priority |
|---|---|---|
| C (highest) | Both keys provided AND both match on same row | All three identifiers agree — strongest possible confirmation |
| A (middle) | Retailer item number provided and matches | Retailer's own internal ID is the most reliable single identifier — it's what they actually use day-to-day |
| B (lowest) | EAN/UPC provided and matches | Reliable in most cases, but UPCs can be reused across SKUs (especially with private-label co-pack scenarios) — slight degradation risk |

**Conflict handling:** when both keys are provided but they point to different rows (rare but possible — usually indicates stale data on one side), the resolver picks the row where `retailer_item_number` matches. The retailer's own ID is trusted over the barcode.

## Worked example — Walmart Pedigree Dry 22lb, archetype-by-archetype

Tiger Foods MATNR: `MAT-PDG-DOG-DRY-22LB`. Walmart KUNNR: `0001000245`. CMIR / MEAN populated per the seed data in `cmir_raw_sources_v2.sql`.

The crosswalk produces three rows for this combination (EA, CS, PAL).

### Archetype A — Walmart Retail Link direct extract

Inbound feed row:
```json
{"customer_kunnr": "0001000245", "retailer_item_number": "552847391", "uom": "CS"}
```

Resolution call:
```sql
SELECT * FROM `tiger_semantic.resolve_external_to_internal`(
  '0001000245', '552847391', NULL, 'CS');
```

Returns:
| material_matnr | resolved_retailer_item_number | resolved_ean_upc | uom | matched_archetype | match_score |
|---|---|---|---|---|---|
| MAT-PDG-DOG-DRY-22LB | 552847391 | 10023100013777 | CS | A | 80 |

The crosswalk row that matched had both keys; only retailer_item_number was queried, so the matched_archetype is A. The `resolved_ean_upc` is returned as additional information — useful for downstream joins back into POS data or shipment manifests.

### Archetype B — POS scan log

Inbound feed row:
```json
{"customer_kunnr": "0001000245", "ean_upc": "023100013770", "uom": "EA"}
```

Resolution call:
```sql
SELECT * FROM `tiger_semantic.resolve_external_to_internal`(
  '0001000245', NULL, '023100013770', 'EA');
```

Returns:
| material_matnr | resolved_retailer_item_number | resolved_ean_upc | uom | matched_archetype | match_score |
|---|---|---|---|---|---|
| MAT-PDG-DOG-DRY-22LB | 552847391 | 023100013770 | EA | B | 60 |

UPC-only resolution. The matched row also had retailer_item_number, so it's returned for downstream joins, but the matched_archetype is B because only ean_upc was used in the query.

### Archetype C — Modern EDI 852 with both keys

Inbound feed row:
```json
{"customer_kunnr": "0001000245", "retailer_item_number": "552847391", "ean_upc": "10023100013777", "uom": "CS"}
```

Resolution call:
```sql
SELECT * FROM `tiger_semantic.resolve_external_to_internal`(
  '0001000245', '552847391', '10023100013777', 'CS');
```

Returns:
| material_matnr | resolved_retailer_item_number | resolved_ean_upc | uom | matched_archetype | match_score |
|---|---|---|---|---|---|
| MAT-PDG-DOG-DRY-22LB | 552847391 | 10023100013777 | CS | C | 100 |

Both keys agreed; matched_archetype is C and match_score is the maximum 100. This is the strongest possible resolution confirmation.

## How the agents will use this

The Retail Intelligence Agent and Demand Planning Agent both consume external retailer feeds. The pattern is:

1. **Inbound feed arrives** at `tiger_raw.retail_*` (Walmart Retail Link, retailer EDI, syndicated POS, etc.) with whatever archetype shape that feed uses
2. **Resolution at the semantic layer** — the `fct_retail_dc_inventory` and `fct_retail_store_inventory` views (per the revised `retail_data_gap_v2.md`; velocity + promo deferred) call `resolve_external_to_internal` to add `material_matnr` and the normalized identifiers
3. **Agents consume the semantic views** — they see `material_matnr` and never deal with raw external keys
4. **Resolution failures bubble up** as `material_matnr IS NULL` rows in the semantic view — downstream consumers can flag and report these for the data steward (rather than silently dropping them)

When a retailer feed has rows that fail resolution, the Retail Intelligence Agent's reasoning_summary should note the data coverage gap (e.g., "Retail velocity received for Walmart but 14% of rows failed to resolve to MATNR — flagged for data steward review").

## Acceptance check after the crosswalk lands

```sql
-- Coverage summary by UoM and archetype support
SELECT
  uom,
  COUNT(*)                              AS rows,
  COUNTIF(supports_archetype_a)         AS arch_a_supported,
  COUNTIF(supports_archetype_b)         AS arch_b_supported,
  COUNTIF(supports_archetype_c)         AS arch_c_supported,
  COUNTIF(is_active)                    AS active_rows,
  COUNT(DISTINCT customer_kunnr)        AS customers,
  COUNT(DISTINCT material_matnr)        AS materials
FROM `resilience-riskradar.tiger_semantic.dim_external_product_crosswalk`
GROUP BY uom
ORDER BY uom;
```

Expected for the top-7-customer scope:
- 7 customer_kunnr values, US sold-tos only
- Materials count matches the active FERT (finished-good) count in MARA filtered to the top-7 sales footprint
- Archetype A coverage > 90% (CMIR should be well-maintained for top 7)
- Archetype B coverage > 90% at EA, > 70% at CS, < 30% at PAL (master-data pallet barcodes are rare)
- Archetype C coverage tracks the minimum of A and B at each UoM

**Smoke test the TVF on all three archetype paths** before declaring the crosswalk operational — see the verification block at the bottom of `dim_external_product_crosswalk_v2.sql`.

## Adding country later (scalability)

When Tiger Foods expands the crosswalk to MX or CA:

1. Add `country STRING NOT NULL` to the view selection (default `'US'` for existing rows via a `COALESCE` if needed during transition).
2. Add `in_country STRING` to the TVF signature with a default value pattern.
3. Update agent tools to pass `country` (default 'US') in resolution calls.

The structural change is a single ALTER VIEW + ALTER TABLE FUNCTION. No data migration. The deck's "scalability" claim is honored without paying the schema cost up front.

## What's deliberately NOT in this build

- **`dim_retailer`** — KUNNR maps directly to retailer for the top 7 customers, per scope decision. If a retailer hierarchy is needed later (e.g., Walmart parent → Walmart Supercenter / Sam's Club / Walmart.com), add it as a separate `dim_retailer_hierarchy` view; the crosswalk doesn't need to change.
- **Retailer sub-channel (archetypes D-F)** — out of scope.
- **Syndicated/panel resolution (archetypes G-H)** — out of scope; will use archetype B path when needed.
- **External item registry** — a separate raw table for retailer item numbers that don't yet appear in KNMT. For the POC, CMIR is the source of truth. Add later if real resolution gaps emerge.
- **Multi-MATNR resolution** — the TVF returns one row. If a single external key legitimately maps to multiple MATNRs (rare; usually only for retired SKU reuse), the resolution is non-deterministic and returns whichever row sorts first. Flag in the docs as a known limitation.

# Retail Data Gap — v3 contract

**Status:** v2 ships with retail-side data deliberately absent. The Retail Intelligence Agent and Demand Planning Agent return `INSUFFICIENT_DATA` on the retail-driven dimensions; the package is designed for retail data to land as a v3 upgrade without code or agent-prompt changes.

**This document specifies the contract between the agent layer and the v3 data layer.** What's stable vs flexible is called out explicitly so the v3 data team isn't constrained on implementation choices that don't matter to the agents.

---

## What's stable (agent-facing contracts)

These are real design decisions the agent code and prompts depend on:

| Contract | Why it's stable |
|---|---|
| **DC inventory and store inventory are two separate dimensions.** | Different grain (DC ID vs store ID), different volume (~45 DCs vs ~4,700 stores per top retailer), different update cadence, different decision context (buffer-build vs OOS). Documented in the ERD. |
| **Retail tools see Tiger Foods keys, not external keys.** | The tools receive `customer_kunnr` + `material_matnr`. Resolution from `retailer_item_number` / `ean_upc` to MATNR happens in the semantic view layer via the crosswalk TVF, not in the tool. |
| **Tool function names.** | `get_retail_dc_inventory`, `get_retail_store_inventory`, `get_retail_velocity` — these are the agent-facing API. Changing them requires updating the agent prompts. |
| **Pydantic schema shape for the agent's signal payload.** | `RetailerDCInventoryPosition` and `RetailerStoreInventoryPosition` in `schemas_v2.py` define what the agent produces. The Customer Supply Agent's synthesis logic expects these field names. |
| **Stub behavior when data is absent.** | Tools return `data_available: false` payloads. Agent prompts are written to handle this path and produce `INSUFFICIENT_DATA` signals honestly. |
| **Crosswalk integration pattern.** | The retail tools query semantic views; those views call `resolve_external_to_internal` in their DDL to bridge external retailer keys to MATNR. |

## What's flexible (v3 data team owns)

These are implementation choices the v3 data engineering team can make freely:

| Decision | Notes |
|---|---|
| **Actual view names.** | The agent tools default to `fct_retail_dc_inventory`, `fct_retail_store_inventory`, etc., but these are overridable via env vars (`RETAIL_DC_INVENTORY_VIEW`, `RETAIL_STORE_INVENTORY_VIEW`, `RETAIL_VELOCITY_VIEW`, `RETAIL_PROMO_VIEW`). Set them in Cloud Run config; no code change. |
| **View DDL specifics.** | How the views are constructed, what intermediate CTEs are used, what additional columns are exposed beyond the agent-required minimum. |
| **Materialization choice.** | View vs materialized view vs table-with-scheduled-refresh — agents don't care. |
| **Raw landing table design.** | One raw table per retailer, one combined raw table with retailer_id partition, federated query against external warehouse — v3 team's call. |
| **Refresh cadence.** | Daily, hourly, near-real-time — agents will read what's there at decision time. |

## What the agent tools require from the semantic views

The agent tools issue queries with this shape. The v3 team's views must return these columns (additional columns are fine):

### DC inventory tool (`get_retail_dc_inventory`)

Input: `customer_kunnr`, `material_matnr`. Filters to last 7 days, ordered by snapshot date desc and DC id, limited to 50 rows.

Required output columns:
- `customer_kunnr` STRING
- `material_matnr` STRING
- `retailer_dc_id` STRING (the retailer's own DC identifier — e.g., Walmart "DC-30")
- `snapshot_date` DATE
- `on_hand_units` INT64
- `days_of_supply` FLOAT64
- `dos_trend_4w` STRING — one of "DECLINING" / "FLAT" / "BUILDING"

### Store inventory tool (`get_retail_store_inventory`)

Input: `customer_kunnr`, `material_matnr`. Same filter window. Volume can be much larger than DC; limit is 500 rows.

Required output columns:
- `customer_kunnr` STRING
- `material_matnr` STRING
- `store_id` STRING (the retailer's own store identifier)
- `snapshot_date` DATE
- `on_hand_units` INT64
- `oos_flag` BOOL (true if the store reported zero on-hand at any point in the snapshot window)

### Velocity tool (`get_retail_velocity`) — v3-deferred beyond initial release

Input: `customer_kunnr`, `material_matnr`, `weeks_back` (default 8).

Required output columns (when v4 lights this up):
- `customer_kunnr` STRING
- `material_matnr` STRING
- `iso_week` STRING (e.g., "2026-W18")
- `units_sold` INT64
- `store_count_reporting` INT64

## The crosswalk integration pattern

The v3 data engineering team builds the semantic views on top of raw retailer landing tables. The pattern is the same regardless of which retailer's data is being landed.

**Raw landing table** (one or more — v3 design choice):

```sql
-- Example: one row per retailer DC × SKU × snapshot
CREATE TABLE tiger_raw.retail_dc_inventory_raw (
  -- External keys from the retailer feed (no Tiger Foods identifiers yet)
  customer_kunnr        STRING NOT NULL,   -- known because we know which feed it is
  retailer_item_number  STRING,            -- archetype A or C
  ean_upc               STRING,            -- archetype B or C
  retailer_dc_id        STRING NOT NULL,
  snapshot_date         DATE   NOT NULL,
  on_hand_units         INT64,
  days_of_supply        FLOAT64,
  ...
)
```

**Semantic view** (the agent tool's query target):

```sql
CREATE OR REPLACE VIEW tiger_semantic.fct_retail_dc_inventory AS
SELECT
  resolved.material_matnr,
  raw.customer_kunnr,
  raw.retailer_dc_id,
  raw.snapshot_date,
  raw.on_hand_units,
  raw.days_of_supply,
  -- 4-week trend can be computed here as a window function
  CASE
    WHEN <trend logic> THEN 'BUILDING'
    WHEN <trend logic> THEN 'DECLINING'
    ELSE 'FLAT'
  END AS dos_trend_4w
FROM tiger_raw.retail_dc_inventory_raw raw,
LATERAL `tiger_semantic.resolve_external_to_internal`(
  raw.customer_kunnr,
  raw.retailer_item_number,
  raw.ean_upc,
  'CS'
) resolved
WHERE resolved.material_matnr IS NOT NULL  -- drop rows that fail resolution
```

The TVF call is the bridge. Rows where the external key can't be resolved get dropped (or flagged for the data steward) rather than appearing as `material_matnr = NULL` in the agent-facing view. The v3 data team should decide whether unresolved rows go to a dead-letter table for review.

## Use cases that come online when this lands

| Use case | Required data | v2 today |
|---|---|---|
| Buffer-build hint from DC inventory trend | DC inventory + DOS trend | INSUFFICIENT_DATA |
| OOS detection from store inventory | Store inventory + OOS flag | INSUFFICIENT_DATA |
| DOS-aware MABD prioritization (expedite when retailer DOS low) | DC inventory + DOS | Not exercised |
| Lost-sales estimation | Store inventory + OOS rate × historic velocity | v4-deferred |
| GENUINE_PULL classification with high confidence | DC inventory + store inventory + velocity | Beyond initial v3 |
| Closed-loop measurement: did buffer-build classification correlate with returns / aged inventory at retailer? | DC inventory time series | Lights up incrementally |

## Acceptance check after v3 lands

```sql
-- Confirm the agent-facing views exist and have the required columns
SELECT table_name, column_name, data_type
FROM `<PROJECT>.tiger_semantic.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name IN (
  '<RETAIL_DC_INVENTORY_VIEW>',
  '<RETAIL_STORE_INVENTORY_VIEW>'
)
ORDER BY table_name, ordinal_position;

-- Confirm crosswalk resolution succeeded (no NULL material_matnr in the view)
SELECT COUNTIF(material_matnr IS NULL) AS unresolved,
       COUNT(*) AS total
FROM `<PROJECT>.tiger_semantic.<RETAIL_DC_INVENTORY_VIEW>`
WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY);

-- Smoke test the agent tool against the Walmart Pedigree fixture
-- Should return data_available=true with at least one row
curl -X POST <service-url>/sessions \
  -H "Content-Type: application/json" \
  -d @reference/walmart_payload.json
# Check the Retail Intelligence step in the response; classification should
# no longer be INSUFFICIENT_DATA if DC inventory data is present for Walmart.
```

## v3-deferred items (not part of initial retail-data upgrade)

- **POS velocity** (`get_retail_velocity`). Stubbed, agent prompts handle missing case. Comes in v4 or later.
- **Promotional calendar** (`get_promotional_calendar`). Stubbed similarly. Comes alongside velocity.
- **Lost-sales estimation algorithm.** Requires velocity + store inventory together. Stays out until both are present.
- **Per-retailer item master raw tables** (Kenvue-style `walmart_item_master`, `kroger_item_master`, etc.). Deferred unless / until a retailer-master-versus-CMIR conflict actually materializes in practice.

# CMIR and the Internal-External Crosswalk Foundation — v2

**What this is:** the documentation behind `infra/cmir_raw_sources_v2.sql` and `infra/dim_customer_material_v2.sql`. The Customer Material Info Record (CMIR) is the spine of the upcoming internal-external crosswalk that will let external retail data join back to Tiger Foods SAP material numbers.

**Why this came in before the retail data spec:** the existing `reference/retail_data_gap_v2.md` was naive. It assumed retailer feeds (Walmart Retail Link, syndicated POS, EDI 852, retailer inventory) would arrive keyed by `customer_kunnr + material_matnr`. They will not. Retailers send data tagged with **their own** item numbers and product barcodes. Without a crosswalk, the Retail Intelligence Agent has nothing to join against.

---

## What CMIR actually is

In SAP, the **Customer Material Info Record** (table `KNMT`) is the per-customer overlay on top of the material master. One row per `(sales_org, distribution_channel, customer, material)`. It exists because the same physical product gets called different things by different customers:

- **Tiger Foods MATNR**: `MAT-PDG-DOG-DRY-22LB`
- **Walmart's item number** (KDMAT in KNMT): `552847391`
- **Kroger's item number** (KDMAT in KNMT): `KRO-DG-PD-22`
- **Costco's item number** (KDMAT in KNMT): something else again

When Walmart Retail Link sends a POS file, it sends `item_number=552847391`. To know that's Pedigree Dry 22lb, you join through KNMT.

The other half of the crosswalk is the **EAN/UPC/GTIN** barcodes — global standards that any retailer can use. In SAP these live in table `MEAN`, with multiple barcodes per material at different unit-of-measure levels:

| UoM level | What it is | Typical barcode type | Where it's scanned |
|---|---|---|---|
| EA (each / consumer) | The retail-facing bag | UPC-A (12 digits) or EAN-13 | Consumer checkout |
| CS (case) | The case the bag ships in | GTIN-14 (leading 1 indicates case) | Retailer DC receiving |
| PAL (pallet) | The pallet | SSCC-18 | Warehouse / 3PL moves |

So the same physical 22lb bag of Pedigree has at least three valid barcodes, each scanned at a different point in the supply chain.

## The four tables this generates

### `tiger_raw.sap_knmt` — primary CMIR
One row per `(sales_org, distribution_channel, customer, material)`. The critical column is `kdmat` — the customer's own material number. Also carries customer-specific commercial rules: partial delivery indicators, overdelivery / underdelivery tolerances, delivery priority. These already affect OTIF decisions today; the new Retail Intelligence and Demand Planning Agents will use them too.

### `tiger_raw.sap_mara` — material master (EAN-relevant extract)
The default EAN/UPC at base UoM (`ean11`), plus material type, group, weight, status, shelf life. We pull a slim subset of MARA's ~200 columns — only what the crosswalk and the agents need.

### `tiger_raw.sap_mean` — international article numbers
Multiple barcodes per material, one row per `(material, UoM, sequence)`. `hpean = 'X'` marks the primary barcode for that material+UoM combination. The semantic view pivots this into three columns: `ean_consumer`, `ean_case`, `ean_pallet`.

### `tiger_raw.sap_mvke` — sales-org material data
Per `(material, sales_org, distribution_channel)` overrides: minimum order quantity, minimum delivery quantity, sales-org-level delivery time, sales material status. Different sales channels (retail vs foodservice) get different rules.

## The semantic view: `dim_customer_material`

`infra/dim_customer_material_v2.sql` joins all four raw tables into one denormalized record per `(customer, material)`. Native SAP column names are renamed to readable English:

| Raw SAP | Semantic name |
|---|---|
| KDMAT | customer_material_number |
| POSTX | customer_material_description |
| EAN11 (from MEAN, meinh='EA', hpean='X') | ean_consumer |
| EAN11 (from MEAN, meinh='CS', hpean='X') | ean_case |
| EAN11 (from MEAN, meinh='PAL', hpean='X') | ean_pallet |
| ANTLF | max_partial_deliveries |
| UEBTO | overdelivery_tolerance_pct |
| UNTTO | underdelivery_tolerance_pct |
| VMSTA | sales_material_status_label (decoded to ACTIVE/BLOCKED_FOR_SALES/...) |
| FAKSP | billing_block_active (bool) |
| LFREL | is_delivery_relevant (bool) |

This is the view the crosswalk will read from, and the view the Retail Intelligence Agent's resolution tools will use.

## Walmart Pedigree Dry 22lb — worked example

| Field | Value |
|---|---|
| Tiger Foods MATNR | `MAT-PDG-DOG-DRY-22LB` |
| Customer (Walmart) KUNNR | `0001000245` |
| Sales org | `US01` |
| Distribution channel | `10` (retail) |
| **customer_material_number (KDMAT)** | `552847391` |
| customer_material_description | `PEDIGREE ADULT DRY 22LB STORE BAG` |
| **ean_consumer** (UPC-A) | `023100013770` |
| **ean_case** (GTIN-14) | `10023100013777` |
| **ean_pallet** (SSCC-18) | `00310000231000137708` |
| max_partial_deliveries | 1 |
| partial_delivery_indicator | B (no partial — Walmart's rule for this SKU) |
| overdelivery_tolerance_pct | 0.0 |
| underdelivery_tolerance_pct | 5.0 |
| delivery_priority | 01 (highest) |
| sales_material_status_label | ACTIVE |

Note `partial_delivery_indicator = 'B'` — Walmart does not accept partial deliveries on this SKU. The Customer Supply Agent's PARTIAL_FULFILL recommendation in the current Walmart demo scenario would actually be **rejected at the EDI gateway** by Walmart's order-acknowledgment rules. **This is a finding the AI/ML team should validate when they look at the Walmart demo expected output.** The recommendation in `walmart_demo_scenario_v2.md` may need to flip from PARTIAL_FULFILL to ACCEPT-OR-REJECT-AS-WHOLE in light of this CMIR detail. Or the demand-planning side flags that we're committing to a whole-or-nothing order, which materially changes the risk calculus.

This is exactly the kind of correction CMIR enables that wasn't visible to the v2 design.

## How this becomes the internal-external crosswalk

This turn only covered the **internal side** (SAP). The crosswalk has three more components still to build:

### Component 2: External retailer item references
A new table — `tiger_raw.external_retailer_items` (working name) — that lists, for each external retailer data feed, what the retailer's item-number column means and how often it changes. Walmart Retail Link uses `item_number` (varies by retailer DC sometimes); Kroger 84.51° uses `upc`; Circana / NielsenIQ syndicated panels use a UPC-based key. Each retailer feed gets a row.

### Component 3: GS1 barcode registry
A reference table mapping every EAN/UPC/GTIN/SSCC barcode we know about back to its `(matnr, uom_level)`. This is essentially a transpose of `sap_mean` — keyed by the barcode rather than by the material — and includes barcodes from sources OTHER than SAP (e.g., a co-packer-printed case GTIN that didn't make it back into MEAN; a private-label retailer barcode for a SKU we co-pack). Critical for resolution when a retailer's feed gives us a barcode we don't have in MEAN.

### Component 4: The crosswalk view itself
`tiger_semantic.fct_internal_external_crosswalk` — a denormalized view that takes any external key (customer item number OR EAN/UPC OR GTIN-14 OR SSCC) and resolves it to `(material_matnr, customer_kunnr, sales_org, distribution_channel)`. Built by unioning:
- `dim_customer_material` keyed on `customer_material_number`
- `dim_customer_material` keyed on `ean_consumer`
- `dim_customer_material` keyed on `ean_case`
- `dim_customer_material` keyed on `ean_pallet`
- The GS1 registry for barcodes we know about that aren't in MEAN

The Retail Intelligence Agent's resolution tool (working name `resolve_external_to_internal`) calls this crosswalk view as its first step on any inbound retail data row.

## How this changes `retail_data_gap_v2.md`

The retail data gap document has been revised to spec `fct_retail_dc_inventory` and `fct_retail_store_inventory` (initial v3) with velocity and promo deferred. Specifically, the keying columns at the raw layer should be:

```
-- BEFORE (the naive v2 spec):
customer_kunnr STRING
material_matnr STRING

-- AFTER (what actually arrives in real retailer feeds):
retailer_id          STRING  -- which retailer the feed came from
retailer_item_number STRING  -- the retailer's own item ID, sometimes empty
retailer_upc         STRING  -- the consumer UPC, sometimes used instead
retailer_gtin        STRING  -- the case-level GTIN if shipping data
```

The semantic-layer views (`tiger_semantic.fct_retail_*`) then resolve these into `customer_kunnr + material_matnr` via the crosswalk, presenting the joined result to the agents. The agents see the semantic shape; the resolution is hidden in the view.

This is a cleaner separation: raw retailer data stays faithful to what arrives over EDI / API, and the resolution to internal keys happens in one well-tested place.

## What's needed from the data engineering team

1. **SAP extract pipeline** — KNMT, MARA (slim), MEAN, MVKE landing daily into `tiger_raw.sap_*`. The DDL is in `infra/cmir_raw_sources_v2.sql`. SLT or BigQuery Connector for SAP both work. Daily delta + weekly full.
2. **Initial backfill** — full history of these four tables, especially KNMT, since AEDAT (last changed) on a recent extract can lag the actual KDMAT changes a customer pushed weeks ago.
3. **Coverage check** — once landed, run the verification queries in `cmir_raw_sources_v2.sql` to confirm row counts and EAN coverage per UoM level. KNMT in particular should cover every active `(top-50 customer × top-200 SKU)` combination — gaps here are gaps in our ability to receive retailer feeds.
4. **MEAN sanity** — for every active finished-good MATNR, expect at minimum an EA-level EAN. Case-level GTIN coverage is more variable; pallet-level SSCC is often absent in master data (SSCC is assigned per pallet at packing time, not in master data). Document gaps.

## What's needed from the AI/ML engineering team

Nothing yet on the CMIR work itself — this is data engineering scope. But once it lands:

- The `dim_customer_material` view becomes a new tool the Retail Intelligence Agent can call: `get_customer_material_mapping(customer_kunnr, material_matnr)` returns the customer's item number, all three EANs, and the commercial rules (partial delivery indicator etc.).
- The `partial_delivery_indicator` and `max_partial_deliveries` fields **need to be plumbed into the Customer Supply Agent's PARTIAL_FULFILL decision logic**. Today the agent considers partial fulfillment as universally available; with CMIR loaded, that's no longer true. Some customers/SKUs forbid partial delivery (KZTLF='B') and the agent must respect that constraint.

## Acceptance check after the CMIR landing

```sql
-- Run after extract has landed
SELECT
  COUNT(*)                                        AS total_cmir_rows,
  COUNT(DISTINCT customer_kunnr)                  AS customers,
  COUNT(DISTINCT material_matnr)                  AS materials,
  COUNTIF(customer_material_number IS NOT NULL)   AS rows_with_kdmat,
  COUNTIF(ean_consumer IS NOT NULL)               AS rows_with_consumer_ean,
  COUNTIF(ean_case IS NOT NULL)                   AS rows_with_case_ean,
  COUNTIF(ean_pallet IS NOT NULL)                 AS rows_with_pallet_ean,
  COUNTIF(partial_delivery_indicator = 'B')       AS rows_no_partial_allowed
FROM `resilience-riskradar.tiger_semantic.dim_customer_material`;
```

Expectation: `rows_with_kdmat` should be > 95% of total (a CMIR row without KDMAT defeats the purpose — investigate any gap). `rows_with_consumer_ean` > 90%. `rows_with_case_ean` > 70% (lower coverage is expected — many smaller SKUs don't have separate case GTINs in master data). `rows_no_partial_allowed` > 0 — confirms the partial-delivery rules are being captured (zero would suggest the field isn't being populated by the SAP extract).

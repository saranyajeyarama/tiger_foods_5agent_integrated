-- ============================================================================
-- SAP Customer Material Info Record (CMIR) — raw source tables
-- ============================================================================
-- Lands the four SAP tables that together form the customer-material crosswalk
-- foundation. Schema preserves native SAP column names so the raw layer stays
-- a faithful representation of the source extract — the curated layer
-- (infra/dim_customer_material_v2.sql) is where we rename and join.
--
-- Project: resilience-riskradar
-- Dataset: tiger_raw
-- Tables:  sap_knmt (primary CMIR), sap_mara (material master extract),
--          sap_mean (international article numbers), sap_mvke (sales-org data)
--
-- Source: SAP S/4HANA extract via SLT or BigQuery Connector for SAP.
-- Refresh: daily delta, full refresh weekly (Sunday 02:00 UTC).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. sap_knmt — Customer Material Info Record (primary CMIR table)
-- ----------------------------------------------------------------------------
-- The customer-specific overlay of material information. One row per
-- (sales_org, distribution_channel, customer, material). Carries the
-- customer's own material number (KDMAT) — the single most important field
-- in this whole crosswalk.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `resilience-riskradar.tiger_raw.sap_knmt` (
  -- Keys
  mandt        STRING  OPTIONS (description = 'SAP client code; filtered to production client in extract'),
  vkorg        STRING  OPTIONS (description = 'Sales organization (e.g., US01 for Tiger Foods US)'),
  vtweg        STRING  OPTIONS (description = 'Distribution channel (e.g., 10 = retail, 20 = foodservice)'),
  kunnr        STRING  NOT NULL OPTIONS (description = 'Customer number — sold-to party'),
  matnr        STRING  NOT NULL OPTIONS (description = 'Tiger Foods SAP material number — the internal key'),

  -- The crosswalk payload
  kdmat        STRING  OPTIONS (description = 'Customer material number — the customer\'s own item ID. THIS IS THE PRIMARY EXTERNAL CROSSWALK KEY for retailer data feeds.'),
  postx        STRING  OPTIONS (description = 'Customer-specific material description (e.g., "PEDIGREE ADULT DRY 22LB STORE BAG")'),
  meins        STRING  OPTIONS (description = 'Base unit of measure (EA, CS, KG, LB)'),

  -- Customer-specific commercial rules (override material master defaults)
  lprio        STRING  OPTIONS (description = 'Delivery priority: 01 highest, 10 lowest'),
  ean11        STRING  OPTIONS (description = 'Customer-specific EAN/UPC override at this material level; usually empty (defer to MEAN)'),

  -- Partial delivery rules — drive OTIF logic
  antlf        INT64   OPTIONS (description = 'Maximum number of partial deliveries allowed per order item'),
  kztlf        STRING  OPTIONS (description = 'Partial delivery indicator: A=allow with limits, B=no partial, C=one partial only, D=unlimited'),
  uebto        FLOAT64 OPTIONS (description = 'Overdelivery tolerance percentage (0.0–100.0)'),
  untto        FLOAT64 OPTIONS (description = 'Underdelivery tolerance percentage (0.0–100.0)'),

  -- Other commercial controls
  faksp        STRING  OPTIONS (description = 'Billing block: non-null = billing suspended for this customer-material'),
  abrvw        STRING  OPTIONS (description = 'Usage indicator (industry-specific)'),
  lfrel        STRING  OPTIONS (description = 'Delivery-relevant flag: X = yes, empty = no'),
  berid        STRING  OPTIONS (description = 'MRP area used for this customer-material'),
  prgrs        STRING  OPTIONS (description = 'Date type for scheduling (calendar week, day, month)'),

  -- Customer's order-list / item-proposal references
  vsort        STRING  OPTIONS (description = 'Item proposal / order list reference for this customer-material'),
  wmeng        FLOAT64 OPTIONS (description = 'Target quantity in sales unit'),
  bmeng        FLOAT64 OPTIONS (description = 'Target quantity in base unit'),

  -- Material grouping (5 customer-mappable buckets)
  mvgr1        STRING  OPTIONS (description = 'Material grouping field 1 (free-text customer use)'),
  mvgr2        STRING  OPTIONS (description = 'Material grouping field 2'),
  mvgr3        STRING  OPTIONS (description = 'Material grouping field 3'),
  mvgr4        STRING  OPTIONS (description = 'Material grouping field 4'),
  mvgr5        STRING  OPTIONS (description = 'Material grouping field 5'),

  -- Audit
  erdat        DATE    OPTIONS (description = 'Created on'),
  ernam        STRING  OPTIONS (description = 'Created by (SAP user)'),
  aedat        DATE    OPTIONS (description = 'Last changed on'),
  aenam        STRING  OPTIONS (description = 'Last changed by'),

  -- Extract metadata (not in SAP — added by the loader)
  _ingested_at      TIMESTAMP NOT NULL OPTIONS (description = 'When this row was loaded into tiger_raw'),
  _source_extract   STRING    OPTIONS (description = 'Extract run identifier for lineage'),
  _is_current       BOOL      OPTIONS (description = 'True for the latest version of this composite key')
)
PARTITION BY DATE(_ingested_at)
CLUSTER BY kunnr, matnr
OPTIONS (
  description = 'SAP table KNMT — Customer Material Info Record. Primary CMIR source. Refresh: daily delta + weekly full.',
  labels = [('layer', 'raw'), ('source', 'sap_s4hana'), ('domain', 'master_data')]
);


-- ----------------------------------------------------------------------------
-- 2. sap_mara — Material master (general view, EAN-relevant excerpt)
-- ----------------------------------------------------------------------------
-- The full MARA table has ~200 columns. We pull only what we need for the
-- crosswalk and master data: identification, default EAN, unit, weight, audit.
-- The semantic dim_material view already lives in tiger_semantic, but it was
-- built against a different SAP extract; this raw table is the new
-- ground truth for the crosswalk.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `resilience-riskradar.tiger_raw.sap_mara` (
  mandt        STRING,
  matnr        STRING  NOT NULL OPTIONS (description = 'SAP material number'),

  -- Identification
  mtart        STRING  OPTIONS (description = 'Material type (FERT=finished good, ROH=raw material, HALB=semi-finished, HAWA=trading good)'),
  matkl        STRING  OPTIONS (description = 'Material group (Tiger Foods uses 4-char codes like DRY_DOG, WET_CAT, TREATS)'),
  meins        STRING  OPTIONS (description = 'Base unit of measure'),

  -- Default barcode at base unit (overridden by MEAN if multi-UoM)
  ean11        STRING  OPTIONS (description = 'Default EAN/UPC at base unit. For multi-UoM products (case + each + pallet), the full EAN set lives in sap_mean.'),
  numtp        STRING  OPTIONS (description = 'EAN category for ean11: UC=UPC-A (12-digit), HE=EAN-13, IA=GTIN-14, IB=SSCC-18'),

  -- Physical
  brgew        FLOAT64 OPTIONS (description = 'Gross weight'),
  ntgew        FLOAT64 OPTIONS (description = 'Net weight'),
  gewei        STRING  OPTIONS (description = 'Weight unit (LB, KG)'),
  volum        FLOAT64 OPTIONS (description = 'Volume'),
  voleh        STRING  OPTIONS (description = 'Volume unit'),

  -- Lifecycle / status
  mstae        STRING  OPTIONS (description = 'Plant-level material status (cross-plant)'),
  lvorm        STRING  OPTIONS (description = 'Deletion flag at client level'),

  -- Shelf life
  mhdrz        INT64   OPTIONS (description = 'Minimum remaining shelf life on receipt (days)'),
  mhdhb        INT64   OPTIONS (description = 'Total shelf life (days)'),
  iprkz        STRING  OPTIONS (description = 'Period indicator for shelf life: D=days, M=months'),

  -- Audit
  erdat        DATE,
  ernam        STRING,
  laeda        DATE    OPTIONS (description = 'Last general change date'),
  aenam        STRING,

  _ingested_at      TIMESTAMP NOT NULL,
  _source_extract   STRING,
  _is_current       BOOL
)
PARTITION BY DATE(_ingested_at)
CLUSTER BY matnr
OPTIONS (
  description = 'SAP table MARA — material master (general view), EAN-relevant excerpt. Refresh: daily delta + weekly full.',
  labels = [('layer', 'raw'), ('source', 'sap_s4hana'), ('domain', 'master_data')]
);


-- ----------------------------------------------------------------------------
-- 3. sap_mean — International article numbers (multiple EANs per material)
-- ----------------------------------------------------------------------------
-- This is where the per-unit-of-measure EANs live. A 22lb bag of Pedigree
-- typically has THREE codes: the consumer-facing UPC on the bag (EA level),
-- the case-level GTIN-14 (CS level), and the pallet SSCC (PAL level).
-- Retailer POS scans the EA code at the checkout; retailer DC receiving scans
-- the CS code; warehouse moves scan the PAL code. All three need crosswalk.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `resilience-riskradar.tiger_raw.sap_mean` (
  mandt        STRING,
  matnr        STRING  NOT NULL,
  meinh        STRING  NOT NULL OPTIONS (description = 'Alternative unit of measure: EA, CS, PAL, LAYER'),
  lfnum        INT64   OPTIONS (description = 'Sequence number when multiple EANs exist for the same matnr+meinh'),

  -- The barcode
  ean11        STRING  OPTIONS (description = 'The EAN/UPC/GTIN/SSCC barcode itself'),
  eantp        STRING  OPTIONS (description = 'Barcode category: UC=UPC-A 12-digit, HE=EAN-13, IA=GTIN-14, IB=SSCC-18'),
  hpean        STRING  OPTIONS (description = 'Main EAN flag: X = primary code for this matnr+meinh combination'),

  -- Audit
  erdat        DATE,
  aedat        DATE,

  _ingested_at      TIMESTAMP NOT NULL,
  _source_extract   STRING,
  _is_current       BOOL
)
PARTITION BY DATE(_ingested_at)
CLUSTER BY matnr, meinh
OPTIONS (
  description = 'SAP table MEAN — international article numbers (multiple EAN/UPC/GTIN per material per UoM). Refresh: daily delta + weekly full.',
  labels = [('layer', 'raw'), ('source', 'sap_s4hana'), ('domain', 'master_data')]
);


-- ----------------------------------------------------------------------------
-- 4. sap_mvke — Material master, sales-organization-specific data
-- ----------------------------------------------------------------------------
-- Sales-org-specific overrides on top of MARA: per (matnr, vkorg, vtweg).
-- Drives minimum order quantities, sales status, delivery time at the
-- sales-channel level. Tiger Foods uses different MVKE rows for the same
-- MATNR sold through retail vs foodservice channels.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `resilience-riskradar.tiger_raw.sap_mvke` (
  mandt        STRING,
  matnr        STRING  NOT NULL,
  vkorg        STRING  NOT NULL,
  vtweg        STRING  NOT NULL,

  -- Sales unit and rules
  vrkme        STRING  OPTIONS (description = 'Sales unit of measure (often CS for retail)'),
  lfmng        FLOAT64 OPTIONS (description = 'Minimum delivery quantity in sales unit'),
  efmng        FLOAT64 OPTIONS (description = 'Minimum order quantity in sales unit'),
  aumng        FLOAT64 OPTIONS (description = 'Minimum make-to-order quantity'),
  schtv        INT64   OPTIONS (description = 'Delivery time in days'),

  -- Status
  vmsta        STRING  OPTIONS (description = 'Sales material status: 01=active, 02=blocked for sales, 04=blocked for orders only'),
  vmstd        DATE    OPTIONS (description = 'Sales status valid from'),

  -- Pricing/grouping refs
  ktgrm        STRING  OPTIONS (description = 'Account assignment group'),
  mvgr1        STRING  OPTIONS (description = 'Material group 1 at sales-org level'),
  mvgr2        STRING,
  mvgr3        STRING,
  mvgr4        STRING,
  mvgr5        STRING,

  -- Audit
  erdat        DATE,
  ernam        STRING,
  laeda        DATE,
  aenam        STRING,

  _ingested_at      TIMESTAMP NOT NULL,
  _source_extract   STRING,
  _is_current       BOOL
)
PARTITION BY DATE(_ingested_at)
CLUSTER BY matnr, vkorg
OPTIONS (
  description = 'SAP table MVKE — material master sales-org-specific data. Refresh: daily delta + weekly full.',
  labels = [('layer', 'raw'), ('source', 'sap_s4hana'), ('domain', 'master_data')]
);


-- ============================================================================
-- Verification queries (run after extract lands)
-- ============================================================================

-- Confirm KNMT row count and key uniqueness
-- SELECT COUNT(*) AS row_count,
--        COUNT(DISTINCT FORMAT('%s|%s|%s|%s|%s', mandt, vkorg, vtweg, kunnr, matnr)) AS distinct_key
-- FROM `resilience-riskradar.tiger_raw.sap_knmt`
-- WHERE _is_current = TRUE;

-- Confirm coverage: how many CMIR rows exist per top customer
-- SELECT kunnr, COUNT(*) AS material_count
-- FROM `resilience-riskradar.tiger_raw.sap_knmt`
-- WHERE _is_current = TRUE
-- GROUP BY kunnr
-- ORDER BY material_count DESC
-- LIMIT 20;

-- Confirm EAN coverage per material at each UoM level (EA, CS, PAL)
-- SELECT matnr, meinh, COUNT(*) AS ean_count,
--        COUNTIF(hpean = 'X') AS primary_ean_count
-- FROM `resilience-riskradar.tiger_raw.sap_mean`
-- WHERE _is_current = TRUE
-- GROUP BY matnr, meinh;


-- ============================================================================
-- Sample seed data (illustrative — Walmart Pedigree Dry 22lb scenario)
-- ============================================================================
-- Demonstrates the multi-table relationship. Uncomment to load test rows.
--
-- INSERT INTO `resilience-riskradar.tiger_raw.sap_knmt`
-- (mandt, vkorg, vtweg, kunnr, matnr, kdmat, postx, meins, lprio,
--  antlf, kztlf, uebto, untto, lfrel, _ingested_at, _is_current)
-- VALUES
--   ('100', 'US01', '10', '0001000245', 'MAT-PDG-DOG-DRY-22LB',
--    '552847391', 'PEDIGREE ADULT DRY 22LB STORE BAG', 'CS', '01',
--    1, 'B', 0.0, 5.0, 'X', CURRENT_TIMESTAMP(), TRUE),
--   ('100', 'US01', '10', '0001000245', 'MAT-PDG-DOG-DRY-15LB',
--    '552847392', 'PEDIGREE ADULT DRY 15LB STORE BAG', 'CS', '01',
--    1, 'B', 0.0, 5.0, 'X', CURRENT_TIMESTAMP(), TRUE),
--   ('100', 'US01', '10', '0001000312', 'MAT-PDG-DOG-DRY-22LB',
--    'KRO-DG-PD-22', 'PEDIGREE 22LB BAG ADULT DOG', 'CS', '02',
--    2, 'A', 5.0, 10.0, 'X', CURRENT_TIMESTAMP(), TRUE);
--
-- INSERT INTO `resilience-riskradar.tiger_raw.sap_mean`
-- (mandt, matnr, meinh, lfnum, ean11, eantp, hpean, _ingested_at, _is_current)
-- VALUES
--   ('100', 'MAT-PDG-DOG-DRY-22LB', 'EA', 1, '023100013770', 'UC', 'X', CURRENT_TIMESTAMP(), TRUE),
--   ('100', 'MAT-PDG-DOG-DRY-22LB', 'CS', 1, '10023100013777', 'IA', 'X', CURRENT_TIMESTAMP(), TRUE),
--   ('100', 'MAT-PDG-DOG-DRY-22LB', 'PAL', 1, '00310000231000137708', 'IB', 'X', CURRENT_TIMESTAMP(), TRUE);

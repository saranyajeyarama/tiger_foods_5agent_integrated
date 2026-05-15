-- ============================================================================
-- dim_customer_material — semantic-layer CMIR view
-- ============================================================================
-- Joins the four raw SAP tables (sap_knmt, sap_mara, sap_mean, sap_mvke) into
-- a single agent-readable record per (customer, material) combination, with
-- the three EAN levels (consumer / case / pallet) pivoted out as columns and
-- SAP's obscure four-letter names renamed to something humans can read.
--
-- This view is the spine of the upcoming internal-external crosswalk: it
-- exposes the customer's own material number (customer_material_number,
-- formerly KDMAT) alongside Tiger Foods' internal MATNR, plus all three
-- barcode levels so external retailer feeds can resolve back to MATNR by
-- ANY of: customer_material_number, ean_consumer, ean_case, or ean_pallet.
--
-- Project: resilience-riskradar
-- Dataset: tiger_semantic
-- View:    dim_customer_material
--
-- Refresh: BigQuery view (always current; reflects whatever is in tiger_raw).
-- ============================================================================

CREATE OR REPLACE VIEW `resilience-riskradar.tiger_semantic.dim_customer_material` AS
WITH
-- Primary CMIR record, current version only
knmt AS (
  SELECT
    vkorg                 AS sales_org,
    vtweg                 AS distribution_channel,
    kunnr                 AS customer_kunnr,
    matnr                 AS material_matnr,
    kdmat                 AS customer_material_number,
    postx                 AS customer_material_description,
    meins                 AS base_unit_of_measure,
    lprio                 AS delivery_priority,
    antlf                 AS max_partial_deliveries,
    kztlf                 AS partial_delivery_indicator,
    uebto                 AS overdelivery_tolerance_pct,
    untto                 AS underdelivery_tolerance_pct,
    faksp IS NOT NULL     AS billing_block_active,
    lfrel = 'X'           AS is_delivery_relevant,
    berid                 AS mrp_area,
    erdat                 AS cmir_created_date,
    aedat                 AS cmir_last_changed_date
  FROM `resilience-riskradar.tiger_raw.sap_knmt`
  WHERE _is_current = TRUE
),

-- Material master enrichment (default EAN + shelf life + status)
mara AS (
  SELECT
    matnr                 AS material_matnr,
    mtart                 AS material_type,
    matkl                 AS material_group,
    meins                 AS material_base_unit,
    mhdrz                 AS minimum_remaining_shelf_life_days,
    mhdhb                 AS total_shelf_life_days,
    ean11                 AS material_master_ean,
    brgew                 AS gross_weight,
    ntgew                 AS net_weight,
    gewei                 AS weight_unit,
    mstae                 AS material_status,
    lvorm = 'X'           AS is_deletion_flagged
  FROM `resilience-riskradar.tiger_raw.sap_mara`
  WHERE _is_current = TRUE
),

-- EAN/UPC/GTIN at each unit-of-measure level. We pivot the three common
-- levels (EA = consumer, CS = case, PAL = pallet) into columns and prefer
-- the row marked hpean = 'X' (primary code) when multiple exist.
mean_ea AS (
  SELECT matnr AS material_matnr, ean11 AS ean_consumer, eantp AS ean_consumer_type
  FROM `resilience-riskradar.tiger_raw.sap_mean`
  WHERE _is_current = TRUE AND meinh = 'EA' AND hpean = 'X'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY matnr ORDER BY lfnum) = 1
),
mean_cs AS (
  SELECT matnr AS material_matnr, ean11 AS ean_case, eantp AS ean_case_type
  FROM `resilience-riskradar.tiger_raw.sap_mean`
  WHERE _is_current = TRUE AND meinh = 'CS' AND hpean = 'X'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY matnr ORDER BY lfnum) = 1
),
mean_pal AS (
  SELECT matnr AS material_matnr, ean11 AS ean_pallet, eantp AS ean_pallet_type
  FROM `resilience-riskradar.tiger_raw.sap_mean`
  WHERE _is_current = TRUE AND meinh = 'PAL' AND hpean = 'X'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY matnr ORDER BY lfnum) = 1
),

-- Sales-org overrides (minimum order qty, sales status)
mvke AS (
  SELECT
    matnr                 AS material_matnr,
    vkorg                 AS sales_org,
    vtweg                 AS distribution_channel,
    vrkme                 AS sales_unit_of_measure,
    lfmng                 AS minimum_delivery_qty,
    efmng                 AS minimum_order_qty,
    schtv                 AS sales_org_delivery_time_days,
    vmsta                 AS sales_material_status,
    CASE vmsta
      WHEN '01' THEN 'ACTIVE'
      WHEN '02' THEN 'BLOCKED_FOR_SALES'
      WHEN '04' THEN 'BLOCKED_FOR_ORDERS'
      ELSE 'UNKNOWN'
    END                   AS sales_material_status_label
  FROM `resilience-riskradar.tiger_raw.sap_mvke`
  WHERE _is_current = TRUE
)

-- The semantic record: one row per (sales_org, dist_channel, customer, material)
SELECT
  -- ----- Composite key -----
  knmt.sales_org,
  knmt.distribution_channel,
  knmt.customer_kunnr,
  knmt.material_matnr,

  -- ----- The crosswalk payload (this is what makes this view valuable) -----
  knmt.customer_material_number,            -- KDMAT: customer's own item ID
  knmt.customer_material_description,       -- POSTX: customer's description
  COALESCE(mean_ea.ean_consumer, mara.material_master_ean) AS ean_consumer,
  mean_ea.ean_consumer_type,
  mean_cs.ean_case,
  mean_cs.ean_case_type,
  mean_pal.ean_pallet,
  mean_pal.ean_pallet_type,

  -- ----- Tiger Foods material master attributes -----
  mara.material_type,
  mara.material_group,
  mara.material_base_unit,
  mara.minimum_remaining_shelf_life_days,
  mara.total_shelf_life_days,
  mara.gross_weight,
  mara.net_weight,
  mara.weight_unit,
  mara.material_status,
  mara.is_deletion_flagged,

  -- ----- Customer-specific commercial rules -----
  knmt.base_unit_of_measure,
  knmt.delivery_priority,
  knmt.max_partial_deliveries,
  knmt.partial_delivery_indicator,
  knmt.overdelivery_tolerance_pct,
  knmt.underdelivery_tolerance_pct,
  knmt.billing_block_active,
  knmt.is_delivery_relevant,
  knmt.mrp_area,

  -- ----- Sales-org overrides -----
  mvke.sales_unit_of_measure,
  mvke.minimum_delivery_qty,
  mvke.minimum_order_qty,
  mvke.sales_org_delivery_time_days,
  mvke.sales_material_status_label,

  -- ----- Audit / lineage -----
  knmt.cmir_created_date,
  knmt.cmir_last_changed_date,
  CURRENT_TIMESTAMP() AS _view_resolved_at

FROM      knmt
LEFT JOIN mara     USING (material_matnr)
LEFT JOIN mean_ea  USING (material_matnr)
LEFT JOIN mean_cs  USING (material_matnr)
LEFT JOIN mean_pal USING (material_matnr)
LEFT JOIN mvke     USING (material_matnr, sales_org, distribution_channel);


-- ============================================================================
-- Verification — sanity-check the view after creation
-- ============================================================================

-- SELECT COUNT(*) AS rows,
--        COUNT(DISTINCT customer_kunnr) AS customers,
--        COUNT(DISTINCT material_matnr) AS materials,
--        COUNTIF(customer_material_number IS NOT NULL) AS rows_with_kdmat,
--        COUNTIF(ean_consumer IS NOT NULL) AS rows_with_consumer_ean,
--        COUNTIF(ean_case IS NOT NULL) AS rows_with_case_ean
-- FROM `resilience-riskradar.tiger_semantic.dim_customer_material`;

-- Inspect the Walmart Pedigree mapping
-- SELECT customer_kunnr, material_matnr, customer_material_number,
--        customer_material_description, ean_consumer, ean_case, ean_pallet
-- FROM `resilience-riskradar.tiger_semantic.dim_customer_material`
-- WHERE customer_kunnr = '0001000245'
--   AND material_matnr = 'MAT-PDG-DOG-DRY-22LB';

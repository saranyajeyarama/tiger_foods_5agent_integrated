-- ============================================================================
-- External product crosswalk — view + resolution TVF
-- ============================================================================
-- Resolves external retailer keys (retailer item number, EAN/UPC) to Tiger
-- Foods MATNR for archetypes A, B, C of the CDM product mapping framework:
--
--   A:  Retailer, Retailer Item #, UoM
--   B:  Retailer, EAN/UPC,         UoM
--   C:  Retailer, Retailer Item #, EAN/UPC, UoM
--
-- Built on top of dim_customer_material (the CMIR semantic view), which
-- already joins KNMT + MARA + MEAN + MVKE.
--
-- Scope: US sold-to KUNNRs only. Country dimension deliberately omitted —
-- can be added via ALTER VIEW with a single column addition when needed.
--
-- Project: resilience-riskradar
-- Dataset: tiger_semantic
-- Objects: dim_external_product_crosswalk (view)
--          resolve_external_to_internal     (table-valued function)
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. dim_external_product_crosswalk — denormalized resolution view
-- ----------------------------------------------------------------------------
-- One row per (customer_kunnr, material_matnr, uom, ean_upc). The
-- retailer_item_number is an attribute on the row — same KDMAT value
-- repeats across UoM rows for the same customer-material pair, because
-- KNMT.KDMAT is not UoM-specific. Each UoM has its own EAN row.
--
-- The archetype is a query-shape concept, not a row property. Each row
-- can serve queries for any archetype whose required keys are present
-- on that row — encoded via the supports_archetype_* flags.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW `resilience-riskradar.tiger_semantic.dim_external_product_crosswalk` AS
WITH base AS (
  -- Pivot ean_consumer/ean_case/ean_pallet from dim_customer_material into
  -- one row per UoM, so each row represents one externally-keyable instance.
  SELECT
    cm.customer_kunnr,
    cm.material_matnr,
    cm.customer_material_number               AS retailer_item_number,
    cm.customer_material_description          AS retailer_item_description,
    cm.sales_org,
    cm.distribution_channel,
    'EA'                                       AS uom,
    cm.ean_consumer                            AS ean_upc,
    cm.ean_consumer_type                       AS ean_upc_type,
    cm.material_status IS NOT NULL
      AND NOT cm.is_deletion_flagged           AS is_active
  FROM `resilience-riskradar.tiger_semantic.dim_customer_material` cm
  WHERE cm.ean_consumer IS NOT NULL OR cm.customer_material_number IS NOT NULL

  UNION ALL

  SELECT
    cm.customer_kunnr,
    cm.material_matnr,
    cm.customer_material_number,
    cm.customer_material_description,
    cm.sales_org,
    cm.distribution_channel,
    'CS'                                       AS uom,
    cm.ean_case                                AS ean_upc,
    cm.ean_case_type                           AS ean_upc_type,
    cm.material_status IS NOT NULL
      AND NOT cm.is_deletion_flagged           AS is_active
  FROM `resilience-riskradar.tiger_semantic.dim_customer_material` cm
  WHERE cm.ean_case IS NOT NULL OR cm.customer_material_number IS NOT NULL

  UNION ALL

  SELECT
    cm.customer_kunnr,
    cm.material_matnr,
    cm.customer_material_number,
    cm.customer_material_description,
    cm.sales_org,
    cm.distribution_channel,
    'PAL'                                      AS uom,
    cm.ean_pallet                              AS ean_upc,
    cm.ean_pallet_type                         AS ean_upc_type,
    cm.material_status IS NOT NULL
      AND NOT cm.is_deletion_flagged           AS is_active
  FROM `resilience-riskradar.tiger_semantic.dim_customer_material` cm
  WHERE cm.ean_pallet IS NOT NULL
)
SELECT
  customer_kunnr,
  material_matnr,
  retailer_item_number,
  retailer_item_description,
  ean_upc,
  ean_upc_type,
  uom,
  sales_org,
  distribution_channel,

  -- Archetype support flags — TRUE iff this row carries the keys needed
  -- to answer a query in that archetype shape.
  retailer_item_number IS NOT NULL                              AS supports_archetype_a,
  ean_upc IS NOT NULL                                           AS supports_archetype_b,
  retailer_item_number IS NOT NULL AND ean_upc IS NOT NULL      AS supports_archetype_c,

  is_active,
  CURRENT_TIMESTAMP()                                            AS _view_resolved_at

FROM base
WHERE customer_kunnr IS NOT NULL
  AND material_matnr IS NOT NULL;


-- ----------------------------------------------------------------------------
-- 2. resolve_external_to_internal — table-valued function
-- ----------------------------------------------------------------------------
-- Takes the archetype-shaped input (customer + any combination of retailer
-- item number and/or EAN/UPC + UoM) and returns the matched MATNR with
-- resolution provenance.
--
-- Resolution priority: C > A > B
--   - If both keys are provided AND both match on the same row → archetype C match
--   - If only retailer item number is provided → archetype A match
--   - If only EAN/UPC is provided                → archetype B match
--   - If both keys provided but they disagree    → prefer the retailer item number
--     (retailer's own ID is more reliable than barcode, which can be shared
--     across SKUs in degenerate cases like private-label co-pack)
--
-- Usage:
--   SELECT * FROM `tiger_semantic.resolve_external_to_internal`(
--     '0001000245',    -- customer_kunnr (Walmart)
--     '552847391',     -- retailer_item_number (NULL if not provided)
--     NULL,            -- ean_upc (NULL if not provided)
--     'CS'             -- uom
--   );
-- ----------------------------------------------------------------------------

CREATE OR REPLACE TABLE FUNCTION `resilience-riskradar.tiger_semantic.resolve_external_to_internal`(
  in_customer_kunnr        STRING,
  in_retailer_item_number  STRING,
  in_ean_upc               STRING,
  in_uom                   STRING
) AS (
  WITH candidates AS (
    SELECT
      x.customer_kunnr,
      x.material_matnr,
      x.retailer_item_number,
      x.ean_upc,
      x.uom,
      x.retailer_item_description,
      x.is_active,
      -- Score this row against the input: higher score = better match
      CASE
        WHEN in_retailer_item_number IS NOT NULL
             AND in_ean_upc IS NOT NULL
             AND x.retailer_item_number = in_retailer_item_number
             AND x.ean_upc = in_ean_upc                              THEN 100  -- archetype C
        WHEN in_retailer_item_number IS NOT NULL
             AND x.retailer_item_number = in_retailer_item_number    THEN 80   -- archetype A
        WHEN in_ean_upc IS NOT NULL
             AND x.ean_upc = in_ean_upc                              THEN 60   -- archetype B
        ELSE 0
      END                                                              AS match_score,
      CASE
        WHEN in_retailer_item_number IS NOT NULL
             AND in_ean_upc IS NOT NULL
             AND x.retailer_item_number = in_retailer_item_number
             AND x.ean_upc = in_ean_upc                              THEN 'C'
        WHEN in_retailer_item_number IS NOT NULL
             AND x.retailer_item_number = in_retailer_item_number    THEN 'A'
        WHEN in_ean_upc IS NOT NULL
             AND x.ean_upc = in_ean_upc                              THEN 'B'
        ELSE NULL
      END                                                              AS matched_archetype
    FROM `resilience-riskradar.tiger_semantic.dim_external_product_crosswalk` x
    WHERE x.customer_kunnr = in_customer_kunnr
      AND x.uom            = in_uom
      AND x.is_active
  )
  SELECT
    customer_kunnr,
    material_matnr,
    retailer_item_number       AS resolved_retailer_item_number,
    ean_upc                    AS resolved_ean_upc,
    uom,
    retailer_item_description,
    matched_archetype,
    match_score,
    CURRENT_TIMESTAMP()         AS _resolved_at
  FROM candidates
  WHERE match_score > 0
  ORDER BY match_score DESC
  LIMIT 1
);


-- ============================================================================
-- Verification queries
-- ============================================================================

-- Confirm crosswalk row counts by UoM
-- SELECT uom, COUNT(*) AS rows, COUNTIF(supports_archetype_a) AS arch_a,
--        COUNTIF(supports_archetype_b) AS arch_b, COUNTIF(supports_archetype_c) AS arch_c
-- FROM `resilience-riskradar.tiger_semantic.dim_external_product_crosswalk`
-- GROUP BY uom ORDER BY uom;

-- Resolution test: Walmart Pedigree by retailer item number only (archetype A)
-- SELECT * FROM `resilience-riskradar.tiger_semantic.resolve_external_to_internal`(
--   '0001000245', '552847391', NULL, 'CS');

-- Resolution test: Walmart Pedigree by case UPC only (archetype B)
-- SELECT * FROM `resilience-riskradar.tiger_semantic.resolve_external_to_internal`(
--   '0001000245', NULL, '10023100013777', 'CS');

-- Resolution test: Walmart Pedigree with both keys (archetype C)
-- SELECT * FROM `resilience-riskradar.tiger_semantic.resolve_external_to_internal`(
--   '0001000245', '552847391', '10023100013777', 'CS');

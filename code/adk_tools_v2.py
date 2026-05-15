"""
ADK tool definitions for the v2 5-agent build.

Pattern: reuse the v1 tools where they apply (get_otif_performance,
get_chargeback_risk, get_transfer_cost_comparison, get_allocation_history,
get_active_alerts, get_shelf_life_risk). Add new tools for the 5-agent
flow. Retail-signals tools return data_available=false stubs for v2 —
v3 lights them up when fct_retail_dc_inventory and fct_retail_store_inventory
land. Velocity / promo stubs remain deferred beyond initial v3.

CDM domain map (which CDM domain each tool reads from):

  Sales & Orders      → get_open_sales_orders, get_otif_performance,
                         get_order_history, classify_order_vs_forecast,
                         get_chargeback_risk
  Inventory           → get_finished_goods_inventory, get_shelf_life_risk,
                         get_safety_stock_position
  Supply & Production → get_production_orders
  Procurement         → get_procurement_orders, get_raw_materials_status
  Master Data         → get_customer_compliance_rules
  Retail Signals      → get_retail_dc_inventory (v3),
                         get_retail_store_inventory (v3),
                         get_retail_velocity (v3-deferred),
                         get_promotional_calendar (v3-deferred)
  Logistics           → get_lane_capacity, get_carrier_otp,
                         get_transfer_cost_comparison

Reuses _run_query and the BigQuery client from adk_tools (v1).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Literal, Optional

from google.adk.tools import FunctionTool
from google.cloud import bigquery

# Reuse the v1 client and chokepoint — same project, same dataset boundaries.
from adk_tools import (
    _bq, _run_query, PROJECT_ID, SEMANTIC_DS, DECISIONS_DS,
    get_otif_performance,
    get_chargeback_risk,
    get_transfer_cost_comparison,
    get_allocation_history,
    get_active_alerts,
    get_shelf_life_risk,
)


# ---------------------------------------------------------------------------
# Sales & Orders — new tools
# ---------------------------------------------------------------------------
def get_open_sales_orders(
    customer_kunnr: Optional[str] = None,
    material_matnr: Optional[str] = None,
    horizon_days: int = 30,
) -> dict:
    """Open sales orders for a customer × SKU within a forward horizon."""
    where = ["status IN ('OPEN', 'CONFIRMED')",
             "requested_delivery_date <= DATE_ADD(CURRENT_DATE(), "
             "INTERVAL @days DAY)"]
    params = [bigquery.ScalarQueryParameter("days", "INT64", horizon_days)]
    if customer_kunnr:
        where.append("customer_kunnr = @kunnr")
        params.append(bigquery.ScalarQueryParameter(
            "kunnr", "STRING", customer_kunnr))
    if material_matnr:
        where.append("material_matnr = @matnr")
        params.append(bigquery.ScalarQueryParameter(
            "matnr", "STRING", material_matnr))

    sql = f"""
      SELECT order_id, customer_kunnr, material_matnr,
             confirmed_qty_cs, requested_delivery_date, status,
             origin_plant
      FROM `{SEMANTIC_DS}.fct_sales_orders`
      WHERE {' AND '.join(where)}
      ORDER BY requested_delivery_date ASC
      LIMIT 100
    """
    rows = _run_query(sql, params)
    return {"rows": rows,
            "view_queried": "tiger_semantic.fct_sales_orders",
            "row_count": len(rows)}


def get_order_history(
    customer_kunnr: str,
    material_matnr: str,
    lookback_weeks: int = 12,
) -> dict:
    """Historical order pattern for a customer × SKU. Used by Demand
    Planning Agent for above-forecast classification."""
    params = [
        bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr),
        bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr),
        bigquery.ScalarQueryParameter("weeks", "INT64", lookback_weeks),
    ]
    sql = f"""
      SELECT
        FORMAT_DATE('%G-W%V', order_date) AS iso_week,
        SUM(ordered_qty_cs) AS ordered_qty_cs
      FROM `{SEMANTIC_DS}.fct_sales_orders`
      WHERE customer_kunnr = @kunnr
        AND material_matnr = @matnr
        AND order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL @weeks WEEK)
      GROUP BY iso_week
      ORDER BY iso_week DESC
    """
    rows = _run_query(sql, params)
    return {"rows": rows,
            "view_queried": "tiger_semantic.fct_sales_orders",
            "row_count": len(rows)}


def classify_order_vs_forecast(
    customer_kunnr: str,
    material_matnr: str,
    week: Optional[str] = None,
) -> dict:
    """Compares an ordered quantity to the demand plan for the same
    customer × SKU × week. Returns plan number and above_forecast flag.
    Used by Demand Planning Agent and Customer Supply Agent.

    `week` is an ISO week string like '2026-W21'; if None uses current week.
    """
    params = [
        bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr),
        bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr),
    ]
    week_filter = ""
    if week:
        week_filter = "AND FORMAT_DATE('%G-W%V', forecast_week) = @week"
        params.append(bigquery.ScalarQueryParameter("week", "STRING", week))
    else:
        week_filter = ("AND forecast_week = "
                        "DATE_TRUNC(CURRENT_DATE(), WEEK)")

    sql = f"""
      WITH plan AS (
        SELECT
          customer_kunnr, material_matnr,
          SUM(forecast_qty_cs) AS demand_plan_qty_cs
        FROM `{SEMANTIC_DS}.fct_forecast_accuracy`
        WHERE customer_kunnr = @kunnr
          AND material_matnr = @matnr
          {week_filter}
        GROUP BY customer_kunnr, material_matnr
      ),
      actual AS (
        SELECT
          customer_kunnr, material_matnr,
          SUM(ordered_qty_cs) AS actual_qty_cs
        FROM `{SEMANTIC_DS}.fct_sales_orders`
        WHERE customer_kunnr = @kunnr
          AND material_matnr = @matnr
          AND order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
        GROUP BY customer_kunnr, material_matnr
      )
      SELECT
        COALESCE(p.demand_plan_qty_cs, 0) AS demand_plan_qty_cs,
        COALESCE(a.actual_qty_cs, 0)       AS actual_qty_cs,
        SAFE_DIVIDE(a.actual_qty_cs - p.demand_plan_qty_cs,
                    p.demand_plan_qty_cs)  AS above_forecast_pct,
        a.actual_qty_cs > p.demand_plan_qty_cs * 1.10
          AS is_above_forecast
      FROM plan p
      FULL OUTER JOIN actual a USING (customer_kunnr, material_matnr)
    """
    rows = _run_query(sql, params)
    if not rows:
        return {"error": "No plan or actual data found",
                "view_queried":
                  "tiger_semantic.fct_forecast_accuracy + fct_sales_orders"}
    r = rows[0]
    r["view_queried"] = ("tiger_semantic.fct_forecast_accuracy + "
                         "fct_sales_orders")
    return r


# ---------------------------------------------------------------------------
# Inventory — extends v1 with explicit FG inventory lookup
# ---------------------------------------------------------------------------
def get_finished_goods_inventory(
    material_matnr: str,
    plant: Optional[str] = None,
    include_shelf_life: bool = True,
) -> dict:
    """Current FG position for a SKU, optionally filtered by plant.
    Aliased to the v1 inventory tool with FG-specific signature for
    the Supply Planning Agent's mental model."""
    where = ["movement_date = CURRENT_DATE()", "material_matnr = @matnr"]
    params = [bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr)]
    if plant:
        where.append("plant = @plant")
        params.append(bigquery.ScalarQueryParameter("plant", "STRING", plant))

    shelf_select = """,
      earliest_expiry_date AS earliest_expiry,
      DATE_DIFF(earliest_expiry_date, CURRENT_DATE(), DAY) AS days_to_expiry
    """ if include_shelf_life else ""

    sql = f"""
      SELECT plant, plant_name,
             qty_on_hand_cs,
             qty_committed_cs,
             qty_on_hand_cs - qty_committed_cs AS qty_available_cs
             {shelf_select}
      FROM `{SEMANTIC_DS}.fct_inventory_movements`
      WHERE {' AND '.join(where)}
      ORDER BY qty_available_cs DESC
    """
    rows = _run_query(sql, params)
    return {"rows": rows,
            "view_queried": "tiger_semantic.fct_inventory_movements",
            "row_count": len(rows)}


def get_safety_stock_position(
    material_matnr: str,
    plant: Optional[str] = None,
) -> dict:
    """Safety stock vs target days-of-cover."""
    where = ["material_matnr = @matnr"]
    params = [bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr)]
    if plant:
        where.append("plant = @plant")
        params.append(bigquery.ScalarQueryParameter("plant", "STRING", plant))
    sql = f"""
      SELECT plant, material_matnr,
             current_days_of_cover, target_days_of_cover,
             current_days_of_cover < target_days_of_cover AS below_target
      FROM `{SEMANTIC_DS}.agg_capacity_utilization`
      WHERE {' AND '.join(where)}
        AND snapshot_date = CURRENT_DATE()
      LIMIT 50
    """
    rows = _run_query(sql, params)
    return {"rows": rows,
            "view_queried": "tiger_semantic.agg_capacity_utilization",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# Supply & Production
# ---------------------------------------------------------------------------
def get_production_orders(
    material_matnr: str,
    horizon_days: int = 14,
    status_filter: Optional[str] = None,
) -> dict:
    """Upcoming production orders for a SKU within a horizon."""
    where = ["material_matnr = @matnr",
             "scheduled_completion_date <= DATE_ADD(CURRENT_DATE(), "
             "INTERVAL @days DAY)"]
    params = [
        bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr),
        bigquery.ScalarQueryParameter("days", "INT64", horizon_days),
    ]
    if status_filter:
        where.append("status = @status")
        params.append(bigquery.ScalarQueryParameter(
            "status", "STRING", status_filter))
    sql = f"""
      SELECT production_order_id, plant, material_matnr,
             planned_qty_cs, scheduled_completion_date,
             status, hold_reason
      FROM `{SEMANTIC_DS}.fct_production_orders`
      WHERE {' AND '.join(where)}
      ORDER BY scheduled_completion_date ASC
      LIMIT 50
    """
    rows = _run_query(sql, params)
    return {"rows": rows,
            "view_queried": "tiger_semantic.fct_production_orders",
            "row_count": len(rows)}


def get_raw_materials_status(
    material_matnr: str,
) -> dict:
    """Directional RM signal for a FG SKU. Many-to-many — no lot linkage.
    Returns whether RM lots needed for upcoming production runs are
    adequate."""
    params = [bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr)]
    sql = f"""
      WITH bom AS (
        SELECT DISTINCT rm_matnr
        FROM `{SEMANTIC_DS}.dim_material`
        WHERE material_matnr = @matnr
          AND component_type = 'RM'
      ),
      rm_inv AS (
        SELECT i.material_matnr AS rm_matnr,
               SUM(i.qty_on_hand_cs) AS rm_on_hand_units,
               MIN(i.earliest_expiry_date) AS earliest_rm_expiry
        FROM `{SEMANTIC_DS}.fct_inventory_movements` i
        WHERE i.movement_date = CURRENT_DATE()
          AND i.material_type = 'RM'
        GROUP BY rm_matnr
      )
      SELECT bom.rm_matnr,
             COALESCE(rm_inv.rm_on_hand_units, 0) AS rm_on_hand_units,
             rm_inv.earliest_rm_expiry
      FROM bom LEFT JOIN rm_inv USING (rm_matnr)
    """
    try:
        rows = _run_query(sql, params)
    except Exception as exc:
        # BOM data may be sparse in v2; return graceful empty.
        return {"rows": [], "directional_concern": False,
                "rationale": f"BOM lookup unavailable: {exc}",
                "view_queried":
                  "tiger_semantic.dim_material + fct_inventory_movements"}
    concern = any(r.get("rm_on_hand_units", 0) < 1000 for r in rows)
    return {"rows": rows,
            "directional_concern": concern,
            "rationale": ("One or more RM lots low on hand"
                          if concern else "RM levels adequate"),
            "view_queried":
              "tiger_semantic.dim_material + fct_inventory_movements",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# Procurement
# ---------------------------------------------------------------------------
def get_procurement_orders(
    horizon_days: int = 30,
    vendor_id: Optional[str] = None,
) -> dict:
    """Inbound procurement orders. Used by Supply Planning Agent for
    forward RM ETA visibility."""
    where = ["expected_receipt_date <= DATE_ADD(CURRENT_DATE(), "
             "INTERVAL @days DAY)",
             "status IN ('OPEN', 'IN_TRANSIT')"]
    params = [bigquery.ScalarQueryParameter("days", "INT64", horizon_days)]
    if vendor_id:
        where.append("vendor_id = @vendor")
        params.append(bigquery.ScalarQueryParameter(
            "vendor", "STRING", vendor_id))
    sql = f"""
      SELECT procurement_order_id, vendor_id, material_matnr,
             ordered_qty, expected_receipt_date, status,
             delay_days
      FROM `{SEMANTIC_DS}.fct_inventory_movements`
      WHERE {' AND '.join(where)}
        AND movement_type = 'PROCUREMENT_INBOUND'
      ORDER BY expected_receipt_date ASC
      LIMIT 50
    """
    try:
        rows = _run_query(sql, params)
    except Exception:
        return {"rows": [], "view_queried":
                  "tiger_semantic.fct_inventory_movements (procurement)",
                "row_count": 0}
    return {"rows": rows,
            "view_queried":
              "tiger_semantic.fct_inventory_movements (procurement)",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# Master Data
# ---------------------------------------------------------------------------
def get_customer_compliance_rules(
    customer_kunnr: str,
) -> dict:
    """Customer-specific compliance: OTIF target, fine rate, MRSL,
    pallet config. Source of truth: dim_customer."""
    params = [bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr)]
    sql = f"""
      SELECT customer_kunnr, customer_name,
             otif_fine_rate_usd_per_cs AS fine_rate_usd_per_cs,
             otif_target_pct AS customer_otif_target_pct,
             mrsl_days_required,
             pallet_config_notes
      FROM `{SEMANTIC_DS}.dim_customer`
      WHERE customer_kunnr = @kunnr
      LIMIT 1
    """
    rows = _run_query(sql, params)
    if not rows:
        return {"error": f"Customer {customer_kunnr} not found in dim_customer",
                "view_queried": "tiger_semantic.dim_customer"}
    r = rows[0]
    r["view_queried"] = "tiger_semantic.dim_customer"
    return r


# ---------------------------------------------------------------------------
# Logistics
# ---------------------------------------------------------------------------
def get_lane_capacity(
    origin_plant: str,
    destination_region: str,
    ship_date: Optional[str] = None,
    quantity_cs: int = 0,
) -> dict:
    """Lane-level capacity and viability check."""
    params = [
        bigquery.ScalarQueryParameter("origin", "STRING", origin_plant),
        bigquery.ScalarQueryParameter("dest", "STRING", destination_region),
        bigquery.ScalarQueryParameter("qty", "INT64", quantity_cs),
    ]
    sql = f"""
      WITH lane AS (
        SELECT AVG(transit_days) AS avg_transit_days,
               COUNT(*) AS recent_shipment_count,
               AVG(actual_capacity_remaining_cs) AS avg_capacity_remaining
        FROM `{SEMANTIC_DS}.fct_delivery`
        WHERE origin_plant = @origin
          AND destination_region = @dest
          AND delivery_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
      )
      SELECT @origin AS origin_plant,
             @dest AS destination_region,
             avg_transit_days,
             recent_shipment_count,
             avg_capacity_remaining,
             avg_capacity_remaining >= @qty AS capacity_sufficient
      FROM lane
    """
    rows = _run_query(sql, params)
    r = rows[0] if rows else {}
    r["view_queried"] = "tiger_semantic.fct_delivery"
    return r


def get_carrier_otp(
    origin_plant: str,
    destination_region: str,
    trailing_days: int = 30,
) -> dict:
    """Carrier on-time performance on a lane."""
    params = [
        bigquery.ScalarQueryParameter("origin", "STRING", origin_plant),
        bigquery.ScalarQueryParameter("dest", "STRING", destination_region),
        bigquery.ScalarQueryParameter("days", "INT64", trailing_days),
    ]
    sql = f"""
      SELECT carrier_id, carrier_name,
             COUNT(*) AS deliveries,
             SUM(CAST(is_on_time AS INT64)) AS on_time_deliveries,
             SAFE_DIVIDE(SUM(CAST(is_on_time AS INT64)), COUNT(*))
               AS trailing_otp_pct
      FROM `{SEMANTIC_DS}.fct_delivery`
      WHERE origin_plant = @origin
        AND destination_region = @dest
        AND delivery_date >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
      GROUP BY carrier_id, carrier_name
      ORDER BY deliveries DESC
      LIMIT 5
    """
    rows = _run_query(sql, params)
    return {"rows": rows,
            "view_queried": "tiger_semantic.fct_delivery",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# Retail Signals — v3 stubs
# ---------------------------------------------------------------------------
# These tools return data_available=false in v2. When the v3 retail data
# layer lands, the tools detect view presence and switch to live queries
# without any agent prompt change.
#
# IMPORTANT: the view names below are PROVISIONAL PLACEHOLDERS. The v3 data
# engineering team owns the actual naming, schema, and materialization
# choices. These constants exist so the agent tools have something concrete
# to query against during development, AND so the v3 team can override the
# names via env vars without code changes once they make their decisions:
#
#   export RETAIL_DC_INVENTORY_VIEW="..."
#   export RETAIL_STORE_INVENTORY_VIEW="..."
#   export RETAIL_VELOCITY_VIEW="..."
#   export RETAIL_PROMO_VIEW="..."
#
# The architectural contract (DC/store split, crosswalk-resolved keys, agent
# tool surface) is what's stable. The view names are the data team's call.
# See reference/retail_data_gap_v2.md for the contract spec.
#
# Resolution from external retailer keys (retailer_item_number / ean_upc) to
# MATNR happens in the semantic-layer view DDL via
# tiger_semantic.resolve_external_to_internal — the agent tools just see
# already-resolved customer_kunnr + material_matnr.
# ---------------------------------------------------------------------------

# Provisional view names — v3 team confirms / renames
RETAIL_DC_INVENTORY_VIEW    = os.environ.get("RETAIL_DC_INVENTORY_VIEW",    "fct_retail_dc_inventory")
RETAIL_STORE_INVENTORY_VIEW = os.environ.get("RETAIL_STORE_INVENTORY_VIEW", "fct_retail_store_inventory")
RETAIL_VELOCITY_VIEW        = os.environ.get("RETAIL_VELOCITY_VIEW",        "fct_retail_velocity")
RETAIL_PROMO_VIEW           = os.environ.get("RETAIL_PROMO_VIEW",           "dim_promotion")

def _retail_view_exists(view_name: str) -> bool:
    """Check if a retail signals view exists. Returns False in v2."""
    try:
        _bq.get_table(f"{SEMANTIC_DS}.{view_name}")
        return True
    except Exception:
        return False


def get_retail_dc_inventory(
    customer_kunnr: str,
    material_matnr: str,
) -> dict:
    """Retailer DC (warehouse) on-hand and days-of-supply by SKU.
    V2: returns data_available=false. V3: queries fct_retail_dc_inventory.

    Used by Retail Intelligence Agent for buffer-build / DOS signals.
    The underlying view resolves external retailer keys
    (retailer_item_number / ean_upc) to MATNR via the
    tiger_semantic.resolve_external_to_internal TVF — see
    reference/retail_data_gap_v2.md for the view DDL contract.
    """
    if not _retail_view_exists(RETAIL_DC_INVENTORY_VIEW):
        return {
            "data_available": False,
            "v3_pending": True,
            "view_queried":
              f"tiger_semantic.{RETAIL_DC_INVENTORY_VIEW} (NOT LOADED)",
            "rationale": ("Retail DC inventory data not yet loaded. See "
                          "reference/retail_data_gap_v2.md."),
        }
    params = [
        bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr),
        bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr),
    ]
    sql = f"""
      SELECT customer_kunnr, material_matnr,
             retailer_dc_id, snapshot_date,
             on_hand_units, days_of_supply,
             dos_trend_4w
      FROM `{SEMANTIC_DS}.{RETAIL_DC_INVENTORY_VIEW}`
      WHERE customer_kunnr = @kunnr
        AND material_matnr = @matnr
        AND snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
      ORDER BY snapshot_date DESC, retailer_dc_id
      LIMIT 50
    """
    rows = _run_query(sql, params)
    return {
        "data_available": bool(rows),
        "rows": rows,
        "view_queried": f"tiger_semantic.{RETAIL_DC_INVENTORY_VIEW}",
        "row_count": len(rows),
    }


def get_retail_store_inventory(
    customer_kunnr: str,
    material_matnr: str,
) -> dict:
    """Retailer store-level on-hand by SKU. V2: returns data_available=false.
    V3: queries fct_retail_store_inventory.

    Used by Retail Intelligence Agent for OOS / lost-sales signals.
    Volume is typically 10-100x larger than DC inventory (one row per store,
    not per DC). Resolution from external keys to MATNR happens in the view
    layer via resolve_external_to_internal TVF.
    """
    if not _retail_view_exists(RETAIL_STORE_INVENTORY_VIEW):
        return {
            "data_available": False,
            "v3_pending": True,
            "view_queried":
              f"tiger_semantic.{RETAIL_STORE_INVENTORY_VIEW} (NOT LOADED)",
            "rationale": ("Retail store inventory data not yet loaded. See "
                          "reference/retail_data_gap_v2.md."),
        }
    params = [
        bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr),
        bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr),
    ]
    sql = f"""
      SELECT customer_kunnr, material_matnr,
             store_id, snapshot_date,
             on_hand_units, oos_flag
      FROM `{SEMANTIC_DS}.{RETAIL_STORE_INVENTORY_VIEW}`
      WHERE customer_kunnr = @kunnr
        AND material_matnr = @matnr
        AND snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
      ORDER BY snapshot_date DESC, store_id
      LIMIT 500
    """
    rows = _run_query(sql, params)
    oos_count = sum(1 for r in rows if r.get("oos_flag"))
    return {
        "data_available": bool(rows),
        "rows": rows,
        "view_queried": f"tiger_semantic.{RETAIL_STORE_INVENTORY_VIEW}",
        "row_count": len(rows),
        "store_oos_count": oos_count,
    }


def get_retail_velocity(
    customer_kunnr: str,
    material_matnr: str,
    weeks_back: int = 8,
) -> dict:
    """Retailer POS velocity by week. V2: data_available=false."""
    if not _retail_view_exists(RETAIL_VELOCITY_VIEW):
        return {
            "data_available": False,
            "v3_pending": True,
            "view_queried": f"tiger_semantic.{RETAIL_VELOCITY_VIEW} (NOT LOADED)",
            "rationale": ("Retail velocity data not yet loaded. See "
                          "reference/retail_data_gap_v2.md."),
        }
    params = [
        bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr),
        bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr),
        bigquery.ScalarQueryParameter("weeks", "INT64", weeks_back),
    ]
    sql = f"""
      SELECT iso_week,
             pos_units_sold,
             pos_units_sold - LAG(pos_units_sold, 1)
                OVER (ORDER BY iso_week) AS wow_change
      FROM `{SEMANTIC_DS}.{RETAIL_VELOCITY_VIEW}`
      WHERE customer_kunnr = @kunnr
        AND material_matnr = @matnr
        AND iso_week >= FORMAT_DATE('%G-W%V',
                          DATE_SUB(CURRENT_DATE(), INTERVAL @weeks WEEK))
      ORDER BY iso_week ASC
    """
    rows = _run_query(sql, params)
    return {
        "data_available": bool(rows),
        "rows": rows,
        "view_queried": f"tiger_semantic.{RETAIL_VELOCITY_VIEW}",
        "row_count": len(rows),
    }


def get_promotional_calendar(
    customer_kunnr: str,
    week: Optional[str] = None,
) -> dict:
    """Promotional calendar from TPM. V2: data_available=false."""
    if not _retail_view_exists(RETAIL_PROMO_VIEW):
        return {
            "data_available": False,
            "v3_pending": True,
            "view_queried": f"tiger_semantic.{RETAIL_PROMO_VIEW} (NOT LOADED)",
            "rationale": ("Promotional calendar from TPM not yet loaded. "
                          "See reference/retail_data_gap_v2.md."),
        }
    params = [bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr)]
    week_filter = ""
    if week:
        week_filter = "AND promo_week = @week"
        params.append(bigquery.ScalarQueryParameter("week", "STRING", week))
    sql = f"""
      SELECT promo_id, customer_kunnr, material_matnr,
             promo_week, promo_type, lift_multiplier
      FROM `{SEMANTIC_DS}.{RETAIL_PROMO_VIEW}`
      WHERE customer_kunnr = @kunnr
      {week_filter}
      LIMIT 20
    """
    rows = _run_query(sql, params)
    return {"data_available": bool(rows), "rows": rows,
            "view_queried": f"tiger_semantic.{RETAIL_PROMO_VIEW}",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# DCE write (Customer Supply Agent does NOT call this directly — orchestrator
# does after human approval). Kept here so the tool surface is complete.
# ---------------------------------------------------------------------------
def dce_write(
    session_id: str,
    decision_payload_json: str,
    user_decision: Literal["approved", "rejected", "cancelled"],
    user_id: Optional[str] = None,
    rejection_reason: Optional[str] = None,
) -> dict:
    """Write a Decision Capture Engine record. Extends v1 log_decision to
    populate the DCE-specific columns added by infra/dce_alter_table_v2.sql.

    The orchestrator calls this — not the agent."""
    import json
    import uuid
    from datetime import datetime, timezone

    decision_id = str(uuid.uuid4())
    payload = json.loads(decision_payload_json)
    rec = payload.get("recommendation", {}) or {}
    order = payload.get("order", {}) or {}
    dce = payload.get("dce_payload", {}) or {}
    signals = payload.get("specialist_signals", {}) or {}

    cs_action = rec.get("action")
    aligned = (cs_action and user_decision == "approved")

    # CDM domains as a BigQuery-compatible array
    domains = dce.get("cdm_domains_referenced", [])

    row = {
        # v1 columns (preserved)
        "decision_id":          decision_id,
        "session_id":           session_id,
        "decision_timestamp":   datetime.now(timezone.utc).isoformat(),
        "trigger_type":         payload.get("trigger_type", "new_order"),
        "customer_kunnr":       order.get("customer_kunnr"),
        "customer_name":        order.get("customer_name"),
        "material_matnr":       order.get("material_matnr"),
        "material_name":        order.get("material_name"),
        "shipment_or_order_id": payload.get("sales_order_id"),
        "mabd":                 order.get("mabd"),
        "recommended_action":   cs_action,
        "origin_plant":         None,   # synthesizer-level; not always set
        "destination":          order.get("ship_to"),
        "quantity_cs":          rec.get("fulfill_qty_cs"),
        "carrier_mode":         None,
        "estimated_freight_cost_usd": None,
        "avoided_fine_usd":     None,
        "net_value_usd":        None,
        "human_decision":       user_decision,
        "human_decision_at":    datetime.now(timezone.utc).isoformat(),
        "human_decision_by":    user_id,
        "rejection_reason":     rejection_reason,
        "watchdog_final_round":  None,
        "economist_final_round": None,
        "convergence_round":     None,
        "was_deadlocked":        any(
            c.get("resolution") == "DEADLOCK"
            for c in payload.get("conflicts_detected", [])),
        "reasoning_summary_json": decision_payload_json,
        "watchdog_confidence":   None,
        "economist_confidence":  None,
        "agent_model_versions":  "gemini-2.5-pro,gemini-2.5-flash",
        "orchestrator_version":  "v2.0.0",
        # v2 DCE-specific columns (added by ALTER TABLE)
        "flow_mode":             "five_agent",
        "agent_recommendation":  cs_action,
        "agent_confidence_score": rec.get("confidence"),
        "user_decision":         user_decision,
        "decision_aligned_with_agent": aligned,
        "user_modification_notes": None,
        "cdm_domains_referenced": domains,
        "outcome_cfr_impact_cs": None,    # populated retrospectively
        "outcome_fine_avoided_usd": None, # populated retrospectively
        "scenario_tag":          dce.get("scenario_tag"),
    }
    table_ref = f"{DECISIONS_DS}.fct_allocation_decisions"
    errors = _bq.insert_rows_json(table_ref, [row])
    if errors:
        return {"error": str(errors)}
    return {"decision_id": decision_id,
            "inserted_at": row["decision_timestamp"],
            "table": table_ref,
            "dce": True}


# ---------------------------------------------------------------------------
# Tool surfaces per agent
# ---------------------------------------------------------------------------
CUSTOMER_SUPPLY_TOOLS_V2 = [
    FunctionTool(func=get_open_sales_orders),
    FunctionTool(func=get_finished_goods_inventory),
    FunctionTool(func=get_customer_compliance_rules),
    FunctionTool(func=classify_order_vs_forecast),
    FunctionTool(func=get_allocation_history),
    # `invoke_specialist` is exposed by the orchestrator runtime, not as
    # a BigQuery tool — see orchestrator_v2.py.
]

SUPPLY_PLANNING_TOOLS_V2 = [
    FunctionTool(func=get_production_orders),
    FunctionTool(func=get_finished_goods_inventory),
    FunctionTool(func=get_raw_materials_status),
    FunctionTool(func=get_procurement_orders),
    FunctionTool(func=get_safety_stock_position),
    FunctionTool(func=get_shelf_life_risk),
]

DEMAND_PLANNING_TOOLS_V2 = [
    # forecast_accuracy reused from v1
    FunctionTool(func=get_order_history),
    FunctionTool(func=classify_order_vs_forecast),
    FunctionTool(func=get_retail_velocity),       # v3 stub in v2
    FunctionTool(func=get_promotional_calendar),  # v3 stub in v2
]

TRANSPORTATION_TOOLS_V2 = [
    FunctionTool(func=get_otif_performance),
    FunctionTool(func=get_lane_capacity),
    FunctionTool(func=get_carrier_otp),
    FunctionTool(func=get_chargeback_risk),
    FunctionTool(func=get_transfer_cost_comparison),
    FunctionTool(func=get_active_alerts),
]

RETAIL_INTELLIGENCE_TOOLS_V2 = [
    FunctionTool(func=get_retail_dc_inventory),    # v3 stub in v2
    FunctionTool(func=get_retail_store_inventory), # v3 stub in v2
    FunctionTool(func=get_retail_velocity),        # v3-deferred stub (beyond initial v3)
    FunctionTool(func=get_shelf_life_risk),
    FunctionTool(func=get_customer_compliance_rules),
    FunctionTool(func=get_order_history),
]

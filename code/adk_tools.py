"""
ADK tool definitions for the Tiger Foods agentic AI.

Each tool wraps ONE parameterized query against tiger_semantic (or writes
tiger_decisions). Agents call these tools by name; agents never write SQL.

GCP project:   resilience-riskradar
Datasets:      tiger_semantic (read-only for agents), tiger_decisions (writable
                only for log_decision)

Tools (10):
  1.  get_otif_performance         - fct_otif, agg_otif_customer_quarter
  2.  get_cfr_weekly               - agg_cfr_weekly
  3.  get_inventory_positions      - fct_inventory_movements
  4.  get_shelf_life_risk          - fct_inventory_movements + dim_customer (MRSL)
  5.  get_chargeback_risk          - fct_chargebacks + dim_customer
  6.  get_transfer_cost_comparison - fct_delivery
  7.  get_forecast_accuracy        - fct_forecast_accuracy
  8.  get_allocation_history       - fct_allocation_decisions (read both datasets)
  9.  get_active_alerts            - fct_otif
  10. log_decision                 - WRITES tiger_decisions.fct_allocation_decisions
"""


import json as _json
import os
import uuid
from datetime import date, datetime, timezone

from google.adk.tools import FunctionTool
from google.cloud import bigquery

PROJECT_ID = os.environ.get("PROJECT_ID", "resilience-riskradar")
SEMANTIC_DS = f"{PROJECT_ID}.tiger_semantic"
DECISIONS_DS = f"{PROJECT_ID}.tiger_decisions"

_bq = bigquery.Client(project=PROJECT_ID)


def _run_query(sql: str, params: list) -> list[dict]:
    """Execute a parameterized BigQuery job and return rows as list of dicts.

    Single chokepoint to BigQuery. All tools route through this so tool calls
    can be audited via the orchestrator's Firestore step writes.
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    job = _bq.query(sql, job_config=job_config)
    rows = job.result()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# TOOL 1 — get_otif_performance
# ---------------------------------------------------------------------------
def get_otif_performance(
    customer_kunnr: str,
    start_date: str,
    end_date: str,
    group_by: str,
) -> dict:
    """Returns OTIF performance from fct_otif, optionally aggregated.

    Args:
        customer_kunnr: SAP customer number to filter to. Pass empty string for all customers.
        start_date: ISO date YYYY-MM-DD inclusive. Pass empty string for first of current month.
        end_date: ISO date YYYY-MM-DD inclusive. Pass empty string for today.
        group_by: Aggregation dimension: customer | carrier | lane | week. Default customer.

    Returns:
        dict with rows, view_queried, row_count.
    """
    customer_kunnr = customer_kunnr or ""
    start_date = start_date or (date.today().replace(day=1)).isoformat()
    end_date = end_date or date.today().isoformat()
    group_by = group_by or "customer"
    where = ["delivery_date BETWEEN @start AND @end"]
    params = [
        bigquery.ScalarQueryParameter("start", "DATE", start_date),
        bigquery.ScalarQueryParameter("end", "DATE", end_date),
    ]
    if customer_kunnr:
        where.append("customer_kunnr = @kunnr")
        params.append(bigquery.ScalarQueryParameter("kunnr", "STRING",
                                                    customer_kunnr))

    group_col = {
        "customer": "customer_kunnr, customer_name",
        "carrier":  "carrier_id, carrier_name",
        "lane":     "origin_plant, destination_region",
        "week":     "FORMAT_DATE('%G-W%V', delivery_date) AS iso_week",
    }.get(group_by, "customer_kunnr, customer_name")

    sql = f"""
      SELECT {group_col},
             COUNT(*)                                              AS deliveries_total,
             SUM(CAST(is_otif AS INT64))                            AS deliveries_otif,
             SAFE_DIVIDE(SUM(CAST(is_otif AS INT64)), COUNT(*))     AS otif_rate,
             SUM(IF(is_otif=FALSE, otif_fine_usd, 0))               AS total_fine_exposure_usd
      FROM `{SEMANTIC_DS}.fct_otif`
      WHERE {' AND '.join(where)}
      GROUP BY {group_col}
      ORDER BY deliveries_total DESC
      LIMIT 100
    """
    rows = _run_query(sql, params)
    return {
        "rows": rows,
        "view_queried": "tiger_semantic.fct_otif",
        "row_count": len(rows),
    }


# ---------------------------------------------------------------------------
# TOOL 2 — get_cfr_weekly
# ---------------------------------------------------------------------------
def get_cfr_weekly(
    weeks_back: int,
    customer_kunnr: str,
) -> dict:
    """Returns weekly Case Fill Rate trend.

    Args:
        weeks_back: Number of trailing weeks. Pass 0 to use default of 8.
        customer_kunnr: Customer filter. Pass empty string for network total.
    """
    weeks_back = weeks_back or 8
    customer_kunnr = customer_kunnr or ""
    where = ["iso_week >= FORMAT_DATE('%G-W%V', "
             "DATE_SUB(CURRENT_DATE(), INTERVAL @weeks WEEK))"]
    params = [bigquery.ScalarQueryParameter("weeks", "INT64", weeks_back)]
    if customer_kunnr:
        where.append("customer_kunnr = @kunnr")
        params.append(bigquery.ScalarQueryParameter("kunnr", "STRING",
                                                    customer_kunnr))

    sql = f"""
      SELECT iso_week,
             cfr_pct,
             cfr_pct - 0.98 AS vs_target_pct,
             cases_shipped,
             cases_ordered
      FROM `{SEMANTIC_DS}.agg_cfr_weekly`
      WHERE {' AND '.join(where)}
      ORDER BY iso_week DESC
    """
    rows = _run_query(sql, params)
    return {"rows": rows, "view_queried": "tiger_semantic.agg_cfr_weekly",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# TOOL 3 — get_inventory_positions
# ---------------------------------------------------------------------------
def get_inventory_positions(
    plant: str,
    material_matnr: str,
    include_shelf_life: bool,
) -> dict:
    """Returns current inventory positions by plant and material.

    Args:
        plant: WERKS plant code to filter. Pass empty string for all plants.
        material_matnr: MATNR to filter. Pass empty string for all materials.
        include_shelf_life: Pass true to include earliest expiration date.
    """
    plant = plant or ""
    material_matnr = material_matnr or ""
    where = ["movement_date = CURRENT_DATE()"]
    params = []
    if plant:
        where.append("plant = @plant")
        params.append(bigquery.ScalarQueryParameter("plant", "STRING", plant))
    if material_matnr:
        where.append("material_matnr = @matnr")
        params.append(bigquery.ScalarQueryParameter("matnr", "STRING",
                                                    material_matnr))

    shelf_life_select = """,
             earliest_expiry_date AS earliest_expiry,
             DATE_DIFF(earliest_expiry_date, CURRENT_DATE(), DAY) AS days_to_expiry
    """ if include_shelf_life else ""

    sql = f"""
      SELECT plant,
             plant_name,
             material_matnr AS matnr,
             material_name,
             qty_on_hand_cs,
             qty_committed_cs,
             qty_on_hand_cs - qty_committed_cs AS qty_available_cs
             {shelf_life_select}
      FROM `{SEMANTIC_DS}.fct_inventory_movements`
      WHERE {' AND '.join(where)}
      ORDER BY qty_available_cs DESC
      LIMIT 200
    """
    rows = _run_query(sql, params)
    return {"rows": rows,
            "view_queried": "tiger_semantic.fct_inventory_movements",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# TOOL 4 — get_shelf_life_risk
# ---------------------------------------------------------------------------
def get_shelf_life_risk(
    customer_kunnr: str,
    material_matnr: str,
    horizon_days: int,
) -> dict:
    """Identifies inventory at MRSL conflict risk for a specific customer.

    MRSL = Minimum Remaining Shelf Life. Each customer specifies a minimum
    shelf-life-remaining at delivery (in days).

    Args:
        customer_kunnr: SAP customer number. Required (MRSL is customer-specific).
        material_matnr: Material filter. Pass empty string for all materials.
        horizon_days: Days forward to consider. Pass 0 to use default of 30.
    """
    material_matnr = material_matnr or ""
    horizon_days = horizon_days or 30
    where = [
        "i.movement_date = CURRENT_DATE()",
        "c.customer_kunnr = @kunnr",
        ("DATE_DIFF(i.earliest_expiry_date, CURRENT_DATE(), DAY) "
         "< c.mrsl_days_required"),
    ]
    params = [bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr)]
    if material_matnr:
        where.append("i.material_matnr = @matnr")
        params.append(bigquery.ScalarQueryParameter("matnr", "STRING",
                                                    material_matnr))

    sql = f"""
      SELECT i.material_matnr AS matnr,
             i.material_name,
             i.qty_on_hand_cs AS qty_at_risk_cs,
             c.mrsl_days_required,
             i.earliest_expiry_date AS earliest_expiry,
             DATE_DIFF(i.earliest_expiry_date, CURRENT_DATE(), DAY)
               AS days_to_expiry,
             i.plant,
             i.plant_name
      FROM `{SEMANTIC_DS}.fct_inventory_movements` i
      CROSS JOIN `{SEMANTIC_DS}.dim_customer` c
      WHERE {' AND '.join(where)}
      ORDER BY days_to_expiry ASC
      LIMIT 100
    """
    rows = _run_query(sql, params)
    return {"rows": rows,
            "view_queried":
                "tiger_semantic.fct_inventory_movements + dim_customer",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# TOOL 5 — get_chargeback_risk
# ---------------------------------------------------------------------------
def get_chargeback_risk(
    customer_kunnr: str,
    shipment_date: str,
    fine_rate_override_usd_per_cs: float,
) -> dict:
    """Returns customer fine rates and chargeback exposure.

    Args:
        customer_kunnr: SAP customer number. Required.
        shipment_date: ISO date for fine schedule lookup. Pass empty string to skip.
        fine_rate_override_usd_per_cs: Override fine rate. Pass 0.0 to use contract rate.
    """
    shipment_date = shipment_date or ""
    fine_rate_override_usd_per_cs = fine_rate_override_usd_per_cs or 0.0
    params = [bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr)]
    sql = f"""
      WITH cust AS (
        SELECT customer_kunnr, customer_name,
               otif_fine_rate_usd_per_cs AS fine_rate_usd_per_cs
        FROM `{SEMANTIC_DS}.dim_customer`
        WHERE customer_kunnr = @kunnr
      ),
      cb AS (
        SELECT SUM(chargeback_amount_usd) AS total_amt,
               COUNT(*) AS cnt
        FROM `{SEMANTIC_DS}.fct_chargebacks`
        WHERE customer_kunnr = @kunnr
          AND chargeback_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
      ),
      top_reasons AS (
        SELECT reason_code, SUM(chargeback_amount_usd) AS amount_usd
        FROM `{SEMANTIC_DS}.fct_chargebacks`
        WHERE customer_kunnr = @kunnr
          AND chargeback_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
        GROUP BY reason_code
        ORDER BY amount_usd DESC
        LIMIT 5
      )
      SELECT
        cust.customer_kunnr, cust.customer_name, cust.fine_rate_usd_per_cs,
        cb.total_amt AS trailing_90d_chargebacks_usd,
        cb.cnt       AS trailing_90d_chargeback_count,
        ARRAY(SELECT AS STRUCT reason_code AS code, amount_usd FROM top_reasons)
          AS top_reason_codes
      FROM cust LEFT JOIN cb ON TRUE
    """
    rows = _run_query(sql, params)
    if not rows:
        return {"error": f"Customer {customer_kunnr} not found in dim_customer",
                "view_queried": "tiger_semantic.dim_customer"}
    row = rows[0]
    if fine_rate_override_usd_per_cs:
        row["fine_rate_usd_per_cs"] = fine_rate_override_usd_per_cs
    row["view_queried"] = ("tiger_semantic.dim_customer + "
                            "tiger_semantic.fct_chargebacks")
    return row


# ---------------------------------------------------------------------------
# TOOL 6 — get_transfer_cost_comparison
# ---------------------------------------------------------------------------
def get_transfer_cost_comparison(
    origin_plant: str,
    destination_region: str,
    material_matnr: str,
    quantity_cs: int,
) -> dict:
    """Returns historical lane freight cost for an origin-destination-material.

    Args:
        origin_plant: WERKS code of origin (e.g. 'DC-01').
        destination_region: Destination region code (e.g. 'US-FL-WALMART').
        material_matnr: MATNR.
        quantity_cs: Quantity in cases.
    """
    params = [
        bigquery.ScalarQueryParameter("origin", "STRING", origin_plant),
        bigquery.ScalarQueryParameter("dest", "STRING", destination_region),
        bigquery.ScalarQueryParameter("matnr", "STRING", material_matnr),
        bigquery.ScalarQueryParameter("qty", "INT64", quantity_cs),
    ]
    sql = f"""
      WITH lane AS (
        SELECT freight_cost_usd, quantity_cs, transit_days
        FROM `{SEMANTIC_DS}.fct_delivery`
        WHERE origin_plant = @origin
          AND destination_region = @dest
          AND material_matnr = @matnr
          AND delivery_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
      )
      SELECT
        @origin AS origin_plant,
        @dest AS destination_region,
        @matnr AS matnr,
        @qty AS quantity_cs,
        SAFE_DIVIDE(SUM(freight_cost_usd), SUM(quantity_cs))
            AS trailing_90d_avg_cost_per_cs,
        COUNT(*) AS trailing_90d_shipment_count,
        AVG(transit_days) AS avg_transit_days
      FROM lane
    """
    rows = _run_query(sql, params)
    row = rows[0] if rows else {}
    cost_per_cs = row.get("trailing_90d_avg_cost_per_cs") or 0.0
    row["estimated_total_cost_usd"] = round(cost_per_cs * quantity_cs, 2)
    row["view_queried"] = "tiger_semantic.fct_delivery"
    return row


# ---------------------------------------------------------------------------
# TOOL 7 — get_forecast_accuracy
# ---------------------------------------------------------------------------
def get_forecast_accuracy(
    customer_kunnr: str,
    material_matnr: str,
    lag_weeks: int,
) -> dict:
    """Returns Anaplan forecast accuracy and bias at a given lag.

    Args:
        customer_kunnr: SAP customer number. Required.
        material_matnr: Material filter. Pass empty string for all materials.
        lag_weeks: Forecast lag to evaluate. Pass 0 to use default of 4.
    """
    material_matnr = material_matnr or ""
    lag_weeks = lag_weeks or 4
    where = ["customer_kunnr = @kunnr", "lag_weeks = @lag"]
    params = [
        bigquery.ScalarQueryParameter("kunnr", "STRING", customer_kunnr),
        bigquery.ScalarQueryParameter("lag", "INT64", lag_weeks),
    ]
    if material_matnr:
        where.append("material_matnr = @matnr")
        params.append(bigquery.ScalarQueryParameter("matnr", "STRING",
                                                    material_matnr))

    sql = f"""
      SELECT @kunnr AS customer_kunnr,
             {'@matnr' if material_matnr else 'NULL'} AS matnr,
             @lag AS lag_weeks,
             AVG(ABS(forecast_qty - actual_qty) / NULLIF(actual_qty, 0))
                AS mape_pct,
             AVG((forecast_qty - actual_qty) / NULLIF(actual_qty, 0))
                AS bias_pct,
             COUNT(*) AS n_observations
      FROM `{SEMANTIC_DS}.fct_forecast_accuracy`
      WHERE {' AND '.join(where)}
    """
    rows = _run_query(sql, params)
    row = rows[0] if rows else {}
    row["view_queried"] = "tiger_semantic.fct_forecast_accuracy"
    return row


# ---------------------------------------------------------------------------
# TOOL 8 — get_allocation_history
# ---------------------------------------------------------------------------
def get_allocation_history(
    customer_kunnr: str,
    material_matnr: str,
    lookback_days: int,
) -> dict:
    """Returns prior allocation decisions for similar contexts.

    Reads from BOTH tiger_semantic.fct_allocation_decisions (historical, from
    SAP Z-tables) AND tiger_decisions.fct_allocation_decisions (this system's
    own decisions).

    Args:
        customer_kunnr: Customer filter. Pass empty string for all customers.
        material_matnr: Material filter. Pass empty string for all materials.
        lookback_days: Days to look back. Pass 0 to use default of 90.
    """
    customer_kunnr = customer_kunnr or ""
    material_matnr = material_matnr or ""
    lookback_days = lookback_days or 90
    where_sem = ["decision_date >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)"]
    where_dec = [
        "DATE(decision_timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)"
    ]
    params = [bigquery.ScalarQueryParameter("days", "INT64", lookback_days)]
    if customer_kunnr:
        where_sem.append("customer_kunnr = @kunnr")
        where_dec.append("customer_kunnr = @kunnr")
        params.append(bigquery.ScalarQueryParameter("kunnr", "STRING",
                                                    customer_kunnr))
    if material_matnr:
        where_sem.append("material_matnr = @matnr")
        where_dec.append("material_matnr = @matnr")
        params.append(bigquery.ScalarQueryParameter("matnr", "STRING",
                                                    material_matnr))

    sql = f"""
      WITH historical AS (
        SELECT decision_date, action, origin_plant, qty_cs,
               human_decision, net_value_usd, 'historical' AS source
        FROM `{SEMANTIC_DS}.fct_allocation_decisions`
        WHERE {' AND '.join(where_sem)}
      ),
      agentic AS (
        SELECT DATE(decision_timestamp) AS decision_date,
               recommended_action AS action, origin_plant,
               quantity_cs AS qty_cs, human_decision, net_value_usd,
               'agentic' AS source
        FROM `{DECISIONS_DS}.fct_allocation_decisions`
        WHERE {' AND '.join(where_dec)}
      )
      SELECT * FROM historical
      UNION ALL
      SELECT * FROM agentic
      ORDER BY decision_date DESC
      LIMIT 100
    """
    rows = _run_query(sql, params)
    approved = sum(1 for r in rows if r.get("human_decision") == "approved")
    total = len(rows)
    return {
        "rows": rows,
        "approval_rate": (approved / total) if total else None,
        "view_queried": ("tiger_semantic.fct_allocation_decisions + "
                         "tiger_decisions.fct_allocation_decisions"),
        "row_count": total,
    }


# ---------------------------------------------------------------------------
# TOOL 9 — get_active_alerts
# ---------------------------------------------------------------------------
def get_active_alerts(
    severity_min: str,
    limit: int,
) -> dict:
    """Returns currently active alerts ranked by exposure.

    Args:
        severity_min: Minimum severity to include: LOW | MEDIUM | HIGH | CRITICAL. Default MEDIUM.
        limit: Maximum rows to return. Pass 0 to use default of 20.
    """
    severity_min = (severity_min or "MEDIUM").upper()
    limit = limit or 20
    sev_order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    params = [
        bigquery.ScalarQueryParameter("min_sev", "INT64",
                                       sev_order.get(severity_min, 2)),
        bigquery.ScalarQueryParameter("lim", "INT64", limit),
    ]
    sql = f"""
      SELECT
        CONCAT('alert_', GENERATE_UUID())         AS alert_id,
        CASE
          WHEN otif_fine_exposure_usd >= 20000 THEN 4
          WHEN otif_fine_exposure_usd >= 10000 THEN 3
          WHEN otif_fine_exposure_usd >= 2500  THEN 2
          ELSE 1
        END                                       AS severity_rank,
        CASE
          WHEN otif_fine_exposure_usd >= 20000 THEN 'CRITICAL'
          WHEN otif_fine_exposure_usd >= 10000 THEN 'HIGH'
          WHEN otif_fine_exposure_usd >= 2500  THEN 'MEDIUM'
          ELSE 'LOW'
        END                                       AS severity,
        'OTIF_BREACH'                             AS alert_type,
        customer_name, customer_kunnr,
        material_matnr, material_name,
        otif_fine_exposure_usd AS financial_exposure_usd,
        mabd, shipment_id
      FROM `{SEMANTIC_DS}.fct_otif`
      WHERE is_at_risk = TRUE
        AND CASE
              WHEN otif_fine_exposure_usd >= 20000 THEN 4
              WHEN otif_fine_exposure_usd >= 10000 THEN 3
              WHEN otif_fine_exposure_usd >= 2500  THEN 2
              ELSE 1
            END >= @min_sev
      ORDER BY otif_fine_exposure_usd DESC
      LIMIT @lim
    """
    rows = _run_query(sql, params)
    return {"rows": rows, "view_queried": "tiger_semantic.fct_otif",
            "row_count": len(rows)}


# ---------------------------------------------------------------------------
# TOOL 10 — log_decision (WRITE — called by orchestrator after human approval)
# ---------------------------------------------------------------------------
def log_decision(
    session_id: str,
    decision_payload_json: str,
    human_decision: str,
    human_decision_by: str,
    rejection_reason: str,
) -> dict:
    """Writes the final agentic decision to the writable decision log.

    Args:
        session_id: Session identifier.
        decision_payload_json: Full executor action card as JSON string.
        human_decision: approved | rejected | cancelled.
        human_decision_by: User ID who made the decision. Pass empty string if unknown.
        rejection_reason: Reason for rejection. Pass empty string if approved.
    """
    human_decision_by = human_decision_by or ""
    rejection_reason = rejection_reason or ""
    decision_id = str(uuid.uuid4())
    payload = _json.loads(decision_payload_json)
    action = payload.get("recommended_action", {}) or {}
    rc = payload.get("reasoning_chain", {}) or {}

    row = {
        "decision_id":          decision_id,
        "session_id":           session_id,
        "decision_timestamp":   datetime.now(timezone.utc).isoformat(),
        "trigger_type":         payload.get("trigger_type", "new_order"),
        "customer_kunnr":       action.get("customer_kunnr"),
        "customer_name":        action.get("customer_name"),
        "material_matnr":       action.get("material_matnr"),
        "material_name":        action.get("material_name"),
        "shipment_or_order_id": action.get("shipment_or_order_id"),
        "mabd":                 action.get("mabd"),
        "recommended_action":   action.get("action_type"),
        "origin_plant":         action.get("origin_plant"),
        "destination":          action.get("destination"),
        "quantity_cs":          action.get("quantity_cs"),
        "carrier_mode":         action.get("carrier_mode"),
        "estimated_freight_cost_usd": action.get("estimated_freight_cost_usd"),
        "avoided_fine_usd":     action.get("avoided_fine_usd"),
        "net_value_usd":        action.get("net_value_usd"),
        "human_decision":       human_decision,
        "human_decision_at":    datetime.now(timezone.utc).isoformat(),
        "human_decision_by":    human_decision_by,
        "rejection_reason":     rejection_reason,
        "watchdog_final_round":  rc.get("watchdog_final_round"),
        "economist_final_round": rc.get("economist_final_round"),
        "convergence_round":     rc.get("convergence_round"),
        "was_deadlocked":        payload.get("status") == "DEADLOCK",
        "reasoning_summary_json": decision_payload_json,
        "watchdog_confidence":   payload.get("watchdog_confidence"),
        "economist_confidence":  payload.get("economist_confidence"),
        "agent_model_versions":  payload.get(
            "agent_model_versions", "gemini-2.5-pro,gemini-2.5-flash"),
        "orchestrator_version":  payload.get("orchestrator_version", "0.1.0"),
    }
    table_ref = f"{DECISIONS_DS}.fct_allocation_decisions"
    errors = _bq.insert_rows_json(table_ref, [row])
    if errors:
        return {"error": str(errors)}
    return {"decision_id": decision_id,
            "inserted_at": row["decision_timestamp"],
            "table": table_ref}


# ---------------------------------------------------------------------------
# Tool registry — what ADK agents see
# ---------------------------------------------------------------------------
ALL_TOOLS = {
    "get_otif_performance":         FunctionTool(func=get_otif_performance),
    "get_cfr_weekly":               FunctionTool(func=get_cfr_weekly),
    "get_inventory_positions":      FunctionTool(func=get_inventory_positions),
    "get_shelf_life_risk":          FunctionTool(func=get_shelf_life_risk),
    "get_chargeback_risk":          FunctionTool(func=get_chargeback_risk),
    "get_transfer_cost_comparison": FunctionTool(func=get_transfer_cost_comparison),
    "get_forecast_accuracy":        FunctionTool(func=get_forecast_accuracy),
    "get_allocation_history":       FunctionTool(func=get_allocation_history),
    "get_active_alerts":            FunctionTool(func=get_active_alerts),
    "log_decision":                 FunctionTool(func=log_decision),
}

WATCHDOG_TOOLS = [
    ALL_TOOLS["get_otif_performance"],
    ALL_TOOLS["get_cfr_weekly"],
    ALL_TOOLS["get_inventory_positions"],
    ALL_TOOLS["get_shelf_life_risk"],
    ALL_TOOLS["get_active_alerts"],
]

ECONOMIST_TOOLS = [
    ALL_TOOLS["get_chargeback_risk"],
    ALL_TOOLS["get_transfer_cost_comparison"],
    ALL_TOOLS["get_forecast_accuracy"],
    ALL_TOOLS["get_allocation_history"],
    ALL_TOOLS["get_otif_performance"],
]

EXECUTOR_TOOLS = [
    ALL_TOOLS["get_allocation_history"],
    # log_decision is NOT here on purpose — orchestrator calls it after human approval.
]

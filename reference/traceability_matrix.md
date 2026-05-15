# Traceability Matrix — Agent → Tool → Semantic View → Upstream Source

Every BigQuery read or write the agents perform traces from agent name → tool function → `tiger_semantic.*` view (or `tiger_decisions.*` for writes) → bronze table → upstream system. This file is the canonical reference. Auditors should be able to follow any row in `tiger_decisions.fct_allocation_decisions` back to source.

## At-a-glance

| Agent | Tools bound | Source dataset |
|---|---|---|
| Watchdog | 5 read tools (OTIF, CFR, inventory, shelf life, alerts) | `tiger_semantic` only |
| Economist | 5 read tools (chargebacks, transfer cost, forecast accuracy, allocation history, OTIF) | `tiger_semantic` and `tiger_decisions` (history only) |
| Executor | 1 read tool (allocation history) | `tiger_semantic` and `tiger_decisions` |
| Orchestrator | 1 write tool (`log_decision`) called after approval | `tiger_decisions` only |

No agent has direct access to `tiger_foods_raw`. The IAM policy in `infra/iam.sh` enforces this.

## Per-tool detail

### Tool 1 — `get_otif_performance` (Watchdog, Economist)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_otif_performance(customer_kunnr, start_date, end_date, group_by)` | One parameterized SQL job |
| Semantic view | `tiger_semantic.fct_otif` | Grain: shipment-line. Computes `is_otif` flag and `otif_fine_usd`. |
| Semantic view (optional aggregate) | `tiger_semantic.agg_otif_customer_quarter` | Grain: customer × quarter. Pre-computed. |
| Bronze tables | `tiger_foods_raw.sap_vbap`, `sap_likp`, `sap_lips`, `sap_vbfa` | SAP sales & delivery documents |
| Upstream source | SAP ECC | Daily replicate via Datastream |

### Tool 2 — `get_cfr_weekly` (Watchdog)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_cfr_weekly(weeks_back, customer_kunnr)` | Trailing CFR by ISO week |
| Semantic view | `tiger_semantic.agg_cfr_weekly` | Grain: customer × ISO week |
| Bronze tables | `tiger_foods_raw.sap_likp`, `sap_lips`, `sap_vbak`, `sap_vbap` | Orders and deliveries |
| Upstream source | SAP ECC | Daily |

### Tool 3 — `get_inventory_positions` (Watchdog)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_inventory_positions(plant, material_matnr, include_shelf_life)` | Current day stock |
| Semantic view | `tiger_semantic.fct_inventory_movements` | Grain: plant × material × date. Computes available = on_hand − committed. |
| Bronze tables | `tiger_foods_raw.sap_mseg`, `sap_mkpf`, `sap_mara`, `sap_marm` | SAP material movements |
| Upstream source | SAP ECC | Hourly replicate (warehouses move fast) |

### Tool 4 — `get_shelf_life_risk` (Watchdog)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_shelf_life_risk(customer_kunnr, material_matnr, horizon_days)` | Cross-join inventory with customer MRSL |
| Semantic views | `tiger_semantic.fct_inventory_movements` × `tiger_semantic.dim_customer` | Customer-specific MRSL in `dim_customer.mrsl_days_required` |
| Bronze tables | `tiger_foods_raw.sap_mseg`, `sap_mara`, `sap_kna1`, `tiger_z_mrsl_rules` | Z-table for MRSL |
| Upstream source | SAP ECC + Tiger custom Z-tables | Daily |

### Tool 5 — `get_chargeback_risk` (Economist)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_chargeback_risk(customer_kunnr, shipment_date, fine_rate_override)` | Customer fine rate + 90d chargebacks |
| Semantic views | `tiger_semantic.dim_customer`, `tiger_semantic.fct_chargebacks` | |
| Bronze tables | `tiger_foods_raw.sap_kna1`, `tiger_z_chargebacks`, `tiger_z_otif_fine_schedule` | Z-tables for fine rates |
| Upstream source | SAP ECC + Tiger custom Z-tables | Weekly |

### Tool 6 — `get_transfer_cost_comparison` (Economist)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_transfer_cost_comparison(origin_plant, destination_region, material_matnr, quantity_cs)` | Lane freight rate × quantity |
| Semantic view | `tiger_semantic.fct_delivery` | Includes `freight_cost_usd`, `transit_days` |
| Bronze tables | `tiger_foods_raw.sap_likp`, `sap_vfkk`, `sap_vfkp` | Freight billing |
| Upstream source | SAP ECC | Daily |

### Tool 7 — `get_forecast_accuracy` (Economist)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_forecast_accuracy(customer_kunnr, material_matnr, lag_weeks)` | MAPE and bias at a given lag |
| Semantic view | `tiger_semantic.fct_forecast_accuracy` | Joins forecast snapshots to actuals |
| Bronze tables | `tiger_foods_raw.anaplan_forecast_snapshot`, `sap_likp` | |
| Upstream source | Anaplan + SAP ECC | Weekly forecast snapshots |

### Tool 8 — `get_allocation_history` (Economist, Executor)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_allocation_history(customer_kunnr, material_matnr, lookback_days)` | UNIONs historical + agentic decisions |
| Semantic view | `tiger_semantic.fct_allocation_decisions` | Historical (SAP-recorded) |
| Decisions table | `tiger_decisions.fct_allocation_decisions` | This system's own decisions |
| Bronze tables | `tiger_foods_raw.tiger_z_allocation_decisions` | Z-table from SAP, ~3 years history |
| Upstream source | SAP ECC Z-tables + this system | Daily |

### Tool 9 — `get_active_alerts` (Watchdog)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `get_active_alerts(severity_min, limit)` | Currently at-risk shipments by exposure |
| Semantic view | `tiger_semantic.fct_otif` | Filter `is_at_risk = TRUE` |
| Bronze tables | (same as Tool 1) | |
| Upstream source | SAP ECC | Daily |

### Tool 10 — `log_decision` (Orchestrator only)

| Layer | Resource | Notes |
|---|---|---|
| Tool | `log_decision(session_id, decision_payload_json, human_decision, ...)` | One INSERT per approval/rejection |
| Decisions table | `tiger_decisions.fct_allocation_decisions` | Partitioned by date, clustered by customer + decision |
| Bronze table | None — this is the source. The agentic system IS the upstream system for this row. | |

## Read/write boundary summary

| Dataset | Read | Write | Reachable from agents? |
|---|---|---|---|
| `tiger_foods_raw` | ❌ | ❌ | No — IAM denies |
| `tiger_semantic` | ✅ (9 read tools) | ❌ | Yes, read only |
| `tiger_decisions` | ✅ (Tool 8 UNIONs) | ✅ (Tool 10 only) | Read yes, write only from orchestrator after human approval |
| Firestore `agent_sessions` | (run log) | (run log) | Orchestrator only |

## Verifying the boundary

After IAM is applied, this command should return no access:

```bash
bq show --format=prettyjson resilience-riskradar:tiger_foods_raw \
  | grep -A1 "tiger-agents-sa" \
  || echo "PASS: no agent access to tiger_foods_raw"
```

If the grep prints anything, the IAM script (`infra/iam.sh`) must not be granting access to bronze. Fix and re-run.

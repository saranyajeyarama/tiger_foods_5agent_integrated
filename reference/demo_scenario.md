# Demo Scenario — Walmart Shipment 89921

Standalone runbook for the Phase 1 reference demo. Full technical version is in `briefs/02_architect_technical_requirements.md` section Q9. This file is what the demo operator needs in hand.

## Scenario at a glance

- **Customer:** Walmart Stores Inc, KUNNR `0001000245`
- **SKU:** Pedigree Choice Cuts Beef 36ct, MATNR `000000000010054321`
- **Shipment:** 89921, 1200 cases, MABD 2026-05-19
- **Problem:** DC-03 Jacksonville (the assigned origin) is short by 900 cases
- **Twist:** DC-01 Chicago has inventory but it is committed to a Target order
- **Resolution:** DC-04 Carlisle reroute (Watchdog and Economist converge in round 2)
- **Approve:** Planner clicks approve → decision logged to BigQuery

Total demo time: 4 minutes (3 minutes orchestration + 1 minute approval).

## Pre-demo verification — 5 minutes before

Run the verification SQL block. Each query must return the exact expected value.

```sql
-- (1) Walmart in dim_customer with fine rate
SELECT customer_kunnr, customer_name, otif_fine_rate_usd_per_cs, mrsl_days_required
FROM `resilience-riskradar.tiger_semantic.dim_customer`
WHERE customer_kunnr = '0001000245';
-- Expected: ('0001000245', 'Walmart Stores Inc', 18.75, 60)

-- (2) Pedigree Choice Cuts Beef 36ct in dim_material
SELECT material_matnr, material_name, brand, case_pack_qty, total_shelf_life_days
FROM `resilience-riskradar.tiger_semantic.dim_material`
WHERE material_matnr = '000000000010054321';
-- Expected: ('000000000010054321', 'Pedigree Choice Cuts Beef 36ct', 'Pedigree', 36, 540)

-- (3) Inventory positions at DC-01, DC-03, DC-04
SELECT plant, qty_on_hand_cs, qty_committed_cs,
       qty_on_hand_cs - qty_committed_cs AS qty_available_cs
FROM `resilience-riskradar.tiger_semantic.fct_inventory_movements`
WHERE material_matnr = '000000000010054321'
  AND plant IN ('DC-01', 'DC-02', 'DC-03', 'DC-04')
  AND movement_date = CURRENT_DATE()
ORDER BY plant;
-- Expected:
--   DC-01: on_hand 2400, committed 1500, available  900   (1500 is Target's commitment)
--   DC-02: on_hand 2100, committed    0, available 2100   (the backfill source)
--   DC-03: on_hand  300, committed    0, available  300   (the demo-causing shortfall)
--   DC-04: on_hand 1800, committed    0, available 1800   (the resolution)

-- (4) Walmart shipment 89921 at risk in fct_otif
SELECT shipment_id, customer_kunnr, material_matnr, qty_at_risk_cs,
       mabd, otif_fine_exposure_usd, origin_plant
FROM `resilience-riskradar.tiger_semantic.fct_otif`
WHERE shipment_id = '0089921001';
-- Expected:
--   ('0089921001', '0001000245', '000000000010054321', 1200,
--    '2026-05-19', 22500.00, 'DC-03')

-- (5) Conflicting Target PO #44019 on DC-01
SELECT order_id, customer_kunnr, material_matnr, confirmed_qty_cs,
       requested_delivery_date
FROM `resilience-riskradar.tiger_semantic.fct_sales_orders`
WHERE order_id = '0000044019';
-- Expected: ('0000044019', '0001000311', '000000000010054321', 1500, '2026-05-21')
```

If any query is off, run the seed script (`seed_demo_state.sql`, kept alongside this file).

## Triggering the session

```bash
SERVICE_URL="$(gcloud run services describe tiger-agents-orchestrator \
    --region=us-central1 --format='value(status.url)')"

curl -X POST "${SERVICE_URL}/sessions" \
  -H "Content-Type: application/json" \
  -d '{
    "trigger_type": "new_order",
    "trigger_payload": {
      "shipment_id": "0089921001",
      "customer_kunnr": "0001000245",
      "customer_name": "Walmart Stores Inc",
      "material_matnr": "000000000010054321",
      "qty_cs": 1200,
      "ship_to": "Walmart DC Florida",
      "mabd": "2026-05-19",
      "default_origin_plant": "DC-03"
    }
  }'
```

Response (immediate):

```json
{ "session_id": "session_20260512_143218_a3f9b1", "status": "active" }
```

Open the UI to that session ID. Watch the run log fill in.

## What the planner sees, beat by beat

### 0:00 — Watchdog round 1

> **Watchdog** [round 1] – Detected OTIF breach risk for Walmart shipment 89921. DC-03 short by 900 CS; DC-01 has 2400 CS available. Recommending reroute to DC-01 via LTL to avoid the $22,500 fine. Confidence 88%.

(Behind the scenes: 3 tool calls — `get_inventory_positions` × 2, `get_otif_performance`. Latency ~5–7 seconds.)

### 0:08 — Economist round 1

> **Economist** [round 1] – Challenging Watchdog. DC-01 inventory is committed to Target PO #44019 (1500 CS @ $9.67/CS = $14,505 exposure). DC-04 Carlisle is $2,100 more on freight ($11,200 vs $9,100) but avoids the Target conflict. **Recommend DC-04.** Net cost delta: -$12,400 in favor of DC-04. Confidence 91%.

(4 tool calls — `get_transfer_cost_comparison` × 2, `get_chargeback_risk` × 2.)

### 0:18 — Orchestrator routes back

> Orchestrator: Economist challenged. Routing back to Watchdog for round 2.

### 0:19 — Watchdog round 2

> **Watchdog** [round 2] – Accepting Economist's Target conflict point but flagging carrier buffer concern for DC-04 (1.5d vs DC-01 2.0d). Holding pending DC-02 verification for Target.

### 0:25 — Economist round 2 (convergence)

> **Economist** [round 2] – Verified DC-02 has 2,100 CS free — Target backfill confirmed. DC-04 lane historical on-time 96%. Agreeing on DC-04 to Walmart, DC-02 to Target. **Converged.**

### 0:30 — Orchestrator routes to Executor

### 0:35 — Executor produces approval card

Card content:

| Field | Value |
|---|---|
| Action | REROUTE — DC-04 Carlisle → Walmart DC Florida |
| Customer | Walmart Stores Inc (0001000245) |
| SKU | Pedigree Choice Cuts Beef 36ct |
| Quantity | 1,200 cases |
| MABD | 2026-05-19 |
| Freight | $11,200 |
| Avoided fine | $22,500 |
| Net value | $11,300 |
| Precedent | 14 similar reroutes in last 90 days, 93% approved |

### 0:40+ — Planner reviews and approves

```bash
curl -X POST "${SERVICE_URL}/sessions/${SESSION_ID}/approve" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo@mars.com"}'
```

## Confirmation query

```sql
SELECT decision_id, customer_name, recommended_action, origin_plant,
       quantity_cs, net_value_usd, human_decision, human_decision_by,
       convergence_round
FROM `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
WHERE session_id = '<the session id you used>';
```

Expected: 1 row with `recommended_action="REROUTE"`, `origin_plant="DC-04"`, `net_value_usd=11300.00`, `human_decision="approved"`, `convergence_round=2`.

## Reset between demos

Run this between demos if you need clean Firestore state. The BigQuery row stays — that's a real audit record.

```bash
# Optional: clear test session from Firestore
gcloud firestore documents delete \
  --recursive agent_sessions/${SESSION_ID} \
  --quiet

# Optional: clear test decisions from BigQuery
bq query --use_legacy_sql=false \
  "DELETE FROM \`resilience-riskradar.tiger_decisions.fct_allocation_decisions\`
   WHERE session_id = '${SESSION_ID}'"
```

## If the demo goes off the rails

| Symptom | Likely cause | Fix |
|---|---|---|
| Watchdog never appears | Cloud Run cold start | `--min-instances 1` for demos; retry |
| Watchdog recommends a different DC | Data state changed | Re-run verification queries; reseed if needed |
| Economist agrees in round 1 (no debate) | DC-01 inventory not actually committed | Confirm `fct_sales_orders` query #5 above |
| Approval card shows wrong customer | Trigger payload typo | Check KUNNR in your curl |
| 500 error from /approve | Session not yet in `awaiting_approval` | Wait for Executor; retry |

## Why this demo holds up to scrutiny

When the Director or Mars stakeholder asks "Is the agent really thinking, or is this scripted?":

1. Show the BigQuery job IDs in Firestore — they correspond to actual queries against `tiger_semantic`. Run `bq show -j <bq_job_id>` to prove it.
2. Mutate one row in the upstream data (e.g., set DC-04 inventory to 0). Re-run the demo. The agents recommend a different DC.
3. Show the `agents/*.md` system prompts — there are no hard-coded numbers. The agents read the data and reason.
4. Pull the Firestore run log timestamps — agents take 5–12 seconds per response; that is Gemini inference time, not a canned reply.

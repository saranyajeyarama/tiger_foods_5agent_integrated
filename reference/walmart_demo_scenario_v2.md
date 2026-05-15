# Walmart Pedigree Dry 22lb — v2 Demo Scenario

**Replaces:** the v1 POC scenario (OTIF Shipment 89921). The Walmart Pedigree scenario is the canonical Mars-facing demo for the deck-aligned 5-agent build.

---

## The order

| Field | Value |
|---|---|
| Customer | Walmart Stores Inc. |
| Customer KUNNR | `0001000245` |
| SKU | Pedigree Dry 22lb |
| Material MATNR | `MAT-PDG-DOG-DRY-22LB` |
| Ordered quantity | **1,280 cs** |
| Demand plan for the week | 970 cs |
| Above-forecast | **+32%** |
| MABD | 2026-05-21 |
| Ship-to | Walmart DC NE-12 (US northeast) |
| Customer MRSL requirement | 60 days remaining shelf life on receipt |
| Customer OTIF target | 98.0% |
| Walmart trailing-90d OTIF | 94.2% (below target) |

This is the exact scenario committed to in the Sambath / Mars deck. The order is real-looking: it's above plan, but not so far above that it's obviously wrong; and Walmart is the highest-stakes account in the book.

## What the 5 agents do

### Customer Supply Agent

1. Receives the order, classifies as ABOVE_FORECAST (+32%).
2. Calls `get_customer_compliance_rules(0001000245)` → OTIF fine rate $18.75/cs, MRSL 60 days.
3. Fires the 4 specialists **in parallel**:

### Supply Planning Agent
- Calls `get_finished_goods_inventory(MAT-PDG-DOG-DRY-22LB)` → total on-hand 1,450 cs across DC-04 (800) and DC-01 (650).
- Applies FEFO + MRSL filter → usable 1,180 cs (short by 100 cs vs ordered 1,280).
- Calls `get_production_orders(MAT-PDG-DOG-DRY-22LB, horizon_days=14)` → `PO-RUN-2026-05221` scheduled 2026-05-18 is **ON_HOLD** pending raw chicken meal QA release.
- Disposition: **CAUTION**, confidence 0.72, hard_block: false.

### Demand Planning Agent
- Calls `classify_order_vs_forecast` → confirms +32%.
- Calls `get_order_history(0001000245, MAT-PDG-DOG-DRY-22LB, lookback_weeks=12)` → 1 prior above-plan event (+18% week 14), no recurring pattern.
- Calls `get_forecast_accuracy` → trailing MAPE 12.4%, bias -1.8%, plan quality HEALTHY.
- Calls `get_promotional_calendar` → **v2 stub**, returns data_available=false.
- Calls `get_retail_velocity` → **v2 stub**, returns data_available=false.
- Classification: ONE_OFF_ANOMALY (best read absent retail data).
- Disposition: **CAUTION**, confidence 0.78. Escalation recommended to demand team.

### Transportation Agent
- Calls `get_otif_performance(0001000245)` → Walmart trailing 90d OTIF 94.2% (3.8 points below 98% target).
- Evaluates primary lane DC-04 → US-NE-WALMART: transit 2.5 days, buffer 1.5 days to MABD, carrier OTP **92.4%**.
- Alternative lane DC-01: transit 2.0 days, carrier OTP 94.8%, viable.
- Fine exposure: full-quantity miss $24,000; partial $14,400.
- Disposition: **CAUTION**, confidence 0.81, hard_block: false.

### Retail Intelligence Agent
- Calls `get_customer_compliance_rules` → MRSL 60 days.
- Calls `get_shelf_life_risk` → 0 cs flagged non-compliant. MRSL satisfied.
- Calls `get_retail_dc_inventory` and `get_retail_store_inventory` → **v2 stubs**, data_available=false.
- Calls `get_retail_velocity` → **v2 stub**, data_available=false.
- Classification: INSUFFICIENT_DATA (v2 limitation, honest).
- Disposition: **CAUTION**, confidence 0.45. data_gaps explicitly listed.

## Customer Supply Agent's conflict detection

Applies the 3 rules:

| Rule | Triggered? | Why |
|---|---|---|
| R1 (hard_block) | No | None of the four set hard_block=true |
| R2 (PROCEED vs BLOCK divergence) | No | All four are CAUTION; no opposing dispositions |
| R3 (confidence asymmetry) | No | Retail Intelligence at 0.45 is the lowest, but it's on the same disposition (CAUTION) as the others — no opposing-disposition + asymmetry |

**No debate rounds needed.** Customer Supply Agent goes directly to synthesis.

## Customer Supply Agent's synthesis

Four CAUTION signals with no hard block point to a controlled partial fulfillment:

- Supply caps useful FG at 1,180 cs (FEFO + MRSL).
- Plan was 970 cs.
- Transportation can handle the in-plan 970 with comfortable buffer; commits beyond that increase fine exposure on a stretched lane.
- Retail Intelligence cannot confirm the +32% as genuine pull (v2 data gap), which is a reason to be conservative.
- Demand Planning's best read is ONE_OFF_ANOMALY — supporting the order in the plan-band is safe, supporting beyond the plan is speculative.

**Decision logic:** fulfill within the demand plan band, not beyond it. 970 is the plan; 768 = 970 × 0.79 covers most of the planned demand within transportation's comfortable buffer.

| Recommendation | PARTIAL_FULFILL |
| Fulfill qty | 768 cs (60% of 1,280) |
| Confidence | 0.87 |
| Expected outcome | Preserves OTIF on the planned portion. Avoids overcommit to the +32% above-plan portion that supply cannot reliably cover. Preserves an estimated **$12,400** in margin that would have been at risk on a forced full-quantity acceptance. |

**Escalations generated:**
- To Demand Planning Team: review demand plan accuracy on this SKU; +32% with no promo, no recurring pattern, no retail velocity confirmation.
- To Transportation Manager: optional — flag the marginal carrier OTP on the DC-04 lane.

## What the deck's dramatic moment looks like in v3

In v3, when retail data lands, the Retail Intelligence Agent's behavior changes:

- It calls `get_retail_dc_inventory(0001000245, MAT-PDG-DOG-DRY-22LB)` → returns aggregate_days_of_supply = 18 days (well above Walmart's typical 9-day target), dos_trend_4w = BUILDING. Then calls `get_retail_store_inventory` to check whether stores are running low.
- It calls `get_retail_velocity` → returns trailing 4w avg flat, trend_vs_prior_8w FLAT.
- Classifies: BUFFER_BUILD with confidence 0.91.
- Disposition flips to **BLOCK**, hard_block: true.

This triggers Customer Supply Agent's conflict detection rule R1 (hard_block) — Retail Intelligence vs the other three CAUTION specialists. Debate round runs. Other specialists HOLD their PROCEED/CAUTION (they had no retail data); Retail Intelligence HOLDs its BLOCK with the velocity and DOS evidence. Conflict DEADLOCK → surfaced to human with recommendation REJECT.

The deck's dramatic moment is intentional: the system flagged something the manual process would have missed. **In v2 it lands as PARTIAL_FULFILL with conservative confidence and a documented data gap. In v3 it lands as the full REJECT-with-buffer-build narrative.**

## How to run the demo

```bash
# Start a v2 session (5-agent flow)
curl -X POST https://<service-url>/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "trigger_type": "new_order",
    "trigger_payload": {
      "customer_kunnr": "0001000245",
      "customer_name": "Walmart Stores Inc",
      "material_matnr": "MAT-PDG-DOG-DRY-22LB",
      "material_name": "Pedigree Dry 22lb",
      "ordered_qty_cs": 1280,
      "mabd": "2026-05-21",
      "ship_to": "Walmart DC NE-12",
      "sales_order_id": "SO-WMT-2026-05-1280"
    }
  }'

# Get returned: { "session_id": "session_20260512_…", "status": "active", "flow_mode": "five_agent" }

# Poll for completion
curl https://<service-url>/sessions/session_20260512_…

# Status will move active → awaiting_approval. The final_action_card contains
# the Customer Supply Decision payload.

# Approve
curl -X POST https://<service-url>/sessions/<id>/approve \
  -H "Content-Type: application/json" \
  -d '{"user_id":"planner.walmart","approval_notes":"Partial fulfill aligned with plan"}'

# Or reject
curl -X POST https://<service-url>/sessions/<id>/reject \
  -H "Content-Type: application/json" \
  -d '{"user_id":"planner.walmart","rejection_reason":"Will accept full quantity; talked to Walmart buyer"}'

# Decision lands in BigQuery
bq query --use_legacy_sql=false \
"SELECT decision_id, flow_mode, agent_recommendation, agent_confidence_score,
        user_decision, decision_aligned_with_agent, cdm_domains_referenced,
        scenario_tag
 FROM \`resilience-riskradar.tiger_decisions.fct_allocation_decisions\`
 ORDER BY decision_timestamp DESC LIMIT 5"
```

## Expected demo run characteristics

| Metric | Target |
|---|---|
| End-to-end latency (PO → recommendation) | < 8 seconds |
| Parallel specialist fan-out latency | < 4 seconds |
| Number of BigQuery jobs | 12–18 (3–5 per specialist) |
| Debate rounds triggered | 0 in v2 (no conflicts); 1–2 expected in v3 |
| Firestore steps written | 25–35 |
| Recommendation confidence | 0.85–0.90 |
| DCE row written on approval | 1 |

## What this scenario proves

1. **N-to-N parallel orchestration works** — 4 specialists run concurrently, not sequentially.
2. **Conflict detection works** — applies the 3 rules deterministically; surfaces conflicts to the orchestrator transparently.
3. **The CDM-domain coverage is real** — the agents read from Sales & Orders, Inventory, Supply & Production, Procurement, and Master Data CDM domains (Retail Signals is the v3 gap).
4. **The DCE captures everything** — agent recommendation, agent confidence, human decision, alignment flag, CDM domains referenced, all in one row of fct_allocation_decisions.
5. **The retail data gap is honest and contained** — Retail Intelligence Agent says "INSUFFICIENT_DATA"; no fabrication. v3 brings it online without code change.

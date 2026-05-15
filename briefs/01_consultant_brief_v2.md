# Consultant Brief — Tiger Foods Agentic AI v2

**For:** persona owners, consultants, planners who'll demo or use this
**Date:** May 2026
**Companion docs:** `reference/walmart_demo_scenario_v2.md` for the live demo script

---

## What you need to understand to walk through this

The 5-agent architecture handles each customer purchase order as a small, structured problem with four interlocking dimensions:

| Dimension | Owned by | Asks |
|---|---|---|
| Can we supply this? | Supply Planning Agent | Do we have FG? Is production on track? Are RM lots adequate? Is safety stock OK? |
| Is this real demand? | Demand Planning Agent | Above plan? If yes, is it a genuine spike or noise? Is the forecast itself bad? |
| Can we deliver on time? | Transportation Agent | Is the lane viable? Is the carrier reliable? What's the fine exposure on a miss? |
| Is this the consumer pulling, or the retailer buffering? | Retail Intelligence Agent | What does retail inventory look like? Is POS velocity up or flat? (v2 limitation: this dimension is degraded until retail data lands) |

A fifth agent — the **Customer Supply Agent** — receives the order, fires the four specialists **in parallel**, and synthesizes their structured signals into a single recommendation card for the planner. If two specialists disagree, the Customer Supply Agent fires a structured debate round between them before synthesizing.

The recommendation card is one of: ACCEPT, REJECT, PARTIAL_FULFILL, DEFER. The planner approves or rejects. Every approval / rejection writes a row to the Decision Capture Engine (DCE) in BigQuery, which becomes the training corpus for Phase 2 closed-loop work.

## Why parallel matters

The deck commits to N-to-N parallel orchestration. This is not just a performance choice — it's a correctness choice.

If you ran the specialists sequentially you'd implicitly privilege the first one's framing. Supply Planning going first would tilt the system toward "what's our FG position?" Transportation going first would tilt it toward "can we ship?" Running in parallel means each specialist independently calls its tools, reasons about its domain, and produces a signal. Conflicts surface explicitly rather than being smoothed away by sequential reasoning. The orchestration layer then handles conflicts deterministically (specific rules, not LLM judgment) and only invokes specialist-to-specialist debate when there's a real contradiction to resolve.

## What a planner sees

```json
{
  "agent": "customer_supply",
  "session_id": "session_20260521_…",
  "order": {
    "customer_name": "Walmart Stores Inc",
    "material_name": "Pedigree Dry 22lb",
    "ordered_qty_cs": 1280,
    "demand_plan_qty_cs": 970,
    "forecast_classification": "ABOVE_FORECAST",
    "above_forecast_pct": 32.0,
    "mabd": "2026-05-21"
  },
  "specialist_signals": { … },          // the 4 specialist outputs
  "conflicts_detected": [],
  "recommendation": {
    "action": "PARTIAL_FULFILL",
    "fulfill_qty_cs": 768,
    "partial_fill_pct": 60.0,
    "confidence": 0.87,
    "expected_outcome": "Partial fulfillment of 768 cs preserves OTIF…"
  },
  "reasoning_chain": {
    "which_specialists_drove_decision": ["supply_planning", "transportation"],
    "key_trade_offs": [
      "FG usable after FEFO+MRSL is 1,180 cs; ordered 1,280",
      "Carrier OTP on primary lane 92.4% — below comfort threshold",
      "Retail demand validation unavailable (v2 data gap)"
    ],
    "what_would_change_the_decision":
      "Resolution of the on-hold production order PO-RUN-2026-05221 within MABD"
  },
  "escalations": {
    "to_demand_planning_team": {
      "summary": "Walmart Pedigree +32% above plan, no recurring pattern, no promo. Validate demand signal origin.",
      "severity": "MEDIUM",
      "recommended_action": "Trace order intent with Walmart account team"
    }
  }
}
```

The planner reads this and decides. The action card shows the recommendation, the reasoning chain (which specialists drove it, what the trade-offs were, and crucially — what would change the decision), and any escalations to send to other personas. The planner approves with optional notes, or rejects with a required reason.

## How to read each specialist's signal

Each specialist returns:

- `disposition`: PROCEED / CAUTION / BLOCK — the specialist's read on its domain
- `confidence`: 0–1, how certain the specialist is
- `hard_block`: bool, only true when the specialist sees a non-negotiable constraint
- `signal`: agent-specific evidence payload
- `evidence[]`: which tool was called, which view was queried, what data point was returned
- `reasoning_summary`: 2–3 sentences a planner can read

The `evidence[]` array is the audit trail. If a planner asks "why does Supply Planning say CAUTION?" — the answer is in those tool calls and view names.

## How the demo runs

See `reference/walmart_demo_scenario_v2.md` for the full script. In summary:

1. POST a PO payload to `/sessions`. Returns a session ID.
2. The session goes through 4 parallel specialist calls + a Customer Supply synthesis call, in 8–12 seconds.
3. GET `/sessions/{id}` to poll for `status: awaiting_approval`. The `final_action_card` is the planner-facing recommendation.
4. POST `/sessions/{id}/approve` or `/reject`.
5. A row lands in `fct_allocation_decisions` with all DCE columns populated.

The Firestore step log records every tool call and every model response — useful for debugging and for confidence-building demos ("here's exactly what each agent looked at and what it concluded").

## What to say about the retail data gap

The deck commits to Retail Intelligence Agent classifying orders as genuine consumer pull vs retailer buffer-build, with high confidence. v2 delivers the agent fully built but with the retail-side data not yet loaded. The agent honestly reports `INSUFFICIENT_DATA` rather than guessing.

This is the right way to handle it. Faking the classification with low-quality data would have made the demo look better and the production system worse. Stakeholders will ask. The answer: "the agent is wired; the data load is the single remaining item between us and the deck's full vision. The data load is a separate workstream (data engineering); when it lands, no agent or code change is required — see `retail_data_gap_v2.md` for the exact view contract."

## What the consultant team is being asked to do

1. Run the Walmart Pedigree demo with the Mars planning team. Walk through the action card. Show the Firestore step log. Show the DCE row landing in BigQuery on approval.
2. Identify the 1–2 highest-volume scenarios in the Mars Customer Supply pipeline where this 5-agent flow should run live. Walmart above-forecast events is the obvious one; the team should pick 1–2 more.
3. Coordinate with data engineering on the Retail Signals data load. The contract is documented; the load is the lift.
4. After 30 days of live use, look at the DCE alignment rate by scenario class. Bring back the data on which scenarios are candidates for Phase 2 auto-approval gating.

## What the consultant team should not over-claim

- This is intelligence, not automation. Humans approve every decision in Phase 1.
- Retail Intelligence is degraded in v2 (built but data-starved). Don't show buffer-build classification confidence numbers above 0.50 in v2 demos.
- The deck's $X fine reduction commitment is Phase 2 territory and depends on closed-loop measurement that takes 90 days of decisions to support.

## Getting started

```bash
# Apply the v2 schema extension (assumes v1 bootstrap already ran)
bash infra/bootstrap_integrated.sh

# Deploy the dual-mode service to Cloud Run
gcloud builds submit code/ \
  --config code/orchestrator_service/cloudbuild_v2.yaml

# Run the Walmart demo
curl -X POST <service-url>/sessions \
  -H "Content-Type: application/json" \
  -d @reference/walmart_pedigree_payload.json
```

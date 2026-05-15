# Director Brief — Tiger Foods Agentic AI v2

**For:** Mars Pet Nutrition leadership
**Date:** May 2026
**Status:** Phase 1 Intelligence Layer — ready for deployment

---

## What this is

The deck-aligned 5-agent build for Tiger Foods Customer Supply operations. Five domain-specialist agents work in parallel on each customer purchase order — Supply Planning, Demand Planning, Transportation, Retail Intelligence, and a Customer Supply Agent that synthesizes their signals into a single recommendation for the planner. The planner approves, and every recommendation-decision pair is captured in the Decision Capture Engine for the Phase 2 closed-loop training corpus.

This supersedes the 3-agent POC (Watchdog / Economist / Executor) which is preserved in the same Cloud Run service and reachable at a separate endpoint for backward compatibility.

## What changed from the POC

| | POC (v1) | This build (v2) |
|---|---|---|
| Agents | 3 (Watchdog, Economist, Executor) | 5 (Customer Supply, Supply Planning, Demand Planning, Transportation, Retail Intelligence) |
| Orchestration | Sequential debate (Watchdog → Economist → 3 rounds) | N-to-N parallel fan-out + debate-on-conflict |
| CDM coverage | Sales & Orders, Inventory, Master Data | All 6 deck-committed domains (adds Supply & Production, Procurement, Retail Signals) |
| Decision Capture Engine | Schema present; basic agent confidence | Full DCE: agent recommendation, agent confidence, human decision, alignment flag, CDM domain provenance, outcome columns for retrospective measurement |
| Demo scenario | OTIF Shipment 89921 | Walmart Pedigree Dry 22lb +32% above forecast |

## What v2 delivers today

- **5-agent parallel orchestration** end-to-end in ~8 seconds per decision
- **Deterministic conflict detection** across specialists (hard block, disposition divergence, confidence asymmetry) with structured debate when conflicts surface
- **Auditable provenance** for every recommendation — Firestore step log with every tool call and every view queried; BigQuery DCE row on every approval / rejection
- **Walmart Pedigree demo** ready to run

## What v2 honestly doesn't deliver yet

The deck's most dramatic moment — Retail Intelligence Agent flipping a recommendation by classifying an above-forecast order as inventory buffer-build rather than real consumer demand — requires retail-side data that's not yet in `tiger_semantic`. Specifically: retailer on-hand inventory, days-of-supply, and POS velocity.

In v2, the Retail Intelligence Agent runs and is fully wired, but reports `INSUFFICIENT_DATA` on the classification dimension. The contract for the v3 data load is documented down to the SQL view shape; no agent prompt change or code change is required when the data lands.

For the v2 Walmart demo, the recommendation is driven by the other three specialists and lands as a conservative PARTIAL_FULFILL (768 cs of 1,280 cs ordered, confidence 0.87, ~$12,400 margin preserved). The dramatic flip to a confident REJECT lands in v3.

## What the Decision Capture Engine gives you

Every decision lands as one row in `fct_allocation_decisions`. The row contains:

- The agent's recommendation (ACCEPT / REJECT / PARTIAL_FULFILL / DEFER) and the agent's confidence score
- The human's decision and whether it matched the agent
- Which CDM domains were read to produce the recommendation
- A free-text field for the human to record why they modified the recommendation, if they did
- Two outcome columns (CFR impact, fine avoided) populated retrospectively by a separate job at T+30 days

This is the training corpus for Phase 2 closed-loop work. After ~90 days you can ask: which scenarios is the agent consistently right on? Which is the human consistently right on? Where are they disagreeing and what is the cost? Those answers gate the move from "intelligence layer with human approval" to "automated action with human notification" — the deck's "Run" phase.

## What you decide

1. **Approve deployment to Cloud Run** so the demo runs against live Mars data with Mars planners.
2. **Sequence the Retail Signals data load.** It's the single highest-leverage data engineering effort to unlock the deck's full vision — without it, one of the 5 agents runs at reduced capability.
3. **Pick the first scenario class to track for Phase 2 alignment.** Walmart above-forecast events are the obvious first candidate; the Walmart Pedigree demo proves we can run it.

## What we will report back on

After 30 days of live use we'll report on:

- Number of decisions captured in DCE
- Alignment rate (% of decisions where the human approved exactly what the agent recommended)
- Disagreement patterns by scenario class and account
- Estimated fine exposure avoided (rough — formal closed-loop measurement is at T+90 days)

If alignment is high (>80%) on a scenario class, that scenario class is a candidate for auto-approval gating in Phase 2. If alignment is low, that's a signal for an agent prompt revision or a tool gap — equally valuable.

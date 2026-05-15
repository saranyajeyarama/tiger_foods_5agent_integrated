# Crawl-Walk-Run Roadmap — v2

Where this v2 build sits on the Mars / Sambath deck's roadmap, and what it enables next.

---

## Crawl — Phase 1 Intelligence Layer (this v2 build)

**Status:** built in this package; ready for Cloud Run deploy.

What it delivers:

- 5 domain-specialist agents reading from `tiger_semantic` CDM views
- Customer Supply Agent synthesizes; humans approve
- Decision Capture Engine records the agent recommendation, the human decision, and the alignment in BigQuery
- No write-back to operational systems — humans take the recommendation into SAP / their existing tools

Constraints accepted:

- Retail Signals CDM domain not yet loaded → Retail Intelligence Agent runs honestly degraded (INSUFFICIENT_DATA classification, low confidence). Other 4 agents fully operational.
- Human is in the loop on every decision. Latency is not the binding constraint; trust-building is.
- No SAP BAPI write-back; planner takes the recommendation to their existing UI.

Value captured in Phase 1:

- Manual triage time reduced (planner sees the synthesized story, not the raw data)
- Decisions are auditable end-to-end (Firestore step log + DCE row)
- Above-forecast events are classified consistently (Demand Planning Agent), not by ad-hoc judgment
- OTIF risk is quantified before commitment (Transportation Agent), not after the miss

---

## Walk — Retail Signals activation (v3, no code change required)

**Status:** awaiting data load.

What it adds:

- `fct_retail_dc_inventory` and `fct_retail_store_inventory` loaded into `tiger_semantic` (initial v3 release; velocity + promo deferred) per the contract in `retail_data_gap_v2.md`
- Retail Intelligence Agent's classification confidence climbs from 0.30–0.50 to 0.75–0.95
- The deck's dramatic moment lands: Retail Intelligence Agent can flip a recommendation by classifying an above-forecast order as BUFFER_BUILD with high confidence
- Demand Planning Agent's PROMO_DRIVEN classification becomes available
- Customer Supply Agent's conflict detection rules now fire on real retail signals (R1 hard_block from Retail Intelligence becomes a real path)

Why this is "Walk" and not "Run":

- Still no write-back — humans still approve
- Adds capability without changing the operating model

Source systems implicated:

- Retailer-Link (Walmart) or syndicated POS panel (Nielsen / Circana) for velocity
- Retailer inventory feeds (typically through EDI 852)
- Anaplan TPM for the promotional calendar

---

## Run — Phase 2: BAPI Write-back + Full System Integration

**Status:** Phase 2 scope; not in this v2 package.

What it adds:

### 2.1 BAPI write-back to SAP
- Approved decisions write back to SAP via BAPI (sales order modification, allocation adjustment)
- Eliminates the planner's manual step of re-keying the decision into SAP
- Auditability: BAPI call ID written to a new column in DCE for end-to-end traceability

### 2.2 SCDP (Supply Chain Demand Planning) activation
- Demand Planning Agent's `demand_team_escalation_recommended` flag triggers a workflow item in SCDP
- Demand planner sees the agent's classification alongside their normal demand plan review tools
- Closed loop: when the demand planner updates the plan, the system tracks whether the next above-forecast event for the same SKU is now within-forecast (validates the classification quality)

### 2.3 OMP (Order Management Platform) activation
- Customer Supply Agent's PARTIAL_FULFILL recommendations integrate directly into the order management workflow
- The planner sees the recommendation in OMP, not in a separate dashboard
- Lane viability and fine exposure visible at the point of decision

### 2.4 Anaplan integration (write-side)
- Approved demand-team escalations write to Anaplan for the next planning cycle
- Pattern: agent identifies SYSTEMATIC_UNDER_FORECAST → demand planner reviews → on approval, Anaplan demand model parameters update for the next cycle

### 2.5 TPM (Trade Promotion Management) integration
- Bidirectional: TPM reads as today (promo calendar feeds dim_promotion)
- New: when Customer Supply Agent recommends a PROMO_DRIVEN partial fulfill, the trade marketing team sees an alert in TPM linked to the promo (closed loop on promo execution effectiveness)

### 2.6 Closed-loop training of the alignment model
- After ~90 days of DCE data, train a model on `decision_aligned_with_agent` and `outcome_cfr_impact_cs` / `outcome_fine_avoided_usd`
- Output: where is the human consistently disagreeing with the agent, and was the human right? (signals an agent prompt or tool gap)
- Output: where is the human consistently agreeing with the agent and the outcome is good? (candidates for auto-approval flag — opt-in only)

### 2.7 Auto-approval gating (opt-in, for high-confidence + high-alignment scenarios only)
- Specific scenario classes that achieve >95% alignment over 90 days are eligible for auto-approval gating
- Planner sees notifications, not approval requests, on these
- This is the final move from intelligence layer to action layer — and only on the narrow slices where the data has earned it

---

## Why this sequence

The crawl-walk-run sequence is the deck's commitment for two reasons:

1. **Trust is built scenario by scenario.** The DCE records every disagreement between agent and human. After 90 days you have empirical evidence on which scenarios the agents are reliable on. Auto-approval requires that evidence; you cannot fake it.

2. **Data debt is structural.** Retail Signals is missing today; we built honestly around the gap rather than papering over it. When the data lands, the system already knows what to do with it (no agent prompt change, no orchestrator change, no code change — just data).

The deck's punchline: "Phase 1 demonstrates the architecture; Phase 2 captures the financial impact." This v2 build is the Phase 1 deliverable.

---

## What "done" looks like for each phase

| Phase | Done criterion |
|---|---|
| Phase 1 (this build) | Walmart Pedigree demo runs end-to-end in < 12s; DCE row lands with all v2 columns populated; planner uses the recommendation card 5+ times per day across 2+ accounts |
| Phase 1.5 / Walk | Retail Signals views loaded; Retail Intelligence Agent's classification confidence ≥ 0.75 on a documented test set; one BUFFER_BUILD case flipped a recommendation that a planner agreed with |
| Phase 2 / Run | BAPI write-back in production; closed-loop training model deployed; auto-approval enabled for ≥1 scenario class with >95% historical alignment; quarterly fine reduction quantified |

---

## Timeline (indicative)

| Period | Work |
|---|---|
| Weeks 0–2 | Deploy v2 to Cloud Run, run the Walmart demo with Mars stakeholders, gather feedback |
| Weeks 2–6 | Retail Signals data load (parallel track owned by data engineering); planner adoption across 2 accounts |
| Weeks 6–10 | Retail Signals activation; v3 demo with Mars |
| Weeks 10–16 | DCE corpus accumulation (target 200+ decisions); SCDP and OMP integration scoping |
| Weeks 16–24 | Phase 2 build: BAPI write-back, SCDP, OMP, Anaplan write-side |
| Weeks 24–32 | Closed-loop training model; auto-approval gating for first scenario class |

Timeline is indicative — depends on data engineering capacity for Retail Signals and on Mars' adoption pace.

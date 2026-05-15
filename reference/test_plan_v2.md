# Test Plan — v2

Acceptance criteria for the v2 5-agent build. Tests are organized as unit (each agent in isolation), integration (orchestrator flow), and end-to-end (full HTTP → BigQuery).

---

## T1. Unit: each agent returns a structured signal

### T1.1 Supply Planning Agent
**Setup:** mock the 6 tools to return canned responses for the Walmart Pedigree scenario.
**Run:** invoke `make_supply_planning_v2()` with the standard order payload.
**Pass:**
- Returns valid `SupplyPlanningSignal` Pydantic object (schema validates)
- `disposition` ∈ {PROCEED, CAUTION, BLOCK}
- `signal.fg_position.usable_after_fefo_mrsl_cs` is an int ≥ 0
- `evidence[]` contains at least 2 tool-call records with `view_queried` populated
- `reasoning_summary` is 1–3 sentences, non-empty

### T1.2 Demand Planning Agent
**Setup:** mock `classify_order_vs_forecast` to return above_forecast_pct=0.32; mock `get_retail_velocity` to return data_available=false (v2 behavior).
**Pass:**
- `signal.above_forecast_classification` ∈ {ONE_OFF_ANOMALY, INSUFFICIENT_DATA} (v2 expected)
- `signal.classification_confidence` ≤ 0.70 (no retail data to support higher confidence)
- `signal.forecast_accuracy_signal.plan_quality_flag` is set

### T1.3 Transportation Agent
**Pass:**
- `signal.primary_lane.viable` is a bool
- `signal.fine_and_fee_exposure.exposure_if_miss_full_qty_usd` is a float ≥ 0
- `signal.customer_otif_position.trailing_90d_otif_pct` matches mocked OTIF tool output

### T1.4 Retail Intelligence Agent (v2 — INSUFFICIENT_DATA path)
**Setup:** the v2 stubs for retail tools (no real view exists).
**Pass:**
- `signal.pull_vs_buffer_classification` = "INSUFFICIENT_DATA"
- `signal.classification_confidence` ≤ 0.50
- `signal.data_gaps[]` contains at least one entry mentioning v3 / retail
- `disposition` = "CAUTION", `hard_block` = false
- `signal.mrsl_compliance.fg_satisfies_mrsl` evaluates correctly against mocked shelf-life data

### T1.5 Customer Supply Agent — synthesis
**Setup:** pass the 4 specialist signals as input.
**Pass:**
- Returns valid `CustomerSupplyDecision`
- `recommendation.action` ∈ {ACCEPT, REJECT, PARTIAL_FULFILL, DEFER}
- `recommendation.confidence` ∈ [0, 1]
- `reasoning_chain.which_specialists_drove_decision` is non-empty
- `dce_payload.cdm_domains_referenced` is non-empty

---

## T2. Unit: deterministic conflict detection

### T2.1 No conflicts case (Walmart scenario in v2)
**Input:** 4 specialist signals, all CAUTION, no hard_blocks, confidences 0.45–0.81.
**Run:** `orchestrator_v2._detect_conflicts(signals)`.
**Pass:** returns empty list.

### T2.2 Hard block conflict (R1)
**Input:** Retail Intelligence sets hard_block=true and disposition=BLOCK; Supply Planning is PROCEED.
**Pass:** returns list with one Conflict of type=HARD_BLOCK; disputants={retail_intelligence, supply_planning}.

### T2.3 Disposition divergence (R2)
**Input:** Supply Planning = PROCEED, Transportation = BLOCK.
**Pass:** returns Conflict type=DISPOSITION_DIVERGENCE; both PROCEED and BLOCK agents in disputants.

### T2.4 Confidence asymmetry (R3)
**Input:** Demand Planning = CAUTION confidence 0.92; Retail Intelligence = PROCEED confidence 0.42.
**Pass:** returns Conflict type=CONFIDENCE_ASYMMETRY.

### T2.5 De-duplication
**Input:** trigger both R1 and R2 with the same disputant pair (e.g., Retail BLOCK with hard_block + Supply PROCEED).
**Pass:** returns exactly one Conflict (the HARD_BLOCK; R2 should not duplicate).

---

## T3. Integration: orchestrator parallel fan-out

### T3.1 Specialists fired in parallel
**Setup:** wrap each `_invoke_specialist` in a timer probe that emits start/end timestamps. Run `run_session_v2()` on the Walmart payload.
**Pass:**
- 4 specialist invocations have overlapping time ranges (their `start_times` differ by < 200 ms)
- Total wall-clock time for the fan-out phase < (sum of individual specialist latencies) − 1500 ms

### T3.2 Firestore writes
**Pass:**
- Firestore step log contains: 1 `orchestrator` route step starting the session; 4 specialist `tool_call` + `response` step groups; 1 `orchestrator` route step "Synthesizing"; 1 customer_supply `response` step
- Each tool_call step has `view_queried` populated
- Step order is monotonic by `created_at`

### T3.3 Debate round trigger (R2 conflict path)
**Setup:** force Supply Planning to return PROCEED and Transportation to return BLOCK (hard_block=true). Run `run_session_v2`.
**Pass:**
- `conflicts_detected[]` in final decision has at least one entry
- Firestore has at least one step with `notes` containing "Debate round 2"
- `conflicts_detected[0].debate_rounds_used` ≤ MAX_DEBATE_ROUNDS (2)
- `conflicts_detected[0].resolution` ∈ {RESOLVED, DEADLOCK}

### T3.4 Deadlock surfacing
**Setup:** force both disputants to HOLD across 2 debate rounds.
**Pass:**
- `conflicts_detected[0].resolution` = "DEADLOCK"
- `recommendation.expected_outcome` mentions the deadlock explicitly
- Decision is still produced (orchestrator does not hang on deadlock)

---

## T4. End-to-end: HTTP → BigQuery

### T4.1 Start session via HTTP — 5-agent flow
**Run:**
```bash
curl -X POST http://localhost:8080/sessions \
  -H "Content-Type: application/json" \
  -d @walmart_pedigree_payload.json
```
**Pass:** returns `{"session_id": "...", "status": "active", "flow_mode": "five_agent"}` with HTTP 200.

### T4.2 Poll session to awaiting_approval
**Pass:** within 15 seconds, GET /sessions/{id} returns status="awaiting_approval" with non-null `final_action_card`.

### T4.3 Approve and DCE write
**Run:** POST /sessions/{id}/approve. Then query BigQuery.
**Pass:**
```sql
SELECT
  flow_mode,
  agent_recommendation,
  agent_confidence_score,
  user_decision,
  decision_aligned_with_agent,
  ARRAY_LENGTH(cdm_domains_referenced) AS domain_count
FROM `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
WHERE session_id = '<session_id>'
```
- Returns exactly 1 row
- `flow_mode` = "five_agent"
- `agent_recommendation` matches the action in the action card
- `user_decision` = "approved"
- `decision_aligned_with_agent` = TRUE
- `domain_count` ≥ 3 (Sales & Orders, Inventory, Master Data are guaranteed; Supply & Production added by Supply Planning)

### T4.4 Reject path
**Run:** start a separate session, then POST /sessions/{id}/reject with a reason.
**Pass:**
- BigQuery row has `user_decision` = "rejected"
- `decision_aligned_with_agent` = FALSE
- `rejection_reason` is populated

### T4.5 POC endpoint still works (backward compat)
**Run:** POST /sessions/poc with the v1 OTIF Shipment 89921 payload.
**Pass:**
- Returns `flow_mode: "poc"`
- Watchdog/Economist/Executor sequential debate runs (per v1 orchestrator)
- BigQuery row has `flow_mode` = "poc"
- v1 columns (watchdog_final_round, economist_final_round, convergence_round) are populated
- v2 columns are NULL (or pass-through)

---

## T5. Behavioral acceptance criteria — Walmart Pedigree scenario in v2

These are the criteria the live demo must hit. From `walmart_demo_scenario_v2.md`:

| Behavior | Expected |
|---|---|
| `forecast_classification` | ABOVE_FORECAST |
| `above_forecast_pct` | 0.30–0.34 |
| Supply Planning disposition | CAUTION |
| Demand Planning disposition | CAUTION |
| Transportation disposition | CAUTION |
| Retail Intelligence disposition | CAUTION |
| Retail Intelligence classification | INSUFFICIENT_DATA |
| Retail Intelligence confidence | 0.30–0.50 |
| Conflicts detected | 0 (all 4 are CAUTION; no opposing dispositions) |
| Debate rounds run | 0 |
| Recommendation action | PARTIAL_FULFILL |
| `recommendation.fulfill_qty_cs` | 700–820 (around 768) |
| `recommendation.confidence` | 0.83–0.90 |
| Demand-team escalation present | TRUE |
| `dce_payload.scenario_tag` | "WALMART_PEDIGREE_ABOVE_FORECAST" or similar |
| Wall-clock latency (PO → awaiting_approval) | < 12 seconds (target < 8) |

---

## T6. Retail data gap regression — v3 readiness

Validates the system is ready to consume retail data when it lands.

### T6.1 Tool auto-detection
**Run:** create a placeholder `fct_retail_dc_inventory` view in `tiger_semantic` with the schema in `retail_data_gap_v2.md` but no rows. Call `get_retail_dc_inventory`.
**Pass:** the function returns `data_available: false` because the view has 0 rows for the customer × SKU pair, but **NOT** `v3_pending: true` — because the view exists. (This proves `_retail_view_exists()` flipped from False to True.)

### T6.2 Live data path
**Run:** populate `fct_retail_dc_inventory` with 1 row for Walmart × Pedigree Dry 22lb with `days_of_supply=18, dos_trend_4w='BUILDING'`. Run the Walmart scenario.
**Pass:** Retail Intelligence Agent's signal shows `data_available: true`, `current_days_of_supply: 18`, `pull_vs_buffer_classification: BUFFER_BUILD`, confidence ≥ 0.75. Conflict detection fires.

### T6.3 No agent prompt or code change
**Verify:** between T6.1 and T6.2, no file in `/code/`, `/agents/`, or `/code/orchestrator_service/` has been modified.

---

## T7. Performance and observability

| Target | Threshold |
|---|---|
| 5-agent end-to-end latency | < 12 seconds (target < 8) |
| Parallel fan-out latency (4 specialists) | < 6 seconds (target < 4) |
| BigQuery total jobs per session | < 25 |
| Firestore writes per session | < 40 |
| Memory footprint per session | < 512 MB |
| Concurrent sessions on one Cloud Run instance | ≥ 10 |
| Cold-start latency | < 8 seconds |

---

## T8. Failure modes

### T8.1 Specialist agent fails / times out
**Setup:** make Supply Planning's tool calls raise an exception.
**Pass:**
- Orchestrator catches and records `_invoke_specialist`'s error envelope (disposition: CAUTION, confidence: 0.0)
- Customer Supply Agent receives the error envelope, produces a recommendation with reduced confidence, and flags `escalations.to_supply_planning_team`
- Session completes with status awaiting_approval; does not error out

### T8.2 Two or more specialists fail
**Pass:** Customer Supply Agent recommends DEFER with explicit reason; does not produce a hallucinated ACCEPT/REJECT

### T8.3 BigQuery DCE write fails
**Setup:** revoke `bigquery.dataEditor` from the service account temporarily.
**Pass:** `approve_session_v2` raises a clear RuntimeError; the session stays in awaiting_approval (not silently lost); Firestore logs the failure

### T8.4 Firestore unreachable
**Pass:** orchestrator returns HTTP 500 with a clear error; session is not created; the client can retry

---

## How to run the test suite

Tests are not yet checked in as code in this v2 deliverable. The test plan above defines acceptance criteria; the QA team implements them in pytest under `code/tests/test_v2_*.py` with mocks via `pytest-mock` for the BigQuery client and `httpx.AsyncClient` for the HTTP layer.

Minimum implementation for sign-off: T1.1–T1.5, T2.1–T2.5, T3.1, T4.1–T4.3, T5 (one full demo run end-to-end), T8.1.

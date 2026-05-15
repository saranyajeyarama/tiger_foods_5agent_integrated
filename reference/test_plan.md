# Test Plan — Phase 1 Acceptance

Standalone test plan. Long-form rationale is in `briefs/02_architect_technical_requirements.md` section Q10. This file is the engineer's checklist.

## Part A — Ten Binary Acceptance Criteria

The system is "agentic" only when ALL 10 pass. Partial credit does not exist.

| # | Criterion | Test method | Pass condition |
|---|---|---|---|
| 1 | Triggering event auto-invokes Watchdog | `POST /sessions` with demo payload; check Firestore | Watchdog response in Firestore within 10 seconds |
| 2 | Watchdog and Economist communicate via structured JSON | Inspect any step; validate against Pydantic schemas | 100% of agent steps pass `model_validate` |
| 3 | Economist disagreement is visible in run log | Run demo; observe UI | Visible to planner without code inspection |
| 4 | System supports up to 3 debate rounds before forced synthesis | Run Test 3 (deadlock); count rounds | Exactly 3 rounds, then Executor with deadlock flag |
| 5 | Run log updates in real time | Open demo in browser; instrument latency | All step renders within 500 ms of write |
| 6 | Every tool call resolves to a live BigQuery query | Sample 10 tool calls; verify `bq_job_id` | 10/10 verified in `INFORMATION_SCHEMA.JOBS` |
| 7 | Approved decision logged with full reasoning chain | After approval, query `fct_allocation_decisions` | Row exists; `reasoning_summary_json` populated |
| 8 | Session terminates on approve or reject | Run both flows; check no further steps | Final step is `agent="human"`; status terminal |
| 9 | Agents have no access to `tiger_foods_raw` | Inspect IAM; attempt query as agent SA | No grant exists; query returns 403 |
| 10 | Same demo input produces same agent conversation | Run demo 3 times; compare outputs | Identical recommended_action across runs |

## Part B — Eight Test Cases

### Test 1 — Happy Path

**Goal:** Validate standard Watchdog → Economist → Executor → Human flow.

**Input:** Demo scenario JSON (see `reference/demo_scenario.md`).

**Expected behavior:**
- Watchdog recommends DC-01 reroute (round 1)
- Economist challenges with DC-04 alternative
- Round 2: agents converge on DC-04
- Executor produces action card, `status=READY_FOR_APPROVAL`

**Expected BigQuery queries:** 7–9 total (Watchdog 3, Economist 4–6, Executor 1).

**Expected Firestore writes:** 13–15 step docs.

**Pass:** Executor's `recommended_action.origin_plant == "DC-04"`, `net_value_usd > 10000`. Criteria 1–6 above pass.

### Test 2 — Immediate Agreement

**Goal:** Validate no-debate happy path.

**Input:** Order where only one DC has the inventory.

```json
{
  "trigger_type": "new_order",
  "trigger_payload": {
    "shipment_id": "TEST-002",
    "customer_kunnr": "0001000599",
    "material_matnr": "000000000010099001",
    "qty_cs": 200,
    "ship_to": "Customer X DC",
    "mabd": "2026-05-20",
    "default_origin_plant": "DC-05"
  }
}
```

(Engineer: pick a customer/SKU combo from the live data where only one DC has inventory and no significant cost alternative exists.)

**Expected:**
- Watchdog recommends the single-viable DC
- Economist returns `position: "agree"` in round 1
- No round 2; Executor invoked at step ~8
- Total steps to approval card < 10

**Pass:** Firestore shows Economist `position: "agree"` at round 1; no round-2 Watchdog message.

### Test 3 — Deadlock (Max Rounds)

**Goal:** Validate deadlock handling.

**Setup:** Set env var `TEST_FORCE_CHALLENGE=true` on the orchestrator (or use the corresponding orchestrator config flag) to force Economist to challenge across all rounds.

**Input:** Demo scenario JSON.

**Expected:**
- Round 1: Watchdog DC-A, Economist challenge
- Round 2: Watchdog HOLD, Economist HOLD
- Round 3: Watchdog HOLD, Economist HOLD
- Orchestrator passes to Executor with `deadlock_flag=True`
- Executor card `status="DEADLOCK"`, `deadlock_detail` populated

**Pass:** Exactly 3 Watchdog rounds and 3 Economist rounds; Executor status DEADLOCK; UI shows "Agents did not converge" warning.

### Test 4 — Human Rejection

**Goal:** Validate rejection flow.

**Procedure:** Run Test 1 to completion; reject instead of approve.

```bash
curl -X POST .../sessions/<session_id>/reject -d '{
  "user_id": "tester@mars.com",
  "rejection_reason": "Carrier unavailable in this window; need different solution."
}'
```

**Expected:**
- Session status → `rejected`
- `log_decision` writes row with `human_decision="rejected"`, `rejection_reason` populated
- Firestore step: `agent="human", action="reject", notes: <reason>`
- Agents NOT re-invoked

**Pass:** Query `WHERE session_id=... AND human_decision='rejected'` returns 1 row; `rejection_reason` matches input; no further Firestore steps.

### Test 5 — BigQuery Returns Empty

**Goal:** Validate graceful no-data handling.

**Input:** Customer KUNNR or material MATNR that does not exist.

```json
{
  "trigger_type": "new_order",
  "trigger_payload": {
    "customer_kunnr": "0001999999",
    "material_matnr": "000000000099999999",
    "qty_cs": 100,
    "mabd": "2026-05-25"
  }
}
```

**Expected:**
- Watchdog tools return empty result sets
- Watchdog produces alert with `gaps: ["Customer not found", ...]`, `confidence < 0.3`
- `initial_recommendation.action == "NO_ACTION"`
- Economist returns `position: "agree"` (no challenge possible without data)
- Executor produces card with `action_type="NO_ACTION"`

**Pass:** Session does NOT error; completes with explicit NO_ACTION; `gaps` array populated; decision logged.

### Test 6 — Schema Guardrail Enforcement

**Goal:** Validate Pydantic schema enforcement.

**Procedure:** Add a unit test that mocks Watchdog to return `action: "DELETE_ORDER"` (not in allowed enum).

**Expected:**
- `WatchdogAlert.model_validate()` raises `ValidationError`
- Orchestrator catches; session marked `status="error"`
- Firestore step: `agent="orchestrator", action="error"`
- Session terminates without invoking Economist

**Pass:** ValidationError caught, logged; no downstream agents invoked; session ends in error state.

### Test 7 — Concurrent Sessions

**Goal:** Validate multi-session isolation.

**Procedure:** Two parallel `POST /sessions` calls with different payloads.

**Expected:**
- Two distinct Firestore documents with different `session_id`
- Step writes do not cross sessions
- Each UI window only renders its own steps
- Both reach `awaiting_approval` independently

**Pass:** Firestore inspection shows two distinct sessions; no cross-pollution; no BigQuery race conditions.

### Test 8 — Data Freshness

**Goal:** Validate live data reads, not cached.

**Procedure:**

```sql
-- 1. Run demo. Note Watchdog finds DC-04 = 1800 CS.
-- 2. Mutate bronze:
UPDATE `resilience-riskradar.tiger_foods_raw.sap_mseg`
SET menge_cs = 500
WHERE plant = 'DC-04'
  AND material_matnr = '000000000010054321'
  AND movement_date = CURRENT_DATE();
-- 3. (Wait for view refresh if views are materialized.)
-- 4. Re-run demo.
```

**Expected:** Watchdog's inventory finding now shows DC-04 = 500 CS. Recommended action changes. BigQuery job IDs differ.

**Pass:** Second run reflects the change without service restart; different `bq_job_id`s; different recommended action.

## Part C — Performance Criteria (p95)

| Operation | Target | Hard maximum |
|---|---|---|
| Single tool call (BQ query + tool return) | < 2 s | 5 s |
| Single Flash agent invocation | < 6 s | 20 s |
| Single Pro agent invocation | < 12 s | 20 s |
| Single Watchdog→Economist round | < 8 s | 25 s |
| Full end-to-end (trigger → approval card visible) | < 30 s | 60 s |
| Firestore listener — write to UI render | < 500 ms | 1 s |
| Cloud Run cold start | < 8 s | 15 s (mitigate with `--min-instances 1` for demos) |

## Part D — Execution Order

Recommended order:

1. Unit tests on `adk_tools.py` functions (mock `_bq`). Validates SQL construction.
2. Integration tests on each agent individually against live BigQuery. Validates schema compliance.
3. Test 5 (empty data) — graceful failure before adding orchestration.
4. Test 1 (happy path) — full orchestration validated.
5. Test 2 (immediate agreement) — no-debate case.
6. Test 3 (deadlock) — max-rounds case.
7. Test 4 (rejection) — human flow.
8. Test 6 (guardrails) — error handling.
9. Test 7 (concurrent) — multi-session isolation.
10. Test 8 (data freshness) — live data.

## Part E — Sign-off

Recorded in `release_signoff.md` in the orchestrator-service repo. Required signatures:

- **AI/ML Engineer** — confirms code review passed, no anti-patterns
- **Data Engineer** — confirms IAM scope correct, semantic layer unchanged
- **Consultant Lead** — confirms acceptance demo passed in front of client stakeholders

Required attachments:

- Acceptance test run output (all 10 criteria, all 8 tests)
- Performance test report (p95 latencies)
- BigQuery `INFORMATION_SCHEMA.JOBS` export covering acceptance run window
- IAM policy export for `tiger-agents-sa`

## Anti-pattern checklist (code review)

Code review must verify the following are NOT present:

- [ ] No hardcoded data in any tool function (all return BigQuery rows or error)
- [ ] No specific numbers in any agent system prompt
- [ ] No cached tool results across sessions
- [ ] No mocked BigQuery client outside `tests/`
- [ ] No `LIMIT 1` + `ORDER BY` patterns that effectively return constant data
- [ ] No agent has `log_decision` in its tool list (orchestrator-only)
- [ ] No `bigquery.Client(project=...)` instantiations except in `adk_tools.py` and `bigquery_client.py`
- [ ] No direct Firestore reads/writes from agent code (orchestrator-only)

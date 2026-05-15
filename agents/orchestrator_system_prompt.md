# Orchestrator — Routing Contract

The orchestrator is **not a reasoning agent**. It is a deterministic Python routing layer that:

- Runs the agent state machine
- Decides routing using rules on agents' structured outputs (no LLM in the loop)
- Captures every event to Firestore as a step in the run log

This file documents the routing contract. The implementation lives in `code/orchestrator_service/orchestrator.py`.

---

## Rule 1 — Initial Invocation

When `POST /sessions` is called:

1. Write a Firestore document at `agent_sessions/{session_id}`:
   - `session_id`, `started_at`, `trigger_type`, `trigger_payload`
   - `status = "active"`
   - `current_round = 1`
2. Write step `00001`: `agent="orchestrator"`, `action="route"`, `notes="session started"`.
3. Invoke Watchdog with the trigger payload.
4. Stream Watchdog's tool calls and response as steps `00002`–`0000N`.
5. Proceed to Rule 2.

## Rule 2 — Post-Watchdog

After Watchdog returns a `WatchdogAlert`:

1. Invoke Economist with the full Watchdog message as input.
2. Stream Economist's tool calls and response as steps.
3. Inspect `economist.position`:
   - if `"agree"` AND `watchdog.round == 1` → proceed to Rule 4 (Executor)
   - if `"agree"` AND prior round had `position == "challenge"` → proceed to Rule 4 (convergence)
   - if `"challenge"` AND `current_round < MAX_DEBATE_ROUNDS` (= 3) → proceed to Rule 3 (debate)
   - if `"challenge"` AND `current_round == 3` → proceed to Rule 4 with `deadlock_flag = True`

## Rule 3 — Debate Round

Increment `current_round` in Firestore. Pass Economist's challenge back to Watchdog.

1. Write step: `agent="orchestrator"`, `action="route"`, `notes="Economist challenged, routing back to Watchdog round N"`.
2. Invoke Watchdog with the full Economist message as input.
3. Watchdog returns an updated `WatchdogAlert` with `response_to_economist` populated.
4. Stream to Firestore.
5. Invoke Economist with the updated Watchdog message.
6. Economist returns updated `EconomistAnalysis`.
7. Stream to Firestore.
8. Re-evaluate Rule 2.

## Rule 4 — Executor

Compile full session state (all Watchdog and Economist messages in order).

1. Determine status:
   - **Converged:** latest `economist.position == "agree"` OR mutual agreement reached
   - **Deadlocked:** round 3 reached without agreement
2. Invoke Executor with the full session state and the converged/deadlocked flag.
3. Stream Executor's response as a step.
4. Update Firestore session doc: `status = "awaiting_approval"`, store `final_action_card`.
5. The Executor's `ExecutorActionCard` becomes the approval card the UI renders.

## Rule 5 — Human Approval

Wait for `POST /sessions/{session_id}/approve` or `POST /sessions/{session_id}/reject`.

**On approve:**
1. Validate session is in `awaiting_approval` state.
2. Call the `log_decision` tool to write to `tiger_decisions.fct_allocation_decisions`.
3. Update Firestore session doc: `status = "approved"`, `decision_id`, `user_id`, `ended_at = NOW`.
4. Write final step: `agent="human"`, `action="approve"`, `notes="<approver email>"`.

**On reject:**
1. Validate session is in `awaiting_approval` state.
2. Call `log_decision` with `human_decision = "rejected"` and the `rejection_reason`. (Rejection is also a decision worth logging.)
3. Update Firestore session doc: `status = "rejected"`, `decision_id`, `user_id`, `ended_at`.
4. Write final step: `agent="human"`, `action="reject"`, `notes="<reason>"`.
5. **Do NOT loop back to agents.** Session terminates.

## Rule 6 — Termination

Sessions terminate on:
- `approved` / `rejected` (Rule 5)
- `cancelled` (admin action via API)
- `deadlock` (terminal if no human decision in the deadlock case)
- `error` (caught exception)

All terminal Firestore writes include `ended_at`. `status` is one of: `approved | rejected | cancelled | error | deadlock`.

---

## Step Document Schema

Every agent invocation writes one or more docs in `agent_sessions/{session_id}/steps/{step_index}`:

```json
{
  "step_index": 4,
  "timestamp_iso": "2026-05-12T14:32:18.234Z",
  "agent": "watchdog",
  "round": 1,
  "action": "response",
  "tool_name": null,
  "tool_args": null,
  "tool_result_summary": null,
  "tool_result_full": null,
  "model_response_json": { /* the full agent JSON output */ },
  "latency_ms": 3420,
  "notes": null,
  "bq_job_id": null
}
```

Tool calls and responses each get their own step doc. A round-2 debate exchange typically produces 8–10 step docs.

---

## Configuration

| Setting | Value | Where set |
|---|---|---|
| `MAX_DEBATE_ROUNDS` | 3 | `orchestrator.py` module-level constant |
| `TOOL_CALL_TIMEOUT_S` | 10 | Per BigQuery query timeout |
| `AGENT_INVOCATION_TIMEOUT_S` | 60 | Per agent call timeout |
| `FORCE_CHALLENGE_FOR_TESTING` | False (default) | Env var `TEST_FORCE_CHALLENGE`, used in Test 3 |

Changing `MAX_DEBATE_ROUNDS` is the easiest knob if real-world data shows agents converging too slowly or too quickly. Start at 3.

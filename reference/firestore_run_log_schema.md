# Firestore Run-Log Schema & React Listener Pattern

The run log is the audit trail visible to the planner during a session and queryable after. It lives in Firestore so the React UI can stream new agent activity in real time.

## Collection structure

```
agent_sessions/                                     (root collection)
  └── {session_id}/                                 (one doc per session)
        ├── session_id, started_at, ended_at,
        ├── trigger_type, trigger_payload,
        ├── status, current_round,
        ├── final_action_card, decision_id, user_id
        │
        └── steps/                                  (sub-collection, ordered)
              ├── 00001 → orchestrator route
              ├── 00002 → watchdog tool_call (get_inventory_positions)
              ├── 00003 → watchdog tool_call (get_otif_performance)
              ├── 00004 → watchdog response
              ├── 00005 → economist tool_call
              ├── ...
              └── 00014 → human approve
```

## Session document fields

```json
{
  "session_id": "session_20260512_143218_a3f9b1",
  "started_at": <FIRESTORE_TIMESTAMP>,
  "ended_at":   <FIRESTORE_TIMESTAMP or null>,
  "trigger_type": "new_order",
  "trigger_payload": { /* TriggerPayload from schemas.py */ },
  "status": "active | awaiting_approval | approved | rejected | cancelled | error | deadlock",
  "current_round": 1,
  "final_action_card": { /* ExecutorActionCard, populated after Rule 4 */ } | null,
  "decision_id": "<uuid, populated on approve/reject>" | null,
  "user_id":     "<approver email>" | null
}
```

Updated by:
- `firestore_client.create_session()` on session start
- `firestore_client.update_session()` on each status transition

## Step document fields

Each step doc is keyed by zero-padded `step_index`. Lexicographic order = monotonic order.

```json
{
  "step_index":          5,
  "timestamp_iso":       "2026-05-12T14:32:24.118Z",
  "agent":               "economist",
  "round":               1,
  "action":              "tool_call",
  "tool_name":           "get_transfer_cost_comparison",
  "tool_args":           { "origin_plant": "DC-01", "destination_region": "US-FL-WALMART",
                            "material_matnr": "000000000010054321", "quantity_cs": 1200 },
  "tool_result_summary": "get_transfer_cost_comparison returned 1 row",
  "tool_result_full":    { /* full rows array */ },
  "model_response_json": null,
  "latency_ms":          1842,
  "bq_job_id":           "bquxjob_abc123_19c7a8e4f12",
  "notes":               null
}
```

For `action="response"`, `model_response_json` is the agent's full structured output (WatchdogAlert / EconomistAnalysis / ExecutorActionCard).

For `action="approve"` or `"reject"`, only `agent="human"`, `notes`, and `timestamp_iso` are populated.

## React listener — the canonical pattern

```jsx
import { useEffect, useState } from "react";
import {
  collection, doc, onSnapshot, orderBy, query,
} from "firebase/firestore";
import { db } from "./firebase";

export function useSessionRunLog(sessionId) {
  const [session, setSession] = useState(null);
  const [steps,   setSteps]   = useState([]);

  // 1. Subscribe to the session doc (status changes, action card).
  useEffect(() => {
    if (!sessionId) return;
    const sessionRef = doc(db, "agent_sessions", sessionId);
    const unsubscribe = onSnapshot(sessionRef, (snap) => {
      if (snap.exists()) setSession({ id: snap.id, ...snap.data() });
    });
    return unsubscribe;
  }, [sessionId]);

  // 2. Subscribe to the steps sub-collection ordered by step_index.
  useEffect(() => {
    if (!sessionId) return;
    const stepsRef = collection(db, "agent_sessions", sessionId, "steps");
    const q = query(stepsRef, orderBy("step_index", "asc"));

    const unsubscribe = onSnapshot(q, (snap) => {
      // Use docChanges() to render incrementally — new steps appear as they arrive.
      snap.docChanges().forEach((change) => {
        if (change.type === "added") {
          setSteps((prev) => [...prev, { id: change.doc.id, ...change.doc.data() }]);
        }
        // We don't expect steps to be modified or removed in normal operation.
      });
    });
    return unsubscribe;
  }, [sessionId]);

  return { session, steps };
}
```

### Rendering a step in the UI

```jsx
function RunLogStep({ step }) {
  const agentColor = {
    watchdog:     "bg-amber-100 text-amber-900",
    economist:    "bg-emerald-100 text-emerald-900",
    executor:     "bg-sky-100 text-sky-900",
    orchestrator: "bg-slate-100 text-slate-700",
    human:        "bg-violet-100 text-violet-900",
  }[step.agent] || "bg-gray-100";

  return (
    <div className={`rounded-lg p-3 my-2 ${agentColor}`}>
      <div className="flex items-center justify-between text-xs opacity-70">
        <span>{step.agent.toUpperCase()} {step.round ? `· round ${step.round}` : ""}</span>
        <span>{new Date(step.timestamp_iso).toLocaleTimeString()}</span>
      </div>
      {step.action === "tool_call" && step.tool_name && !step.tool_result_summary && (
        <div className="text-sm mt-1">↳ calling <code>{step.tool_name}</code></div>
      )}
      {step.action === "tool_call" && step.tool_result_summary && (
        <div className="text-sm mt-1">↩ {step.tool_result_summary}</div>
      )}
      {step.action === "response" && step.model_response_json && (
        <div className="text-sm mt-1">
          {step.model_response_json.reasoning_summary}
        </div>
      )}
      {step.action === "route" && (
        <div className="text-xs italic mt-1">{step.notes}</div>
      )}
      {step.action === "approve" && (
        <div className="text-sm font-medium mt-1">✓ {step.notes}</div>
      )}
      {step.action === "reject" && (
        <div className="text-sm font-medium mt-1">✗ {step.notes}</div>
      )}
    </div>
  );
}
```

## Composite indexes

Only needed if the UI does cross-session queries (e.g., a director dashboard showing all "approved" sessions in the last week). Configurations are in `infra/firestore_indexes.json`.

## Retention

Firestore documents are kept indefinitely by default. For pilot, no TTL is configured. For production, consider:

- 90-day TTL on `agent_sessions` documents
- Migration job archiving session JSON to GCS for long-term audit
- Decision history is preserved permanently in `tiger_decisions.fct_allocation_decisions` (BigQuery), independent of Firestore

## Performance characteristics

Firestore listeners receive document writes within 100–500 ms typically. The UI should feel instantaneous; if it does not, check:

1. Browser network panel for the websocket connection to Firestore
2. Whether multiple `onSnapshot` listeners are stacking (memory leak)
3. Whether `setSteps` is causing full re-renders (use `React.memo` on `RunLogStep`)

"""
Firestore client wrapper.

Two responsibilities:
  1. Manage the parent session document at agent_sessions/{session_id}
  2. Append step docs to agent_sessions/{session_id}/steps/{step_index}

The step doc IDs are zero-padded so Firestore's lexicographic ordering matches
the monotonic step_index integer ("00001", "00002", ..., "00099").

The React frontend listens to the steps sub-collection with onSnapshot ordered
by step_index ascending; new steps render as they arrive.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore


PROJECT_ID = os.environ.get("PROJECT_ID", "resilience-riskradar")
_db = firestore.Client(project=PROJECT_ID)

_SESSIONS = "agent_sessions"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Session document
# ---------------------------------------------------------------------------
def create_session(session_id: str, trigger_type: str,
                   trigger_payload: dict[str, Any]) -> None:
    _db.collection(_SESSIONS).document(session_id).set({
        "session_id":      session_id,
        "started_at":      firestore.SERVER_TIMESTAMP,
        "ended_at":        None,
        "trigger_type":    trigger_type,
        "trigger_payload": trigger_payload,
        "status":          "active",
        "current_round":   1,
        "final_action_card": None,
        "decision_id":     None,
        "user_id":         None,
    })


def update_session(session_id: str, **fields: Any) -> None:
    if "ended_at" in fields and fields["ended_at"] == "NOW":
        fields["ended_at"] = firestore.SERVER_TIMESTAMP
    _db.collection(_SESSIONS).document(session_id).update(fields)


def get_session(session_id: str) -> dict[str, Any] | None:
    snap = _db.collection(_SESSIONS).document(session_id).get()
    return snap.to_dict() if snap.exists else None


# ---------------------------------------------------------------------------
# Step documents (sub-collection)
# ---------------------------------------------------------------------------
class StepWriter:
    """Maintains the monotonic step_index per session and writes step docs."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._next_idx = 1

    def write(self, *,
              agent: str,
              action: str,
              round_idx: int | None = None,
              tool_name: str | None = None,
              tool_args: dict | None = None,
              tool_result_summary: str | None = None,
              tool_result_full: dict | None = None,
              model_response_json: dict | None = None,
              latency_ms: int | None = None,
              bq_job_id: str | None = None,
              notes: str | None = None) -> int:
        """Write one step doc. Returns the step_index used."""
        idx = self._next_idx
        self._next_idx += 1
        doc_id = f"{idx:05d}"  # "00001"
        payload = {
            "step_index":          idx,
            "timestamp_iso":       _now_iso(),
            "agent":               agent,
            "round":               round_idx,
            "action":              action,
            "tool_name":           tool_name,
            "tool_args":           tool_args,
            "tool_result_summary": tool_result_summary,
            "tool_result_full":    tool_result_full,
            "model_response_json": model_response_json,
            "latency_ms":          latency_ms,
            "bq_job_id":           bq_job_id,
            "notes":               notes,
        }
        (_db.collection(_SESSIONS)
            .document(self.session_id)
            .collection("steps")
            .document(doc_id)
            .set(payload))
        return idx

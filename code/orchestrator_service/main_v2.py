"""
FastAPI service for the v2 5-agent build. Dual-mode:

  POST /sessions          → v2 5-agent flow (deck-aligned, default)
  POST /sessions/poc      → v1 3-agent Watchdog/Economist/Executor flow
  GET  /sessions/{id}     → state for either flow
  POST /sessions/{id}/approve, /reject → human gate for either flow

The v1 main.py is superseded by this file when deploying v2. The same
Cloud Run service runs both flows so legacy POC callers continue working
while Mars-facing demos use the deck architecture.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

# Load .env before any google.adk import creates a genai client.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Disable OpenTelemetry SDK to prevent "Token created in a different Context"
# errors that occur when ADK's async generators are garbage-collected across
# asyncio task boundaries.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

# Configure ADK auth:
# - Set GOOGLE_API_KEY in .env for Gemini Developer API (local dev)
# - Otherwise fall back to Vertex AI via ADC / service-account (Cloud Run)
if not os.environ.get("GOOGLE_API_KEY"):
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    os.environ.setdefault(
        "GOOGLE_CLOUD_PROJECT", os.environ.get("PROJECT_ID", "resilience-riskradar")
    )
    os.environ.setdefault(
        "GOOGLE_CLOUD_LOCATION", os.environ.get("REGION", "us-central1")
    )

from fastapi import BackgroundTasks, FastAPI, HTTPException

from firestore_client import create_session, get_session

# v2 (5-agent) imports
from orchestrator_v2 import (
    run_session_v2,
    approve_session_v2,
    reject_session_v2,
)
from schemas_v2 import (
    StartSessionRequestV2,
    StartSessionResponseV2,
)

# v1 (3-agent POC) imports — preserved
from orchestrator import run_session as run_session_poc
from orchestrator import (
    approve_session as approve_session_poc,
    reject_session as reject_session_poc,
)
from schemas import (
    StartSessionRequest as StartSessionRequestPOC,
    ApprovalRequest,
    RejectionRequest,
    DecisionResponse,
)


PROJECT_ID = os.environ.get("PROJECT_ID", "resilience-riskradar")

app = FastAPI(
    title="Tiger Foods Agentic AI Orchestrator (v2)",
    version="2.0.0",
    description=("Dual-mode: 5-agent N-to-N parallel (deck) and 3-agent "
                  "sequential debate (POC) flows."),
)


def _new_session_id(prefix: str = "session") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}_{uuid.uuid4().hex[:6]}"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "project": PROJECT_ID,
            "version": "2.0.0",
            "flows": "five_agent (default), poc"}


# ---------------------------------------------------------------------------
# V2 endpoint — 5-agent flow (deck-aligned, default)
# ---------------------------------------------------------------------------
@app.post("/sessions", response_model=StartSessionResponseV2)
async def start_session_v2(
    req: StartSessionRequestV2,
    background_tasks: BackgroundTasks,
) -> StartSessionResponseV2:
    session_id = _new_session_id("session")
    payload_dict = req.trigger_payload.model_dump()
    payload_dict["flow_mode"] = "five_agent"
    create_session(
        session_id=session_id,
        trigger_type=req.trigger_type,
        trigger_payload=payload_dict,
    )
    background_tasks.add_task(
        run_session_v2,
        session_id,
        req.trigger_type,
        req.trigger_payload,
    )
    return StartSessionResponseV2(
        session_id=session_id, status="active", flow_mode="five_agent")


# ---------------------------------------------------------------------------
# POC endpoint — 3-agent sequential debate (preserved)
# ---------------------------------------------------------------------------
@app.post("/sessions/poc", response_model=StartSessionResponseV2)
async def start_session_poc(
    req: StartSessionRequestPOC,
    background_tasks: BackgroundTasks,
) -> StartSessionResponseV2:
    session_id = _new_session_id("session_poc")
    payload_dict = req.trigger_payload.model_dump()
    payload_dict["flow_mode"] = "poc"
    create_session(
        session_id=session_id,
        trigger_type=req.trigger_type,
        trigger_payload=payload_dict,
    )
    background_tasks.add_task(
        run_session_poc,
        session_id,
        req.trigger_type,
        req.trigger_payload,
    )
    return StartSessionResponseV2(
        session_id=session_id, status="active", flow_mode="poc")


# ---------------------------------------------------------------------------
# Shared session reads
# ---------------------------------------------------------------------------
@app.get("/sessions/{session_id}")
def read_session(session_id: str) -> dict:
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


# ---------------------------------------------------------------------------
# Approve / reject — routes by flow_mode stored in the session
# ---------------------------------------------------------------------------
@app.post("/sessions/{session_id}/approve", response_model=DecisionResponse)
def approve(session_id: str, req: ApprovalRequest) -> DecisionResponse:
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if sess.get("status") != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail=(f"Session is in state '{sess.get('status')}', not "
                     f"awaiting_approval"),
        )
    action_card = sess.get("final_action_card") or {}
    flow_mode = sess.get("trigger_payload", {}).get("flow_mode", "five_agent")
    if flow_mode == "five_agent":
        decision_id = approve_session_v2(
            session_id=session_id,
            action_card=action_card,
            user_id=req.user_id,
            approval_notes=req.approval_notes,
        )
    else:
        decision_id = approve_session_poc(
            session_id=session_id,
            action_card=action_card,
            user_id=req.user_id,
            approval_notes=req.approval_notes,
        )
    return DecisionResponse(decision_id=decision_id, status="approved")


@app.post("/sessions/{session_id}/reject", response_model=DecisionResponse)
def reject(session_id: str, req: RejectionRequest) -> DecisionResponse:
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if sess.get("status") != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail=(f"Session is in state '{sess.get('status')}', not "
                     f"awaiting_approval"),
        )
    action_card = sess.get("final_action_card") or {}
    flow_mode = sess.get("trigger_payload", {}).get("flow_mode", "five_agent")
    if flow_mode == "five_agent":
        decision_id = reject_session_v2(
            session_id=session_id,
            action_card=action_card,
            user_id=req.user_id,
            rejection_reason=req.rejection_reason,
        )
    else:
        decision_id = reject_session_poc(
            session_id=session_id,
            action_card=action_card,
            user_id=req.user_id,
            rejection_reason=req.rejection_reason,
        )
    return DecisionResponse(decision_id=decision_id, status="rejected")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)

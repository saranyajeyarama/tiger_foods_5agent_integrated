"""
FastAPI orchestrator service.

Endpoints:
  POST /sessions                          Start a new debate session.
  GET  /sessions/{session_id}             Read current session state.
  POST /sessions/{session_id}/approve     Approve the action card.
  POST /sessions/{session_id}/reject      Reject the action card.

Cloud Run deployment:
  gcloud run deploy tiger-agents-orchestrator \\
    --source . \\
    --region us-central1 \\
    --service-account tiger-agents-sa@resilience-riskradar.iam.gserviceaccount.com \\
    --set-env-vars PROJECT_ID=resilience-riskradar,REGION=us-central1 \\
    --allow-unauthenticated \\
    --memory 2Gi --cpu 2 --concurrency 40 --timeout 300s
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()  # no-op if .env absent; loads GOOGLE_API_KEY etc. for local dev

# Configure ADK auth before any google.adk import instantiates a genai client.
# - Local dev with Gemini Developer API: set GOOGLE_API_KEY in .env
# - Cloud Run / local gcloud ADC: uses Vertex AI automatically
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
from orchestrator import run_session, approve_session, reject_session
from schemas import (
    StartSessionRequest,
    StartSessionResponse,
    ApprovalRequest,
    RejectionRequest,
    DecisionResponse,
)


PROJECT_ID = os.environ.get("PROJECT_ID", "resilience-riskradar")

app = FastAPI(title="Tiger Foods Agentic AI Orchestrator", version="0.1.0")


def _new_session_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    return f"session_{ts}_{short_uuid}"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "project": PROJECT_ID, "version": "0.1.0"}


@app.post("/sessions", response_model=StartSessionResponse)
async def start_session(req: StartSessionRequest,
                         background_tasks: BackgroundTasks
                         ) -> StartSessionResponse:
    session_id = _new_session_id()
    create_session(
        session_id=session_id,
        trigger_type=req.trigger_type,
        trigger_payload=req.trigger_payload.model_dump(),
    )
    # Run the debate asynchronously. The endpoint returns immediately.
    background_tasks.add_task(
        run_session, session_id, req.trigger_type, req.trigger_payload,
    )
    return StartSessionResponse(session_id=session_id, status="active")


@app.get("/sessions/{session_id}")
def read_session(session_id: str) -> dict:
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


@app.post("/sessions/{session_id}/approve", response_model=DecisionResponse)
def approve(session_id: str, req: ApprovalRequest) -> DecisionResponse:
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if sess.get("status") != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Session is in state '{sess.get('status')}', not "
                   f"awaiting_approval",
        )
    action_card = sess.get("final_action_card") or {}
    decision_id = approve_session(
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
            detail=f"Session is in state '{sess.get('status')}', not "
                   f"awaiting_approval",
        )
    action_card = sess.get("final_action_card") or {}
    decision_id = reject_session(
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

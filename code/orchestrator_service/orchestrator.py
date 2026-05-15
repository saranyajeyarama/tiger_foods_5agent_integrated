"""
Orchestrator — debate state machine.

Implements Rules 1-6 from agents/orchestrator_system_prompt.md.

This is deterministic Python. No LLM is asked to make routing decisions.
The agent's structured output (the `position` field on EconomistAnalysis)
drives routing.

The orchestrator is run as a background task by main.py. The HTTP endpoints
return immediately; the debate runs async. Progress is observable through
Firestore.
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from agents import get_agent
from firestore_client import StepWriter, update_session
from schemas import (
    WatchdogAlert,
    EconomistAnalysis,
    ExecutorActionCard,
    TriggerPayload,
)
from adk_tools import log_decision


MAX_DEBATE_ROUNDS = 3
ORCHESTRATOR_VERSION = "0.1.0"
AGENT_MODEL_VERSIONS = "gemini-2.5-pro,gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)


async def _invoke_agent(agent_name: str, prompt_payload: dict,
                         writer: StepWriter, round_idx: int) -> dict:
    """Invoke one ADK agent and stream its tool calls and response to Firestore.

    Returns the parsed JSON response (already validated by ADK's output_schema).
    """
    agent = get_agent(agent_name)
    session_svc = InMemorySessionService()
    runner = Runner(
        agent=agent,
        session_service=session_svc,
        app_name="tiger-agents",
    )

    t0 = _now_ms()
    user_msg = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=json.dumps(prompt_payload))],
    )
    response_json: dict | None = None

    adk_session_id = f"adk-{writer.session_id}-{agent_name}-r{round_idx}"
    await session_svc.create_session(
        app_name="tiger-agents",
        user_id="orchestrator",
        session_id=adk_session_id,
    )

    # ADK Runner yields events: tool_call, tool_response, agent_response.
    async for event in runner.run_async(
        user_id="orchestrator",
        session_id=adk_session_id,
        new_message=user_msg,
    ):
        # Handle tool calls
        if getattr(event, "is_tool_call", False) and getattr(event, "tool_call", None):
            tc = event.tool_call
            writer.write(
                agent=agent_name,
                round_idx=round_idx,
                action="tool_call",
                tool_name=tc.name,
                tool_args=dict(tc.args) if tc.args else {},
                notes=f"{agent_name} called {tc.name}",
            )
        elif getattr(event, "is_tool_response", False) and getattr(event, "tool_response", None):
            tr = event.tool_response
            result = tr.result if isinstance(tr.result, dict) else {"value": tr.result}
            row_count = result.get("row_count")
            summary = (f"{tr.name} returned {row_count} rows"
                       if row_count is not None
                       else f"{tr.name} returned a result")
            writer.write(
                agent=agent_name,
                round_idx=round_idx,
                action="tool_call",
                tool_name=tr.name,
                tool_result_summary=summary,
                tool_result_full=result,
                bq_job_id=result.get("bq_job_id"),
            )
        elif getattr(event, "is_final_response", False):
            # The structured JSON output from the agent.
            content = event.final_response
            if isinstance(content, str):
                response_json = json.loads(content)
            elif hasattr(content, "model_dump"):
                response_json = content.model_dump()
            elif isinstance(content, dict):
                response_json = content

    latency_ms = _now_ms() - t0

    writer.write(
        agent=agent_name,
        round_idx=round_idx,
        action="response",
        model_response_json=response_json,
        latency_ms=latency_ms,
    )

    if response_json is None:
        raise RuntimeError(f"Agent {agent_name} did not produce a final response.")
    return response_json


# ---------------------------------------------------------------------------
# Main orchestration entrypoint (called from FastAPI background task)
# ---------------------------------------------------------------------------
async def run_session(session_id: str, trigger_type: str,
                       trigger_payload: TriggerPayload) -> None:
    """Run a full debate session asynchronously.

    Rules 1-4 happen here. Rules 5-6 (human approval/rejection) are handled
    in main.py's /approve and /reject endpoints.
    """
    writer = StepWriter(session_id)
    writer.write(agent="orchestrator", action="route",
                 notes="session started")

    try:
        # ---- Rule 1: invoke Watchdog ----
        wd_input = {
            "trigger": trigger_type,
            "order": trigger_payload.model_dump(),
            "round": 1,
        }
        wd_msg = await _invoke_agent("watchdog", wd_input, writer, round_idx=1)
        WatchdogAlert.model_validate(wd_msg)  # raises on bad schema

        # ---- Rule 2: invoke Economist with Watchdog message ----
        ec_input = {"watchdog_alert": wd_msg, "round": 1}
        ec_msg = await _invoke_agent("economist", ec_input, writer, round_idx=1)
        EconomistAnalysis.model_validate(ec_msg)

        current_round = 1
        deadlock = False

        # ---- Rule 3: debate loop ----
        while (ec_msg.get("position") == "challenge"
               and current_round < MAX_DEBATE_ROUNDS):
            current_round += 1
            update_session(session_id, current_round=current_round)
            writer.write(
                agent="orchestrator",
                action="route",
                round_idx=current_round,
                notes=(f"Economist challenged. Routing back to Watchdog "
                       f"for round {current_round}."),
            )

            wd_input = {
                "previous_watchdog_alert": wd_msg,
                "economist_challenge": ec_msg,
                "round": current_round,
            }
            wd_msg = await _invoke_agent("watchdog", wd_input, writer,
                                          round_idx=current_round)
            WatchdogAlert.model_validate(wd_msg)

            ec_input = {
                "watchdog_alert": wd_msg,
                "previous_economist_analysis": ec_msg,
                "round": current_round,
            }
            ec_msg = await _invoke_agent("economist", ec_input, writer,
                                          round_idx=current_round)
            EconomistAnalysis.model_validate(ec_msg)

        if (ec_msg.get("position") == "challenge"
                and current_round == MAX_DEBATE_ROUNDS):
            deadlock = True
            writer.write(
                agent="orchestrator",
                action="route",
                round_idx=current_round,
                notes=("Max debate rounds reached without convergence. "
                       "Routing to Executor with deadlock flag."),
            )
            update_session(session_id, status="deadlock")
        else:
            writer.write(
                agent="orchestrator",
                action="route",
                round_idx=current_round,
                notes=(f"Agents converged at round {current_round}. "
                       f"Routing to Executor."),
            )

        # ---- Rule 4: invoke Executor ----
        ex_input = {
            "session_id":       session_id,
            "watchdog_final":   wd_msg,
            "economist_final":  ec_msg,
            "convergence_round": current_round,
            "deadlock_flag":    deadlock,
        }
        ex_msg = await _invoke_agent("executor", ex_input, writer,
                                      round_idx=current_round)
        ExecutorActionCard.model_validate(ex_msg)

        # Enrich the action card with provenance for log_decision later.
        ex_msg.setdefault("agent_model_versions", AGENT_MODEL_VERSIONS)
        ex_msg.setdefault("orchestrator_version", ORCHESTRATOR_VERSION)
        ex_msg.setdefault("watchdog_confidence", wd_msg.get("confidence"))
        ex_msg.setdefault("economist_confidence", ec_msg.get("confidence"))
        ex_msg.setdefault("reasoning_chain", {})
        ex_msg["reasoning_chain"].setdefault("watchdog_final_round", current_round)
        ex_msg["reasoning_chain"].setdefault("economist_final_round", current_round)

        update_session(
            session_id,
            status="awaiting_approval",
            final_action_card=ex_msg,
        )

    except Exception as exc:
        writer.write(
            agent="orchestrator",
            action="error",
            notes=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[:1000]}",
        )
        update_session(session_id, status="error", ended_at="NOW")
        raise


# ---------------------------------------------------------------------------
# Rule 5 — human approval / rejection (called from HTTP endpoints in main.py)
# ---------------------------------------------------------------------------
def approve_session(session_id: str, action_card: dict,
                     user_id: str, approval_notes: str | None) -> str:
    """Log the decision and finalize the session. Returns decision_id."""
    payload = dict(action_card)
    payload["trigger_type"] = payload.get("trigger_type", "new_order")
    payload["watchdog_confidence"] = payload.get("watchdog_confidence")
    payload["economist_confidence"] = payload.get("economist_confidence")
    payload["agent_model_versions"] = payload.get(
        "agent_model_versions", AGENT_MODEL_VERSIONS)
    payload["orchestrator_version"] = payload.get(
        "orchestrator_version", ORCHESTRATOR_VERSION)

    result = log_decision(
        session_id=session_id,
        decision_payload_json=json.dumps(payload),
        human_decision="approved",
        human_decision_by=user_id,
    )
    if "error" in result:
        raise RuntimeError(f"log_decision failed: {result['error']}")

    writer = StepWriter(session_id)
    writer.write(
        agent="human",
        action="approve",
        notes=f"Approved by {user_id}"
              + (f". {approval_notes}" if approval_notes else ""),
    )
    update_session(
        session_id,
        status="approved",
        decision_id=result["decision_id"],
        user_id=user_id,
        ended_at="NOW",
    )
    return result["decision_id"]


def reject_session(session_id: str, action_card: dict, user_id: str,
                    rejection_reason: str) -> str:
    """Log the rejection and finalize the session. Returns decision_id."""
    payload = dict(action_card)
    payload["trigger_type"] = payload.get("trigger_type", "new_order")
    payload["agent_model_versions"] = payload.get(
        "agent_model_versions", AGENT_MODEL_VERSIONS)
    payload["orchestrator_version"] = payload.get(
        "orchestrator_version", ORCHESTRATOR_VERSION)

    result = log_decision(
        session_id=session_id,
        decision_payload_json=json.dumps(payload),
        human_decision="rejected",
        human_decision_by=user_id,
        rejection_reason=rejection_reason,
    )
    if "error" in result:
        raise RuntimeError(f"log_decision failed: {result['error']}")

    writer = StepWriter(session_id)
    writer.write(
        agent="human",
        action="reject",
        notes=f"Rejected by {user_id}: {rejection_reason}",
    )
    update_session(
        session_id,
        status="rejected",
        decision_id=result["decision_id"],
        user_id=user_id,
        ended_at="NOW",
    )
    return result["decision_id"]

"""
Orchestrator for the v2 5-agent flow.

Flow:
  1. Customer Supply Agent receives the order
  2. Orchestrator fires 4 specialists in PARALLEL via asyncio.gather
  3. Conflict detection on the 4 returned signals
  4. If conflict: structured debate round (max 2) between disputants
  5. Customer Supply Agent synthesizes the final recommendation
  6. Recommendation → Firestore awaiting_approval state
  7. On human approve/reject → DCE write via adk_tools_v2.dce_write

The v1 sequential debate orchestrator (orchestrator.py) is preserved and
remains the entry point for POST /sessions/poc.

Conflict detection rules (deterministic Python, not LLM):
  R1 — Any specialist returns hard_block=true
  R2 — Two specialists return opposing dispositions (PROCEED vs BLOCK)
  R3 — Confidence asymmetry: one specialist confidence ≥ 0.85 and another
       ≤ 0.50 on dispositions that imply different actions

After conflict detection, the orchestrator picks disputant pairs and
fires a debate round. Each disputant sees the other's signal and either
HOLDs or REVISEs. Max 2 follow-up rounds total per conflict.
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

from agents_v2 import get_agent_v2, SPECIALIST_AGENTS
from firestore_client import StepWriter, update_session
from schemas_v2 import (
    CustomerSupplyDecision,
    Conflict,
    TriggerPayloadV2,
)
from adk_tools_v2 import dce_write


MAX_DEBATE_ROUNDS = 2  # Follow-up rounds AFTER initial fan-out.
ORCHESTRATOR_VERSION = "v2.0.0"
AGENT_MODEL_VERSIONS = "gemini-2.5-pro,gemini-2.5-flash"


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Specialist invocation
# ---------------------------------------------------------------------------
async def _invoke_specialist(
    agent_name: str,
    prompt_payload: dict,
    writer: StepWriter,
    round_idx: int,
) -> dict:
    """Invoke one specialist agent and stream its tool calls + response to
    Firestore. Returns the parsed structured signal."""
    agent = get_agent_v2(agent_name)
    session_svc = InMemorySessionService()
    app_name = f"tiger-agents-v2-{agent_name}"
    runner = Runner(
        agent=agent,
        session_service=session_svc,
        app_name=app_name,
    )
    t0 = _now_ms()
    adk_session_id = f"adk-{writer.session_id}-{agent_name}-r{round_idx}"
    user_msg = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=json.dumps(prompt_payload))],
    )
    response_json: dict | None = None

    await session_svc.create_session(
        app_name=app_name,
        user_id="orchestrator-v2",
        session_id=adk_session_id,
    )

    async for event in runner.run_async(
        user_id="orchestrator-v2",
        session_id=adk_session_id,
        new_message=user_msg,
    ):
        for fc in (event.get_function_calls() or []):
            writer.write(
                agent=agent_name,
                round_idx=round_idx,
                action="tool_call",
                tool_name=fc.name,
                tool_args=dict(fc.args) if fc.args else {},
                notes=f"{agent_name} called {fc.name}",
            )

        for fr in (event.get_function_responses() or []):
            raw = getattr(fr, "response", None) or getattr(fr, "result", None)
            result = raw if isinstance(raw, dict) else {"value": raw}
            rc = result.get("row_count")
            summary = (f"{fr.name} returned {rc} rows"
                       if rc is not None else f"{fr.name} returned a result")
            writer.write(
                agent=agent_name,
                round_idx=round_idx,
                action="tool_call",
                tool_name=fr.name,
                tool_result_summary=summary,
                tool_result_full=result,
                bq_job_id=result.get("bq_job_id"),
            )

        if event.is_final_response():
            text_parts = []
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        text_parts.append(part.text)
            text = "".join(text_parts).strip()
            if text:
                try:
                    response_json = json.loads(text)
                except json.JSONDecodeError:
                    response_json = {"raw_response": text}

    latency_ms = _now_ms() - t0
    writer.write(
        agent=agent_name,
        round_idx=round_idx,
        action="response",
        model_response_json=response_json,
        latency_ms=latency_ms,
    )
    if response_json is None:
        # Graceful error envelope — Customer Supply Agent expects this shape.
        return {
            "agent": agent_name,
            "disposition": "CAUTION",
            "confidence": 0.0,
            "hard_block": False,
            "signal": {"error": f"{agent_name} produced no response"},
            "evidence": [],
            "reasoning_summary":
              f"{agent_name} did not return a structured response.",
        }
    return response_json


# ---------------------------------------------------------------------------
# Conflict detection (deterministic)
# ---------------------------------------------------------------------------
def _detect_conflicts(signals: dict[str, dict]) -> list[Conflict]:
    """Apply the 3 rules. Return a list of Conflict objects."""
    conflicts: list[Conflict] = []
    items = [(name, s) for name, s in signals.items()]

    # R1 — Hard block from any specialist
    blockers = [(name, s) for name, s in items if s.get("hard_block")]
    for name, s in blockers:
        # Identify a counterpart who is PROCEED-leaning to pair against
        proceeders = [n for n, ss in items
                      if ss.get("disposition") == "PROCEED"
                      and not ss.get("hard_block")]
        if proceeders:
            conflicts.append(Conflict(
                type="HARD_BLOCK",
                disputants=[name, proceeders[0]],
                summary=(f"{name} returned hard_block; {proceeders[0]} is "
                          f"PROCEED — needs reconciliation."),
            ))

    # R2 — Disposition divergence (PROCEED vs BLOCK)
    dispositions = {n: s.get("disposition") for n, s in items}
    proceed_agents = [n for n, d in dispositions.items() if d == "PROCEED"]
    block_agents = [n for n, d in dispositions.items() if d == "BLOCK"]
    if proceed_agents and block_agents:
        for pa in proceed_agents:
            for ba in block_agents:
                # Avoid duplicate with R1 if hard_block already paired
                already = any(set(c.disputants) == set([pa, ba])
                              for c in conflicts)
                if not already:
                    conflicts.append(Conflict(
                        type="DISPOSITION_DIVERGENCE",
                        disputants=[pa, ba],
                        summary=(f"{pa} says PROCEED, {ba} says BLOCK — "
                                  f"opposing reads of the same order."),
                    ))

    # R3 — Confidence asymmetry on differing dispositions
    for n_a, s_a in items:
        for n_b, s_b in items:
            if n_a >= n_b:
                continue
            d_a, d_b = s_a.get("disposition"), s_b.get("disposition")
            c_a = s_a.get("confidence", 0.0)
            c_b = s_b.get("confidence", 0.0)
            if d_a != d_b and ((c_a >= 0.85 and c_b <= 0.50)
                               or (c_b >= 0.85 and c_a <= 0.50)):
                already = any(set(c.disputants) == set([n_a, n_b])
                              for c in conflicts)
                if not already:
                    conflicts.append(Conflict(
                        type="CONFIDENCE_ASYMMETRY",
                        disputants=[n_a, n_b],
                        summary=(f"{n_a} (conf {c_a:.2f}) vs {n_b} "
                                  f"(conf {c_b:.2f}) — confidence asymmetry "
                                  f"on differing dispositions."),
                    ))

    return conflicts


# ---------------------------------------------------------------------------
# Debate rounds
# ---------------------------------------------------------------------------
async def _run_debate_round(
    conflict: Conflict,
    signals: dict[str, dict],
    writer: StepWriter,
    round_idx: int,
) -> dict[str, dict]:
    """For one Conflict, run one debate round: each disputant sees the
    other's signal and either HOLDs or REVISEs. Returns updated signals
    for the two disputants."""
    a, b = conflict.disputants
    sig_a, sig_b = signals[a], signals[b]
    writer.write(
        agent="orchestrator",
        round_idx=round_idx,
        action="route",
        notes=(f"Debate round {round_idx} on conflict {conflict.type}: "
               f"{a} ↔ {b}"),
    )

    # Fire both disputants in parallel with each other's position
    payload_a = {
        "your_previous_signal": sig_a,
        "disputant_position": sig_b,
        "round_number": round_idx,
        "instruction": ("Read the disputant's position. REVISE if their data "
                         "is materially new; otherwise HOLD with the specific "
                         "data they did not have."),
    }
    payload_b = {
        "your_previous_signal": sig_b,
        "disputant_position": sig_a,
        "round_number": round_idx,
        "instruction": payload_a["instruction"],
    }
    new_a, new_b = await asyncio.gather(
        _invoke_specialist(a, payload_a, writer, round_idx),
        _invoke_specialist(b, payload_b, writer, round_idx),
    )
    return {a: new_a, b: new_b}


def _is_conflict_resolved(
    conflict: Conflict,
    signals: dict[str, dict],
) -> bool:
    """A conflict is resolved when applying the same rule no longer fires
    on the disputant pair."""
    a, b = conflict.disputants
    sig_a, sig_b = signals[a], signals[b]
    if conflict.type == "HARD_BLOCK":
        return not (sig_a.get("hard_block") or sig_b.get("hard_block"))
    if conflict.type == "DISPOSITION_DIVERGENCE":
        return sig_a.get("disposition") != "BLOCK" \
               or sig_b.get("disposition") != "BLOCK"
    # CONFIDENCE_ASYMMETRY: resolved if confidences converge OR dispositions match
    same_d = sig_a.get("disposition") == sig_b.get("disposition")
    c_a, c_b = sig_a.get("confidence", 0), sig_b.get("confidence", 0)
    return same_d or abs(c_a - c_b) < 0.30


# ---------------------------------------------------------------------------
# Customer Supply Agent synthesis call
# ---------------------------------------------------------------------------
async def _synthesize(
    trigger_payload: TriggerPayloadV2,
    signals: dict[str, dict],
    conflicts: list[Conflict],
    writer: StepWriter,
) -> dict:
    """Customer Supply Agent receives all four signals (and conflict
    summaries) and produces the recommendation."""
    payload = {
        "order": trigger_payload.model_dump(),
        "specialist_signals": signals,
        "conflicts_detected": [c.model_dump() for c in conflicts],
        "session_id": writer.session_id,
        "instruction": ("Synthesize the four specialist signals into a "
                         "recommendation. Honor conflict resolutions; "
                         "surface deadlocks explicitly."),
    }
    return await _invoke_specialist(
        "customer_supply", payload, writer, round_idx=0)


# ---------------------------------------------------------------------------
# Public entrypoint — run a v2 session
# ---------------------------------------------------------------------------
async def run_session_v2(
    session_id: str,
    trigger_type: str,
    trigger_payload: TriggerPayloadV2,
) -> None:
    """Run the v2 5-agent N-to-N parallel + debate-on-conflict flow."""
    writer = StepWriter(session_id)
    writer.write(agent="orchestrator", action="route",
                 notes="v2 session started — fanning out to 4 specialists")

    try:
        # ---- Step 1: fan-out to specialists IN PARALLEL ----
        order_payload = {
            "order": trigger_payload.model_dump(),
            "round_number": 1,
            "instruction": ("Evaluate this order in your domain. Return your "
                             "structured signal."),
        }
        specialist_tasks = [
            _invoke_specialist(name, order_payload, writer, round_idx=1)
            for name in SPECIALIST_AGENTS
        ]
        results = await asyncio.gather(*specialist_tasks)
        signals: dict[str, dict] = dict(zip(SPECIALIST_AGENTS, results))

        writer.write(
            agent="orchestrator",
            action="route",
            round_idx=1,
            notes=("Specialist fan-out complete. Running conflict detection."),
        )

        # ---- Step 2: conflict detection ----
        conflicts = _detect_conflicts(signals)
        for c in conflicts:
            writer.write(
                agent="orchestrator",
                round_idx=1,
                action="route",
                notes=(f"Conflict detected: {c.type} between "
                       f"{' and '.join(c.disputants)} — {c.summary}"),
            )

        # ---- Step 3: debate-on-conflict (up to MAX_DEBATE_ROUNDS) ----
        for conflict in conflicts:
            for r in range(2, 2 + MAX_DEBATE_ROUNDS):
                updated = await _run_debate_round(
                    conflict, signals, writer, round_idx=r)
                signals.update(updated)
                conflict.debate_rounds_used = r - 1
                if _is_conflict_resolved(conflict, signals):
                    conflict.resolution = "RESOLVED"
                    writer.write(
                        agent="orchestrator",
                        round_idx=r,
                        action="route",
                        notes=(f"Conflict {conflict.type} between "
                               f"{' and '.join(conflict.disputants)} "
                               f"resolved at round {r}."),
                    )
                    break
            else:
                conflict.resolution = "DEADLOCK"
                writer.write(
                    agent="orchestrator",
                    action="route",
                    notes=(f"Conflict {conflict.type} between "
                           f"{' and '.join(conflict.disputants)} "
                           f"DEADLOCKED after {MAX_DEBATE_ROUNDS} rounds."),
                )

        # ---- Step 4: Customer Supply Agent synthesis ----
        writer.write(
            agent="orchestrator",
            action="route",
            notes="Synthesizing — Customer Supply Agent producing recommendation.",
        )
        decision = await _synthesize(trigger_payload, signals, conflicts,
                                       writer)
        CustomerSupplyDecision.model_validate(decision)

        # Enrich provenance
        decision.setdefault("agent_model_versions", AGENT_MODEL_VERSIONS)
        decision.setdefault("orchestrator_version", ORCHESTRATOR_VERSION)
        decision["sales_order_id"] = getattr(
            trigger_payload, "sales_order_id", None)
        decision["trigger_type"] = trigger_type

        # ---- Step 5: park awaiting human approval ----
        update_session(
            session_id,
            status="awaiting_approval",
            final_action_card=decision,
        )
        writer.write(
            agent="orchestrator",
            action="route",
            notes="Recommendation ready. Awaiting human approval.",
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
# Human approval / rejection — invokes DCE write
# ---------------------------------------------------------------------------
def approve_session_v2(
    session_id: str,
    action_card: dict,
    user_id: str,
    approval_notes: str | None,
) -> str:
    """Log to DCE and finalize. Returns decision_id."""
    payload = dict(action_card)
    payload.setdefault("trigger_type", "new_order")
    payload.setdefault("agent_model_versions", AGENT_MODEL_VERSIONS)
    payload.setdefault("orchestrator_version", ORCHESTRATOR_VERSION)

    result = dce_write(
        session_id=session_id,
        decision_payload_json=json.dumps(payload),
        user_decision="approved",
        user_id=user_id,
    )
    if "error" in result:
        raise RuntimeError(f"dce_write failed: {result['error']}")

    writer = StepWriter(session_id)
    writer.write(
        agent="human",
        action="approve",
        notes=("Approved by " + user_id
               + (f". {approval_notes}" if approval_notes else "")),
    )
    update_session(
        session_id,
        status="approved",
        decision_id=result["decision_id"],
        user_id=user_id,
        ended_at="NOW",
    )
    return result["decision_id"]


def reject_session_v2(
    session_id: str,
    action_card: dict,
    user_id: str,
    rejection_reason: str,
) -> str:
    """Log rejection to DCE and finalize. Returns decision_id."""
    payload = dict(action_card)
    payload.setdefault("trigger_type", "new_order")
    payload.setdefault("agent_model_versions", AGENT_MODEL_VERSIONS)
    payload.setdefault("orchestrator_version", ORCHESTRATOR_VERSION)

    result = dce_write(
        session_id=session_id,
        decision_payload_json=json.dumps(payload),
        user_decision="rejected",
        user_id=user_id,
        rejection_reason=rejection_reason,
    )
    if "error" in result:
        raise RuntimeError(f"dce_write failed: {result['error']}")

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

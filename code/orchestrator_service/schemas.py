"""
Pydantic schemas for agent messages and run-log entries.

Strict types matter here. ADK agents are configured with `output_schema=...`
on these models, which forces Gemini to emit JSON conformant to the shape.
Validation failures are caught by the orchestrator and routed to the error
path in Rule 6.
"""

from __future__ import annotations

from typing import Literal, Optional, Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Watchdog message
# ---------------------------------------------------------------------------
class Evidence(BaseModel):
    tool_called: str
    view_queried: str
    key_finding: str
    data_point: str


class WatchdogScope(BaseModel):
    customer_kunnr: Optional[str] = None
    customer_name: Optional[str] = None
    material_matnr: Optional[str] = None
    material_name: Optional[str] = None
    shipment_or_order_id: Optional[str] = None
    mabd: Optional[str] = None
    qty_at_risk_cs: Optional[int] = None


class WatchdogRecommendation(BaseModel):
    action: Literal["REROUTE", "EXPEDITE", "PARTIAL_FULFILL",
                    "ACCEPT_FINE", "NO_ACTION"]
    origin_plant: Optional[str] = None
    destination: Optional[str] = None
    carrier_mode: Optional[Literal["LTL", "FTL", "PARCEL", "EXPEDITE"]] = None
    expected_outcome: str


class WatchdogResponseToEconomist(BaseModel):
    decision: Literal["HOLD", "REVISE"]
    reasoning: str
    specific_counter: str


class WatchdogAlert(BaseModel):
    agent: Literal["watchdog"] = "watchdog"
    round: int = Field(ge=1, le=3)
    risk_type: Literal["OTIF_BREACH", "SHELF_LIFE", "MRSL_CONFLICT",
                       "SHORTFALL", "DEMAND_SPIKE"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    scope: WatchdogScope
    evidence: list[Evidence]
    financial_exposure_usd: float
    initial_recommendation: WatchdogRecommendation
    confidence: float = Field(ge=0.0, le=1.0)
    gaps: list[str] = Field(default_factory=list)
    reasoning_summary: str
    response_to_economist: Optional[WatchdogResponseToEconomist] = None


# ---------------------------------------------------------------------------
# Economist message
# ---------------------------------------------------------------------------
class CostOption(BaseModel):
    option_label: str
    origin_plant: Optional[str] = None
    freight_cost_usd: float
    expected_fine_usd: float
    total_expected_cost_usd: float
    viability_constraints: list[str] = Field(default_factory=list)
    notes: str = ""


class EconomistRecommendation(BaseModel):
    action: Literal["AGREE_WITH_WATCHDOG", "USE_ALTERNATIVE"]
    preferred_option_label: str
    rationale: str


class EconomistAnalysis(BaseModel):
    agent: Literal["economist"] = "economist"
    round: int = Field(ge=1, le=3)
    position: Literal["agree", "challenge"]
    watchdog_option_analysis: CostOption
    alternative_options: list[CostOption] = Field(default_factory=list)
    cost_delta_vs_watchdog_usd: float
    cost_delta_pct: float
    recommendation: EconomistRecommendation
    evidence: list[Evidence]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str


# ---------------------------------------------------------------------------
# Executor message
# ---------------------------------------------------------------------------
class RecommendedAction(BaseModel):
    action_type: Literal["REROUTE", "EXPEDITE", "PARTIAL_FULFILL",
                         "ACCEPT_FINE", "NO_ACTION"]
    origin_plant: Optional[str] = None
    origin_plant_name: Optional[str] = None
    destination: Optional[str] = None
    customer_kunnr: Optional[str] = None
    customer_name: Optional[str] = None
    material_matnr: Optional[str] = None
    material_name: Optional[str] = None
    quantity_cs: Optional[int] = None
    carrier_mode: Optional[Literal["LTL", "FTL", "PARCEL", "EXPEDITE"]] = None
    mabd: Optional[str] = None
    expected_arrival: Optional[str] = None
    estimated_freight_cost_usd: Optional[float] = None
    avoided_fine_usd: Optional[float] = None
    net_value_usd: Optional[float] = None
    shipment_or_order_id: Optional[str] = None


class ReasoningChain(BaseModel):
    watchdog_position: str
    economist_position: str
    convergence_round: int
    key_trade_offs: list[str] = Field(default_factory=list)
    watchdog_final_round: Optional[int] = None
    economist_final_round: Optional[int] = None


class Precedent(BaseModel):
    similar_decisions_count: int
    historical_approval_rate: Optional[float] = None
    note: Optional[str] = None


class DeadlockDetail(BaseModel):
    watchdog_final: str
    economist_final: str
    reason_for_deadlock: str


class ExecutorActionCard(BaseModel):
    agent: Literal["executor"] = "executor"
    session_id: str
    status: Literal["READY_FOR_APPROVAL", "DEADLOCK"]
    recommended_action: RecommendedAction
    expected_outcome: str
    reasoning_chain: ReasoningChain
    precedent: Precedent
    deadlock_detail: Optional[DeadlockDetail] = None
    approval_required_by: Literal["HUMAN", "ESCALATE_TO_DIRECTOR"] = "HUMAN"
    ready_to_log_on_approval: bool = True

    # Augmented at serialization time by the orchestrator:
    watchdog_confidence: Optional[float] = None
    economist_confidence: Optional[float] = None
    agent_model_versions: Optional[str] = None
    orchestrator_version: Optional[str] = None


# ---------------------------------------------------------------------------
# HTTP request/response models
# ---------------------------------------------------------------------------
class TriggerPayload(BaseModel):
    shipment_id: Optional[str] = None
    customer_kunnr: str
    customer_name: Optional[str] = None
    material_matnr: str
    qty_cs: int
    ship_to: Optional[str] = None
    mabd: Optional[str] = None
    default_origin_plant: Optional[str] = None


class StartSessionRequest(BaseModel):
    trigger_type: Literal["new_order", "alert_fired", "manual"] = "new_order"
    trigger_payload: TriggerPayload


class StartSessionResponse(BaseModel):
    session_id: str
    status: str


class ApprovalRequest(BaseModel):
    user_id: str
    approval_notes: Optional[str] = None


class RejectionRequest(BaseModel):
    user_id: str
    rejection_reason: str


class DecisionResponse(BaseModel):
    decision_id: str
    status: str


# ---------------------------------------------------------------------------
# Run-log step doc
# ---------------------------------------------------------------------------
class RunLogStep(BaseModel):
    step_index: int
    timestamp_iso: str
    agent: Literal["watchdog", "economist", "executor", "human",
                   "orchestrator"]
    round: Optional[int] = None
    action: Literal["tool_call", "response", "route", "approve",
                    "reject", "error"]
    tool_name: Optional[str] = None
    tool_args: Optional[dict[str, Any]] = None
    tool_result_summary: Optional[str] = None
    tool_result_full: Optional[dict[str, Any]] = None
    model_response_json: Optional[dict[str, Any]] = None
    latency_ms: Optional[int] = None
    bq_job_id: Optional[str] = None
    notes: Optional[str] = None

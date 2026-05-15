"""
Pydantic schemas for the v2 5-agent architecture.

Specialist signal schema (used by all 4 specialists, with agent-specific
`signal` payloads), Customer Supply Decision (the synthesizer's output),
conflict and debate-round structures.

Imports the v1 (3-agent POC) schemas where reusable; v1 schemas continue
to work unchanged for the POC flow at POST /sessions/poc.
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

# Reuse the v1 evidence type — same shape.
from schemas import Evidence


# ---------------------------------------------------------------------------
# Common — specialist signal wrapper
# ---------------------------------------------------------------------------
class SpecialistSignalBase(BaseModel):
    """Common envelope returned by each specialist."""
    agent: Literal["supply_planning", "demand_planning",
                   "transportation", "retail_intelligence"]
    disposition: Literal["PROCEED", "CAUTION", "BLOCK"]
    confidence: float = Field(ge=0.0, le=1.0)
    hard_block: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    reasoning_summary: str


# ---------------------------------------------------------------------------
# Supply Planning
# ---------------------------------------------------------------------------
class FGPositionByDC(BaseModel):
    plant: str
    on_hand_cs: int
    usable_cs: int
    earliest_expiry: Optional[str] = None


class FGPosition(BaseModel):
    total_on_hand_cs: int = 0
    usable_after_fefo_mrsl_cs: int = 0
    usable_short_by_cs: int = 0
    by_dc: list[FGPositionByDC] = Field(default_factory=list)


class HighestRiskRun(BaseModel):
    production_order_id: Optional[str] = None
    scheduled_completion: Optional[str] = None
    status: Optional[Literal["RELEASED", "ON_HOLD", "DELAYED"]] = None
    risk_summary: Optional[str] = None


class ProductionOrderRisk(BaseModel):
    upcoming_runs_count: int = 0
    highest_risk_run: Optional[HighestRiskRun] = None


class SupplyPlanningSignalPayload(BaseModel):
    fg_position: FGPosition
    production_order_risk: ProductionOrderRisk
    raw_material_signal: dict[str, Any] = Field(default_factory=dict)
    procurement_signal: dict[str, Any] = Field(default_factory=dict)
    safety_stock: dict[str, Any] = Field(default_factory=dict)


class SupplyPlanningSignal(SpecialistSignalBase):
    agent: Literal["supply_planning"] = "supply_planning"
    signal: SupplyPlanningSignalPayload


# ---------------------------------------------------------------------------
# Demand Planning
# ---------------------------------------------------------------------------
class ForecastAccuracySignal(BaseModel):
    trailing_mape_pct: Optional[float] = None
    trailing_bias_pct: Optional[float] = None
    plan_quality_flag: Literal[
        "HEALTHY", "SYSTEMATIC_UNDER", "SYSTEMATIC_OVER",
        "NOISY", "INSUFFICIENT_DATA"
    ] = "INSUFFICIENT_DATA"


class DemandPlanningSignalPayload(BaseModel):
    above_forecast_pct: Optional[float] = None
    demand_plan_qty_cs: Optional[int] = None
    above_forecast_classification: Literal[
        "GENUINE_PULL", "BUFFER_BUILD", "PROMO_DRIVEN",
        "SYSTEMATIC_PLAN_ERROR", "ONE_OFF_ANOMALY", "INSUFFICIENT_DATA"
    ]
    classification_confidence: float = Field(ge=0.0, le=1.0)
    classification_basis: list[str] = Field(default_factory=list)
    forecast_accuracy_signal: ForecastAccuracySignal
    demand_team_escalation_recommended: bool = False
    demand_team_escalation_reason: Optional[str] = None


class DemandPlanningSignal(SpecialistSignalBase):
    agent: Literal["demand_planning"] = "demand_planning"
    signal: DemandPlanningSignalPayload


# ---------------------------------------------------------------------------
# Transportation
# ---------------------------------------------------------------------------
class LaneEvaluation(BaseModel):
    origin_plant: Optional[str] = None
    destination_region: Optional[str] = None
    carrier_mode: Optional[Literal["LTL", "FTL", "PARCEL", "EXPEDITE"]] = None
    transit_days: float = 0.0
    buffer_days_to_mabd: float = 0.0
    estimated_freight_cost_usd: float = 0.0
    carrier_trailing_30d_otp_pct: float = 0.0
    viable: bool = False
    notes: Optional[str] = None


class FineAndFeeExposure(BaseModel):
    fine_rate_usd_per_cs: float = 0.0
    trailing_90d_chargebacks_usd: float = 0.0
    exposure_if_miss_full_qty_usd: float = 0.0
    exposure_if_miss_partial_qty_usd: Optional[float] = None


class CustomerOtifPosition(BaseModel):
    trailing_90d_otif_pct: float = 0.0
    customer_otif_target_pct: float = 0.0
    delta_to_target_pct: float = 0.0


class LaneAlert(BaseModel):
    alert_id: str
    summary: str
    severity: str


class TransportationSignalPayload(BaseModel):
    primary_lane: LaneEvaluation
    alternative_lanes: list[LaneEvaluation] = Field(default_factory=list)
    fine_and_fee_exposure: FineAndFeeExposure
    customer_otif_position: CustomerOtifPosition
    active_lane_alerts: list[LaneAlert] = Field(default_factory=list)


class TransportationSignal(SpecialistSignalBase):
    agent: Literal["transportation"] = "transportation"
    signal: TransportationSignalPayload


# ---------------------------------------------------------------------------
# Retail Intelligence
# ---------------------------------------------------------------------------
class RetailerDCInventoryPosition(BaseModel):
    """Retailer warehouse / DC level on-hand and days-of-supply.
    Drives buffer-build hints and DOS-aware MABD prioritization."""
    data_available: bool = False
    aggregate_on_hand_units: Optional[int] = None
    aggregate_days_of_supply: Optional[float] = None
    dc_count: Optional[int] = None
    dos_trend_4w: Optional[
        Literal["DECLINING", "FLAT", "BUILDING"]
    ] = None
    above_normal_days_of_supply: Optional[bool] = None


class RetailerStoreInventoryPosition(BaseModel):
    """Retailer store-level on-hand. Drives OOS / lost-sales signals.
    Volume is 10-100x DC inventory — one row per store."""
    data_available: bool = False
    stores_reporting: Optional[int] = None
    stores_with_oos: Optional[int] = None
    oos_rate_pct: Optional[float] = None
    aggregate_on_hand_units: Optional[int] = None


class RetailerPOSVelocity(BaseModel):
    """V3-deferred stub. POS depletion when retailer velocity feeds land
    (beyond the initial retail-inventory v3 release)."""
    data_available: bool = False
    trailing_4w_avg_units_per_week: Optional[float] = None
    trend_vs_prior_8w: Optional[
        Literal["ACCELERATING", "FLAT", "DECELERATING"]
    ] = None


class MRSLCompliance(BaseModel):
    customer_mrsl_days_required: int = 0
    fg_satisfies_mrsl: bool = True
    non_compliant_dcs: list[str] = Field(default_factory=list)
    fg_at_risk_cs: int = 0


class RetailIntelligenceSignalPayload(BaseModel):
    pull_vs_buffer_classification: Literal[
        "GENUINE_PULL", "BUFFER_BUILD", "SHELF_RESET_or_PROMO",
        "INSUFFICIENT_DATA"
    ]
    classification_confidence: float = Field(ge=0.0, le=1.0)
    classification_basis: list[str] = Field(default_factory=list)
    dc_inventory_position: RetailerDCInventoryPosition
    store_inventory_position: RetailerStoreInventoryPosition
    retailer_pos_velocity: RetailerPOSVelocity
    mrsl_compliance: MRSLCompliance
    data_gaps: list[str] = Field(default_factory=list)


class RetailIntelligenceSignal(SpecialistSignalBase):
    agent: Literal["retail_intelligence"] = "retail_intelligence"
    signal: RetailIntelligenceSignalPayload


# ---------------------------------------------------------------------------
# Conflicts and debate
# ---------------------------------------------------------------------------
class Conflict(BaseModel):
    type: Literal["HARD_BLOCK", "DISPOSITION_DIVERGENCE",
                  "CONFIDENCE_ASYMMETRY"]
    disputants: list[str]
    summary: str
    debate_rounds_used: int = 0
    resolution: Literal["UNRESOLVED", "RESOLVED", "DEADLOCK"] = "UNRESOLVED"


class DebateMessage(BaseModel):
    """Sent to a disputant during a debate round."""
    your_previous_signal: dict[str, Any]
    disputant_position: dict[str, Any]
    round_number: int = Field(ge=2, le=3)
    instruction: str = (
        "Read the disputant's position. If their data is genuinely new "
        "and material, REVISE your signal. Otherwise HOLD and cite the "
        "specific data they did not have."
    )


# ---------------------------------------------------------------------------
# Customer Supply Decision (the synthesizer's output)
# ---------------------------------------------------------------------------
class OrderContext(BaseModel):
    customer_kunnr: str
    customer_name: Optional[str] = None
    material_matnr: str
    material_name: Optional[str] = None
    ordered_qty_cs: int
    demand_plan_qty_cs: Optional[int] = None
    forecast_classification: Literal["WITHIN_FORECAST", "ABOVE_FORECAST",
                                      "UNKNOWN"] = "UNKNOWN"
    above_forecast_pct: Optional[float] = None
    mabd: Optional[str] = None
    ship_to: Optional[str] = None


class AlternativeOption(BaseModel):
    label: str
    fulfill_qty_cs: int
    estimated_cost_usd: float
    estimated_fine_avoidance_usd: float
    viable: bool


class Recommendation(BaseModel):
    action: Literal["ACCEPT", "REJECT", "PARTIAL_FULFILL", "DEFER"]
    fulfill_qty_cs: int = 0
    partial_fill_pct: Optional[float] = None
    alternative_options: list[AlternativeOption] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    expected_outcome: str


class ReasoningChain(BaseModel):
    which_specialists_drove_decision: list[str] = Field(default_factory=list)
    key_trade_offs: list[str] = Field(default_factory=list)
    what_would_change_the_decision: str


class Escalation(BaseModel):
    summary: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    recommended_action: str


class Escalations(BaseModel):
    to_transportation_manager: Optional[Escalation] = None
    to_demand_planning_team: Optional[Escalation] = None
    to_supply_planning_team: Optional[Escalation] = None


class DCEPayload(BaseModel):
    cdm_domains_referenced: list[str] = Field(default_factory=list)
    scenario_tag: Optional[str] = None


class CustomerSupplyDecision(BaseModel):
    agent: Literal["customer_supply"] = "customer_supply"
    session_id: str
    order: OrderContext
    specialist_signals: dict[str, dict[str, Any]]
    conflicts_detected: list[Conflict] = Field(default_factory=list)
    recommendation: Recommendation
    reasoning_chain: ReasoningChain
    escalations: Escalations = Field(default_factory=Escalations)
    dce_payload: DCEPayload = Field(default_factory=DCEPayload)
    ready_to_present_to_human: bool = True


# ---------------------------------------------------------------------------
# HTTP request models for v2 (deck-aligned 5-agent flow)
# ---------------------------------------------------------------------------
class TriggerPayloadV2(BaseModel):
    customer_kunnr: str
    customer_name: Optional[str] = None
    material_matnr: str
    material_name: Optional[str] = None
    ordered_qty_cs: int
    mabd: Optional[str] = None
    ship_to: Optional[str] = None
    sales_order_id: Optional[str] = None


class StartSessionRequestV2(BaseModel):
    trigger_type: Literal["new_order", "alert_fired", "manual"] = "new_order"
    trigger_payload: TriggerPayloadV2


class StartSessionResponseV2(BaseModel):
    session_id: str
    status: str
    flow_mode: Literal["five_agent", "poc"] = "five_agent"

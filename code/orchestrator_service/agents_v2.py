"""
ADK agent factory for the v2 5-agent build.

Each factory returns a configured LlmAgent. System prompts loaded from
agents/*_v2.md. The v1 factory (agents.py) is preserved alongside and
remains the source of truth for the POC flow at POST /sessions/poc.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import LlmAgent

from adk_tools_v2 import (
    CUSTOMER_SUPPLY_TOOLS_V2,
    SUPPLY_PLANNING_TOOLS_V2,
    DEMAND_PLANNING_TOOLS_V2,
    TRANSPORTATION_TOOLS_V2,
    RETAIL_INTELLIGENCE_TOOLS_V2,
)
from schemas_v2 import (
    CustomerSupplyDecision,
    SupplyPlanningSignal,
    DemandPlanningSignal,
    TransportationSignal,
    RetailIntelligenceSignal,
)


PROMPTS_DIR = Path(__file__).parent.parent.parent / "agents"


def _load_prompt(name: str) -> str:
    """Load a system prompt from agents/{name}.md, stripping the markdown
    wrapper to the inner ``` text block. Same pattern as v1."""
    path = PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            block = parts[1]
            if block.startswith("text"):
                block = block[4:]
            return block.strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Customer Supply Agent (synthesizer / orchestrator-of-agents)
# ---------------------------------------------------------------------------
def make_customer_supply_v2() -> LlmAgent:
    return LlmAgent(
        name="customer_supply",
        model="gemini-2.5-pro",
        generate_content_config={"temperature": 0.2},
        description=(
            "Synthesizer. Receives the PO, fires 4 specialists in parallel, "
            "runs conflict detection, debate-on-conflict, and produces the "
            "human-facing recommendation card."
        ),
        instruction=_load_prompt("customer_supply_agent_v2"),
        tools=CUSTOMER_SUPPLY_TOOLS_V2,
        output_schema=CustomerSupplyDecision,
    )


# ---------------------------------------------------------------------------
# Supply Planning Agent
# ---------------------------------------------------------------------------
def make_supply_planning_v2() -> LlmAgent:
    return LlmAgent(
        name="supply_planning",
        model="gemini-2.5-flash",
        generate_content_config={"temperature": 0.1},
        description=(
            "Production order execution risk, raw material adequacy, "
            "FEFO/MRSL on finished goods, safety stock."
        ),
        instruction=_load_prompt("supply_planning_agent_v2"),
        tools=SUPPLY_PLANNING_TOOLS_V2,
        output_schema=SupplyPlanningSignal,
    )


# ---------------------------------------------------------------------------
# Demand Planning Agent
# ---------------------------------------------------------------------------
def make_demand_planning_v2() -> LlmAgent:
    return LlmAgent(
        name="demand_planning",
        model="gemini-2.5-pro",
        generate_content_config={"temperature": 0.2},
        description=(
            "Forecast vs actual gap analysis, above-forecast classification "
            "(systematic vs anomaly), retail velocity validation (v3)."
        ),
        instruction=_load_prompt("demand_planning_agent_v2"),
        tools=DEMAND_PLANNING_TOOLS_V2,
        output_schema=DemandPlanningSignal,
    )


# ---------------------------------------------------------------------------
# Transportation Agent
# ---------------------------------------------------------------------------
def make_transportation_v2() -> LlmAgent:
    return LlmAgent(
        name="transportation",
        model="gemini-2.5-flash",
        generate_content_config={"temperature": 0.1},
        description=(
            "OTIF risk by account, lane viability, carrier OTP, fine and "
            "fee exposure. Influences customer-supply decisions; does not "
            "own OTIF."
        ),
        instruction=_load_prompt("transportation_agent_v2"),
        tools=TRANSPORTATION_TOOLS_V2,
        output_schema=TransportationSignal,
    )


# ---------------------------------------------------------------------------
# Retail Intelligence Agent (v2 returns INSUFFICIENT_DATA until v3 lands)
# ---------------------------------------------------------------------------
def make_retail_intelligence_v2() -> LlmAgent:
    return LlmAgent(
        name="retail_intelligence",
        model="gemini-2.5-pro",
        generate_content_config={"temperature": 0.2},
        description=(
            "Retailer inventory & POS velocity reader; classifies orders "
            "as genuine pull vs buffer build. MRSL compliance per shipment. "
            "Honest about v2 data gap; v3 brings classification to full "
            "confidence."
        ),
        instruction=_load_prompt("retail_intelligence_agent_v2"),
        tools=RETAIL_INTELLIGENCE_TOOLS_V2,
        output_schema=RetailIntelligenceSignal,
    )


# ---------------------------------------------------------------------------
# Singleton accessors
# ---------------------------------------------------------------------------
_AGENTS_V2: dict[str, LlmAgent] = {}


def get_agent_v2(name: str) -> LlmAgent:
    if name in _AGENTS_V2:
        return _AGENTS_V2[name]
    factories = {
        "customer_supply":     make_customer_supply_v2,
        "supply_planning":     make_supply_planning_v2,
        "demand_planning":     make_demand_planning_v2,
        "transportation":      make_transportation_v2,
        "retail_intelligence": make_retail_intelligence_v2,
    }
    if name not in factories:
        raise ValueError(f"Unknown v2 agent: {name}")
    _AGENTS_V2[name] = factories[name]()
    return _AGENTS_V2[name]


SPECIALIST_AGENTS = (
    "supply_planning",
    "demand_planning",
    "transportation",
    "retail_intelligence",
)

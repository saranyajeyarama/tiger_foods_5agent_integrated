"""
ADK agent factory.

Instantiates the three primary agents with their model, temperature, tool list,
system prompt (loaded from agents/*.md), and output schema. Returned LlmAgent
objects are invoked by the orchestrator.

Model versions:
  Watchdog:  gemini-2.5-flash  (speed for the frequent path)
  Economist: gemini-2.5-pro    (deeper reasoning, cost math)
  Executor:  gemini-2.5-pro    (synthesis, low temperature)
"""

from __future__ import annotations

import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.models import Gemini

from adk_tools import WATCHDOG_TOOLS, ECONOMIST_TOOLS, EXECUTOR_TOOLS
from schemas import WatchdogAlert, EconomistAnalysis, ExecutorActionCard


PROMPTS_DIR = Path(__file__).parent.parent.parent / "agents"


def _load_prompt(name: str) -> str:
    """Load a system prompt from agents/{name}.md, stripping markdown wrapper."""
    path = PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    # Extract the text block (between the first ``` and the last ```).
    if "```" in text:
        # Find the first fenced block (the system prompt itself).
        parts = text.split("```")
        # parts[0] is preamble, parts[1] is "text\n<prompt>", parts[2] is suffix.
        if len(parts) >= 2:
            block = parts[1]
            # Strip a leading "text" language marker if present.
            if block.startswith("text"):
                block = block[4:]
            return block.strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------
def make_watchdog() -> LlmAgent:
    return LlmAgent(
        name="watchdog",
        model=Gemini(
            model_name="gemini-2.5-flash",
            temperature=0.1,
        ),
        description=(
            "Risk detector. Reads live Tiger Foods supply data. "
            "Flags OTIF/CFR/shelf-life risks and produces a structured alert."
        ),
        instruction=_load_prompt("watchdog_system_prompt"),
        tools=WATCHDOG_TOOLS,
        output_schema=WatchdogAlert,
    )


# ---------------------------------------------------------------------------
# Economist
# ---------------------------------------------------------------------------
def make_economist() -> LlmAgent:
    return LlmAgent(
        name="economist",
        model=Gemini(
            model_name="gemini-2.5-pro",
            temperature=0.2,
        ),
        description=(
            "Cost optimizer and challenger. Computes the cost of Watchdog's "
            "recommendation plus alternatives, and challenges when math "
            "diverges by more than 5%."
        ),
        instruction=_load_prompt("economist_system_prompt"),
        tools=ECONOMIST_TOOLS,
        output_schema=EconomistAnalysis,
    )


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------
def make_executor() -> LlmAgent:
    return LlmAgent(
        name="executor",
        model=Gemini(
            model_name="gemini-2.5-pro",
            temperature=0.0,
        ),
        description=(
            "Synthesizer. Consolidates Watchdog and Economist final positions "
            "into a structured action card for human approval."
        ),
        instruction=_load_prompt("executor_system_prompt"),
        tools=EXECUTOR_TOOLS,
        output_schema=ExecutorActionCard,
    )


# ---------------------------------------------------------------------------
# Convenience: singleton accessor used by the orchestrator
# ---------------------------------------------------------------------------
_AGENTS: dict[str, LlmAgent] = {}


def get_agent(name: str) -> LlmAgent:
    if name not in _AGENTS:
        if name == "watchdog":
            _AGENTS[name] = make_watchdog()
        elif name == "economist":
            _AGENTS[name] = make_economist()
        elif name == "executor":
            _AGENTS[name] = make_executor()
        else:
            raise ValueError(f"Unknown agent name: {name}")
    return _AGENTS[name]

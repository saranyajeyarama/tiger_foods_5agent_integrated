"""
Thin BigQuery client wrapper for orchestrator-level writes.

The agent-facing query work lives in `adk_tools.py`. This module is for
orchestrator-initiated writes that should not be exposed to agents:

  - Writing the approved/rejected decision to tiger_decisions.fct_allocation_decisions
    (delegated to adk_tools.log_decision, but called from the orchestrator,
    not from an agent's tool list)

Keeping this thin and orchestrator-only means the agents' tool surface stays
audit-clean: no write tool is bound to any agent.
"""

from __future__ import annotations

import os
from google.cloud import bigquery

PROJECT_ID = os.environ.get("PROJECT_ID", "resilience-riskradar")
_bq = bigquery.Client(project=PROJECT_ID)


def get_client() -> bigquery.Client:
    return _bq

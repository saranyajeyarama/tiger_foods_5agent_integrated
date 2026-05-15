# =============================================================================
# Tiger Foods Agentic AI — Orchestrator service (integrated v2)
# =============================================================================
# Dual-mode container: serves both the 5-agent flow (POST /sessions) and the
# 3-agent POC flow (POST /sessions/poc).
#
# Build context: the package root (containing agents/, code/, infra/, ...).
# The Dockerfile itself lives at code/orchestrator_service/Dockerfile.
#
# Cloud Run / Cloud Build:
#   gcloud builds submit . \
#     --tag <REGION>-docker.pkg.dev/<PROJECT>/tiger-agents/orchestrator:v2.0.0 \
#     --file code/orchestrator_service/Dockerfile
#
# Local Docker build:
#   docker build -t tiger-agents-v2 \
#     -f code/orchestrator_service/Dockerfile .
# =============================================================================

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Python deps
COPY code/orchestrator_service/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /app/requirements.txt

# v1 modules (preserved — POC flow at POST /sessions/poc)
COPY code/orchestrator_service/schemas.py            /app/schemas.py
COPY code/orchestrator_service/agents.py             /app/agents.py
COPY code/orchestrator_service/orchestrator.py       /app/orchestrator.py
COPY code/orchestrator_service/firestore_client.py   /app/firestore_client.py
COPY code/orchestrator_service/bigquery_client.py    /app/bigquery_client.py
COPY code/orchestrator_service/main.py               /app/main.py
COPY code/adk_tools.py                                /app/adk_tools.py

# v2 modules (5-agent flow at POST /sessions, default)
COPY code/orchestrator_service/schemas_v2.py         /app/schemas_v2.py
COPY code/orchestrator_service/agents_v2.py          /app/agents_v2.py
COPY code/orchestrator_service/orchestrator_v2.py    /app/orchestrator_v2.py
COPY code/orchestrator_service/main_v2.py            /app/main_v2.py
COPY code/adk_tools_v2.py                             /app/adk_tools_v2.py

# Agent prompts (both v1 POC and v2 5-agent — loaded by name at agent init)
COPY agents/  /app/agents/

ENV PROMPTS_DIR=/app/agents

EXPOSE 8080

# main_v2:app is the dual-mode entry point — exposes both /sessions and /sessions/poc
CMD ["uvicorn", "main_v2:app", "--host", "0.0.0.0", "--port", "8080"]

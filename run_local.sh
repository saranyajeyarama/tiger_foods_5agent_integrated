#!/usr/bin/env bash
# =============================================================================
# Tiger Foods Agentic AI — local dev runner
# =============================================================================
# Resolves the PYTHONPATH and PROMPTS_DIR setup so the integrated package can
# be run locally with one command from the package root.
#
# Usage:
#   bash run_local.sh                  # starts uvicorn on port 8080 with reload
#   PORT=9000 bash run_local.sh        # override port
#
# Prerequisites:
#   - Python 3.12+
#   - GCP credentials configured via one of:
#       gcloud auth application-default login          (user creds)
#       export GOOGLE_APPLICATION_CREDENTIALS=...      (service account JSON)
#   - Dependencies installed:
#       pip install -r code/orchestrator_service/requirements.txt
# =============================================================================

set -euo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8080}"

export PYTHONPATH="${PACKAGE_ROOT}/code:${PACKAGE_ROOT}/code/orchestrator_service${PYTHONPATH:+:${PYTHONPATH}}"
export PROMPTS_DIR="${PACKAGE_ROOT}/agents"
export PROJECT_ID="${PROJECT_ID:-resilience-riskradar}"

echo "Tiger Foods integrated v2 — starting locally"
echo "  PACKAGE_ROOT = ${PACKAGE_ROOT}"
echo "  PYTHONPATH   = ${PYTHONPATH}"
echo "  PROMPTS_DIR  = ${PROMPTS_DIR}"
echo "  PROJECT_ID   = ${PROJECT_ID}"
echo "  PORT         = ${PORT}"
echo ""
echo "Endpoints:"
echo "  POST   http://localhost:${PORT}/sessions          (5-agent flow, default)"
echo "  POST   http://localhost:${PORT}/sessions/poc      (3-agent POC flow)"
echo "  GET    http://localhost:${PORT}/sessions/{id}"
echo "  POST   http://localhost:${PORT}/sessions/{id}/approve"
echo "  POST   http://localhost:${PORT}/sessions/{id}/reject"
echo "  GET    http://localhost:${PORT}/health"
echo ""

cd "${PACKAGE_ROOT}/code/orchestrator_service"
exec uvicorn main_v2:app --host 0.0.0.0 --port "${PORT}" --reload

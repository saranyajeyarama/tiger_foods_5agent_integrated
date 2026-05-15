#!/usr/bin/env bash
# =============================================================================
# Tiger Foods Agentic AI — integrated infra bootstrap
# =============================================================================
# Applies the full infra stack in the correct order:
#   1. IAM — service account, BigQuery + Firestore roles
#   2. Base table — fct_allocation_decisions
#   3. DCE extension — v2 columns via ALTER TABLE
#   4. CMIR raw sources — sap_knmt, sap_mara, sap_mean, sap_mvke
#   5. CMIR semantic view — dim_customer_material
#   6. External crosswalk view + TVF — dim_external_product_crosswalk
#
# Idempotent: every statement uses IF NOT EXISTS or CREATE OR REPLACE.
# Safe to re-run on top of an existing v1 deployment.
#
# Usage:
#   bash infra/bootstrap_integrated.sh [PROJECT_ID]
#
# Default PROJECT_ID: resilience-riskradar
# =============================================================================

set -euo pipefail

PROJECT_ID="${1:-${PROJECT_ID:-resilience-riskradar}}"
INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Tiger Foods integrated bootstrap — project: ${PROJECT_ID}"
echo ""

# --- 1. IAM (skip if already done; iam.sh is itself idempotent) -------------
echo "[1/6] Applying IAM (idempotent)..."
bash "${INFRA_DIR}/iam.sh" "${PROJECT_ID}" || {
  echo "[1/6] IAM step failed — verify gcloud auth and project permissions"
  exit 1
}

# --- 2. Base decision table --------------------------------------------------
echo "[2/6] Creating fct_allocation_decisions (idempotent)..."
bq --project_id="${PROJECT_ID}" query \
   --use_legacy_sql=false --format=none --batch=false \
   < "${INFRA_DIR}/decision_log_table.sql"

# --- 3. DCE column extension -------------------------------------------------
echo "[3/6] Applying DCE schema extension..."
bq --project_id="${PROJECT_ID}" query \
   --use_legacy_sql=false --format=none --batch=false \
   < "${INFRA_DIR}/dce_alter_table_v2.sql"

# --- 4. CMIR raw source tables -----------------------------------------------
echo "[4/6] Creating CMIR raw source tables..."
bq --project_id="${PROJECT_ID}" query \
   --use_legacy_sql=false --format=none --batch=false \
   < "${INFRA_DIR}/cmir_raw_sources_v2.sql"

# --- 5. CMIR semantic view ---------------------------------------------------
echo "[5/6] Creating dim_customer_material semantic view..."
bq --project_id="${PROJECT_ID}" query \
   --use_legacy_sql=false --format=none --batch=false \
   < "${INFRA_DIR}/dim_customer_material_v2.sql"

# --- 6. External crosswalk + resolution TVF ----------------------------------
echo "[6/6] Creating dim_external_product_crosswalk + resolve_external_to_internal TVF..."
bq --project_id="${PROJECT_ID}" query \
   --use_legacy_sql=false --format=none --batch=false \
   < "${INFRA_DIR}/dim_external_product_crosswalk_v2.sql"

# --- Verification ------------------------------------------------------------
echo ""
echo "Verification — checking new DCE columns on fct_allocation_decisions:"
bq --project_id="${PROJECT_ID}" query --use_legacy_sql=false --format=pretty \
"SELECT column_name, data_type
 FROM \`${PROJECT_ID}.tiger_decisions.INFORMATION_SCHEMA.COLUMNS\`
 WHERE table_name = 'fct_allocation_decisions'
   AND column_name IN (
     'flow_mode', 'agent_recommendation', 'agent_confidence_score',
     'user_decision', 'decision_aligned_with_agent',
     'user_modification_notes', 'cdm_domains_referenced',
     'outcome_cfr_impact_cs', 'outcome_fine_avoided_usd', 'scenario_tag'
   )
 ORDER BY column_name;"

echo ""
echo "Bootstrap complete. Next: build and deploy the Cloud Run service per README."

#!/usr/bin/env bash
# Tiger Foods Agentic AI — IAM grants for the service account.
#
# This file is the SINGLE SOURCE OF TRUTH for what the agents can touch.
# The deliberate omissions matter:
#   - NO grant on tiger_foods_raw (bronze, SAP-derived)
#   - dataViewer on tiger_semantic only
#   - dataEditor on tiger_decisions only
#   - jobUser at project level (needed to run BigQuery jobs)
#
# Inputs (via env or default):
#   PROJECT_ID  default: resilience-riskradar
#   SA_NAME     default: tiger-agents-sa

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-resilience-riskradar}"
SA_NAME="${SA_NAME:-tiger-agents-sa}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Helper
add_project_role () {
  local role="$1"
  echo "    [project] ${role}"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
}

add_dataset_role () {
  local dataset="$1"
  local role="$2"
  echo "    [dataset:${dataset}] ${role}"
  # Use bq update with merged IAM policy. Safer than rewriting the dataset.
  # Format: <project>:<dataset>
  local tmp
  tmp="$(mktemp)"
  bq show --format=prettyjson "${PROJECT_ID}:${dataset}" > "${tmp}"
  # Compose the access entry if missing.
  python3 - <<PY
import json, sys
path = "${tmp}"
sa = "serviceAccount:${SA_EMAIL}"
role = "${role}"
with open(path) as f:
    d = json.load(f)
access = d.get("access", [])
exists = any(a for a in access
             if a.get("role") == role
             and a.get("userByEmail") == "${SA_EMAIL}")
if not exists:
    access.append({"role": role, "userByEmail": "${SA_EMAIL}"})
    d["access"] = access
with open(path, "w") as f:
    json.dump(d, f, indent=2)
PY
  bq update --source="${tmp}" "${PROJECT_ID}:${dataset}"
  rm -f "${tmp}"
}

# ============================================================
# 1. Vertex AI — invoke Gemini models
# ============================================================
echo "==> Vertex AI"
add_project_role "roles/aiplatform.user"

# ============================================================
# 2. BigQuery — project-level jobUser (needed to run queries at all)
# ============================================================
echo "==> BigQuery project-level"
add_project_role "roles/bigquery.jobUser"

# ============================================================
# 3. BigQuery — dataset-level grants
# ============================================================
echo "==> BigQuery dataset grants (scoped — no access to tiger_foods_raw)"
add_dataset_role "tiger_semantic" "roles/bigquery.dataViewer"
add_dataset_role "tiger_decisions" "roles/bigquery.dataEditor"

# ============================================================
# 4. Firestore (Datastore mode role covers Firestore Native)
# ============================================================
echo "==> Firestore"
add_project_role "roles/datastore.user"

# ============================================================
# 5. Secret Manager — for any future secrets (API keys, etc.)
# ============================================================
echo "==> Secret Manager"
add_project_role "roles/secretmanager.secretAccessor"

# ============================================================
# 6. Cloud Run — invoke (used by upstream services calling this orchestrator)
# ============================================================
# Not granted to the agent SA — this SA is the RUNTIME SA, not the caller SA.

echo "==> IAM grants complete."
echo
echo "Verification: confirm the service account has NO access to tiger_foods_raw:"
echo "  bq show --format=prettyjson ${PROJECT_ID}:tiger_foods_raw | grep -A1 ${SA_EMAIL} || echo 'NO ACCESS — correct.'"

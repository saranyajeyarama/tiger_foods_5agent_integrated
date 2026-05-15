# Tiger Foods Agentic AI — Integrated v2 Package

**For:** Mars Pet Nutrition — Customer Supply Operations
**Stack:** Google Cloud Platform — Vertex AI ADK + Gemini 2.5
**Architecture:** 5 domain-specialist agents (deck-aligned), N-to-N parallel orchestration, with the 3-agent POC preserved at `/sessions/poc`
**Status:** Self-contained — no external package dependencies

This package consolidates the v1 POC and v2 5-agent build into one deployable unit. The earlier two-package layout required engineering teams to install the v1 POC and v2 5-agent packages side-by-side and manage PYTHONPATH between them; this version eliminates that step. Drop the package, build the container, deploy.

If you've already been working with the separate `tiger_foods_agentic/` (v1) and `tiger_foods_5agent/` (v2) packages, this is the same code merged into a single directory tree — same modules, same agent prompts, same endpoints — just consolidated.

## Audience routing

| You are... | Read first |
|---|---|
| Engineer setting this up | This README + `briefs/03_engineering_implementation_brief_v2.md` |
| Mars stakeholder | `briefs/00_director_brief_v2.md` |
| Persona owner / consultant | `briefs/01_consultant_brief_v2.md` |
| Architect doing a deep dive | `briefs/02_architect_technical_requirements_v2.md` |
| Running the demo | `reference/walmart_demo_scenario_v2.md` |
| Tracing data lineage | `reference/cdm_domain_mapping_v2.md` |
| CMIR / crosswalk foundation | `reference/cmir_and_crosswalk_v2.md` + `reference/external_product_mapping_v2.md` |
| Looking at the retail data gap | `reference/retail_data_gap_v2.md` |
| Running acceptance tests | `reference/test_plan_v2.md` |

## Package layout

```
tiger_foods_5agent_integrated/
├── README.md                                    (this file)
├── briefs/                                      (4 audience-routed briefs)
├── agents/                                      (LLM system prompts — both v1 and v2)
│   ├── watchdog_system_prompt.md                (v1 POC)
│   ├── economist_system_prompt.md               (v1 POC)
│   ├── executor_system_prompt.md                (v1 POC)
│   ├── orchestrator_system_prompt.md            (v1 POC)
│   ├── customer_supply_agent_v2.md              (v2 — synthesizer)
│   ├── supply_planning_agent_v2.md              (v2)
│   ├── demand_planning_agent_v2.md              (v2)
│   ├── transportation_agent_v2.md               (v2)
│   └── retail_intelligence_agent_v2.md          (v2)
├── code/
│   ├── adk_tools.py                             (v1 base tools)
│   ├── adk_tools_v2.py                          (v2 — imports v1 base, adds 5-agent tools)
│   └── orchestrator_service/
│       ├── schemas.py                           (v1 — POC Pydantic types)
│       ├── schemas_v2.py                        (v2 — 5-agent Pydantic types)
│       ├── firestore_client.py                  (shared — session state + run log)
│       ├── bigquery_client.py                   (shared — connection helper)
│       ├── agents.py                            (v1 — Watchdog/Economist/Executor factory)
│       ├── agents_v2.py                         (v2 — 5-agent factory)
│       ├── orchestrator.py                      (v1 — sequential 3-agent debate)
│       ├── orchestrator_v2.py                   (v2 — N-to-N parallel + debate-on-conflict)
│       ├── main.py                              (v1 — POC-only FastAPI; preserved as reference)
│       ├── main_v2.py                           (v2 — DUAL-MODE FastAPI; production entry point)
│       ├── Dockerfile                           (single canonical build)
│       └── requirements.txt
├── infra/
│   ├── decision_log_table.sql                   (v1 — base fct_allocation_decisions)
│   ├── dce_alter_table_v2.sql                   (v2 — DCE columns ADD COLUMN IF NOT EXISTS)
│   ├── firestore_indexes.json                   (v1 — session/step indexes)
│   ├── iam.sh                                   (v1 — service account + permissions)
│   ├── cmir_raw_sources_v2.sql                  (v2 — SAP CMIR raw tables)
│   ├── dim_customer_material_v2.sql             (v2 — CMIR semantic view)
│   └── dim_external_product_crosswalk_v2.sql    (v2 — internal-external crosswalk)
└── reference/                                   (12 reference docs — v1 + v2 combined)
```

## How the v1 + v2 integration works

The v2 modules deliberately import from v1 — this is intentional reuse, not coincidence:

| v2 module | What it imports from v1 |
|---|---|
| `schemas_v2.py` | `from schemas import Evidence` |
| `adk_tools_v2.py` | `_bq`, `_run_query`, `PROJECT_ID`, `SEMANTIC_DS`, `DECISIONS_DS`, plus 6 base tools (`get_otif_performance`, `get_chargeback_risk`, `get_transfer_cost_comparison`, `get_allocation_history`, `get_active_alerts`, `get_shelf_life_risk`) |
| `orchestrator_v2.py` | `from firestore_client import StepWriter, update_session` |
| `main_v2.py` | v1's `run_session`, `approve_session`, `reject_session` (for the POC endpoint), plus v1 schema types |

When you build the Dockerfile, both sets of modules land flat in `/app`, so all flat imports resolve. For local development, the same is true if you run from `code/orchestrator_service/` with `code/` on PYTHONPATH (handled by `run_local.sh` below).

## Endpoints exposed by `main_v2:app`

| Endpoint | Flow | Audience |
|---|---|---|
| `POST /sessions` | **5-agent (default)** — deck-aligned, parallel fan-out with debate-on-conflict | Mars-facing demo and production |
| `POST /sessions/poc` | 3-agent — Watchdog/Economist/Executor sequential debate | POC reference, simpler pilot |
| `GET /sessions/{id}` | Both | Read session state |
| `POST /sessions/{id}/approve` | Both — routes by `flow_mode` stored in session | Human approval |
| `POST /sessions/{id}/reject` | Both | Human rejection |

Both flows write to the same `fct_allocation_decisions` table, distinguished by the `flow_mode` column added by `dce_alter_table_v2.sql`.

## Quick start — Cloud Run deployment

```bash
# 1. Apply infra in one command (idempotent; safe to re-run on top of v1 setup)
bash infra/bootstrap_integrated.sh resilience-riskradar

# 2. Build and deploy — build context is the package root (note the trailing dot)
gcloud builds submit . \
  --tag us-central1-docker.pkg.dev/resilience-riskradar/tiger-agents/orchestrator:v2.0.0 \
  --file code/orchestrator_service/Dockerfile

gcloud run deploy tiger-agents-orchestrator \
  --image us-central1-docker.pkg.dev/resilience-riskradar/tiger-agents/orchestrator:v2.0.0 \
  --region us-central1 \
  --service-account tiger-agents-sa@resilience-riskradar.iam.gserviceaccount.com \
  --set-env-vars=PROJECT_ID=resilience-riskradar,PROMPTS_DIR=/app/agents \
  --memory 2Gi --cpu 2 --concurrency 10 --timeout 300s \
  --no-allow-unauthenticated

# 3. Run the Walmart Pedigree demo
curl -X POST <service-url>/sessions \
  -H "Content-Type: application/json" \
  -d @reference/walmart_payload.json
```

## Quick start — local development

```bash
# Authenticate to GCP (one of):
gcloud auth application-default login                                       # user creds
# OR
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json     # service account

# Install dependencies
cd code/orchestrator_service
pip install -r requirements.txt

# Run — the helper script sets PYTHONPATH and PROMPTS_DIR correctly
bash ../../run_local.sh
```

`run_local.sh` is included at the package root. It exports `PYTHONPATH` to include both `code/` and `code/orchestrator_service/`, sets `PROMPTS_DIR` to the absolute path of the `agents/` directory, and starts uvicorn against `main_v2:app` with reload enabled.

## Technical reference

For the deep-dive material — model assignments, conflict detection rules, debate mechanics, performance characteristics, failure handling, observability, deployment commands, acceptance criteria — see `briefs/02_architect_technical_requirements_v2.md`. The architecture brief was written against the standalone v2 package but every code path it describes is identical in this integrated version.

For onboarding the AI/ML engineering team, `briefs/03_engineering_implementation_brief_v2.md` covers process, Vertex AI prep, and the iteration patterns we used.

## What's in scope and what's not

The integrated package preserves the same scope boundaries as the standalone v2:

- ✅ 5-agent N-to-N parallel orchestration with conflict detection and bounded debate
- ✅ Decision Capture Engine extension to `fct_allocation_decisions`
- ✅ SAP CMIR foundation (KNMT + MARA + MEAN + MVKE → `dim_customer_material`)
- ✅ Internal-external crosswalk with archetype A/B/C resolution TVF
- ✅ Walmart Pedigree Dry 22lb demo scenario
- ✅ 3-agent POC preserved at `/sessions/poc`
- ⚠️ Retail Intelligence Agent runs degraded (INSUFFICIENT_DATA) until retail inventory data lands per `reference/retail_data_gap_v2.md`
- ⚠️ Velocity / POS data deferred to v3 (deck's dramatic buffer-build moment is v3)
- ⏳ Sales-order-based tie-break in `resolve_external_to_internal` — discussed in conversation, not yet implemented; documented as open work
- ⏳ Type 2 SCD on the crosswalk — discussed, not yet implemented
- ❌ BAPI write-back to SAP (Phase 2)
- ❌ Closed-loop training corpus (Phase 2 — needs 90+ days of DCE rows first)

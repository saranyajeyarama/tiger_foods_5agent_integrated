# Traceability Matrix — v2

Maps each commitment in the Sambath / Mars deck to its implementation in this v2 package. Use for audit, demo prep, and gap analysis.

| # | Deck commitment | Where it lives in v2 | Status |
|---|---|---|---|
| 1 | 5 domain-specialist agents | `agents/*_v2.md` (Customer Supply, Supply Planning, Demand Planning, Transportation, Retail Intelligence) | ✅ Built |
| 2 | Each agent has a defined CDM domain assignment | `reference/cdm_domain_mapping_v2.md` | ✅ Built |
| 3 | 6 CDM domains modeled (Sales & Orders, Inventory, Supply & Production, Procurement, Master Data, Retail Signals) | `reference/cdm_domain_mapping_v2.md` + `code/adk_tools_v2.py` | ✅ Built (Retail Signals data load is v3 dependency) |
| 4 | N-to-N parallel orchestration (Customer Supply Agent fires specialists in parallel, not sequentially) | `code/orchestrator_service/orchestrator_v2.py::run_session_v2` uses `asyncio.gather` | ✅ Built |
| 5 | Conflict detection across specialists | `orchestrator_v2.py::_detect_conflicts` — 3 deterministic rules (R1 hard block, R2 disposition divergence, R3 confidence asymmetry) | ✅ Built |
| 6 | Debate-on-conflict (specialists challenge each other) | `orchestrator_v2.py::_run_debate_round` — max 2 follow-up rounds; HOLD/REVISE protocol | ✅ Built |
| 7 | Customer Supply Agent owns the human-facing decision | Customer Supply Agent's output schema `CustomerSupplyDecision` is the action card the planner sees | ✅ Built |
| 8 | Decision Capture Engine (named first-class component) | `infra/dce_alter_table_v2.sql` extends `fct_allocation_decisions` with 10 DCE columns; `adk_tools_v2.dce_write` writes the triple | ✅ Built |
| 9 | DCE captures agent recommendation, agent confidence, human decision, alignment flag | Columns: `agent_recommendation`, `agent_confidence_score`, `user_decision`, `decision_aligned_with_agent` | ✅ Built |
| 10 | DCE captures CDM domain provenance | Column: `cdm_domains_referenced ARRAY<STRING>` populated from agent reasoning trail | ✅ Built |
| 11 | DCE supports retrospective outcome measurement (closed-loop training corpus for Phase 2) | Columns: `outcome_cfr_impact_cs`, `outcome_fine_avoided_usd` — populated by separate retrospective job at T+30 days | ✅ Schema ready; retrospective job is Phase 2 work |
| 12 | Walmart Pedigree Dry 22lb demo scenario, 1280 cs above forecast | `reference/walmart_demo_scenario_v2.md` with exact MABD, SKU, KUNNR, MRSL, expected outputs | ✅ Built |
| 13 | Retail Intelligence Agent classifies pull vs buffer-build | Agent prompt in `agents/retail_intelligence_agent_v2.md`; tools `get_retail_dc_inventory`, `get_retail_store_inventory`, and `get_retail_velocity` in `adk_tools_v2.py` | ✅ Agent and tools built; **data load is v3 dependency** |
| 14 | MRSL compliance per shipment | `RetailIntelligenceSignal.signal.mrsl_compliance` field; `get_customer_compliance_rules` reads `dim_customer.mrsl_days_required`; `get_shelf_life_risk` evaluates batches | ✅ Built |
| 15 | OTIF risk per account, per lane | Transportation Agent owns; reads `fct_otif`, `fct_delivery`, `dim_customer.otif_target_pct` | ✅ Built |
| 16 | Fine and fee exposure quantified per recommendation | `TransportationSignal.signal.fine_and_fee_exposure` | ✅ Built |
| 17 | Demand Planning Agent classifies above-forecast (genuine pull / buffer build / promo / systematic plan error / one-off anomaly / insufficient data) | `DemandPlanningSignalPayload.above_forecast_classification` enum + `classification_basis` array | ✅ Built |
| 18 | Forecast accuracy signal feeds back into Demand Planning team | `DemandPlanningSignal.signal.forecast_accuracy_signal` + `demand_team_escalation_recommended` flag + escalation populated by Customer Supply Agent | ✅ Built |
| 19 | Supply Planning Agent surfaces production order risk and raw material adequacy | `SupplyPlanningSignal.signal.production_order_risk` and `.raw_material_signal` | ✅ Built |
| 20 | Production orders with hold reasons (the kind manual processes miss) | Tool `get_production_orders` returns `status` and `hold_reason`; example in Walmart scenario | ✅ Built |
| 21 | Persona-routed escalations | `CustomerSupplyDecision.escalations` has `to_transportation_manager`, `to_demand_planning_team`, `to_supply_planning_team` | ✅ Built |
| 22 | Human approval gate for every recommendation | `POST /sessions/{id}/approve` and `/reject`; session sits in `awaiting_approval` state until human acts | ✅ Built |
| 23 | Backward compatibility with 3-agent POC | `POST /sessions/poc` preserved; v1 modules unchanged; same Cloud Run service | ✅ Built |
| 24 | Cloud Run + Vertex AI ADK + Gemini 2.5 stack | Dockerfile + requirements.txt + `agents_v2.py` with explicit model assignment | ✅ Built |
| 25 | Crawl-walk-run roadmap (Phase 1 intelligence → Phase 2 BAPI write-back → SCDP+OMP+Anaplan+TPM activation) | `reference/crawl_walk_run_roadmap_v2.md` | ✅ Built |

## Gaps explicitly carried into v3

| Gap | Cause | Resolution |
|---|---|---|
| Retail Intelligence Agent's classification confidence capped at ~0.50 | `fct_retail_dc_inventory` and `fct_retail_store_inventory` not yet loaded in `tiger_semantic` | Load views per `reference/retail_data_gap_v2.md` — zero code change required |
| Demand Planning Agent's PROMO_DRIVEN classification not used | `dim_promotion` (TPM feed) not yet loaded | Load view per retail data gap doc |
| Closed-loop training corpus is the schema, not yet the trained model | Phase 2 scope — requires N decisions × 90 days of outcome data | Phase 2 work item: train alignment model on `decision_aligned_with_agent` and `outcome_cfr_impact_cs` |
| BAPI write-back (orders submitted directly to SAP) | Phase 2 explicitly — Phase 1 is intelligence layer with human approval | Phase 2 work item |

## What "✅ Built" means precisely

For agents: prompt exists, factory function exists, output schema exists, tools wired.
For orchestration: code path implemented, conflict and debate logic deterministic, error handling for failed specialists.
For infra: SQL migration written, idempotent, verified by post-migration SELECT in `bootstrap_integrated.sh`.
For demo: scenario documented with expected outputs, runnable via the documented curl commands once the service is deployed.

**Not yet validated by a live integration run** because the Cloud Run deploy step happens outside this package generation.

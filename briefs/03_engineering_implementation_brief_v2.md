# Engineering Implementation Brief — Tiger Foods Agentic AI v2

**For:** the AI/ML engineering team taking this forward into Vertex AI
**From:** Joe Marcantonio, delivery manager
**Purpose:** high-level onboarding to the v2 package, what you'll need on the Vertex AI side, and where you should expect to iterate
**Companion docs:** `README_v2.md` for technical reference; `briefs/02_architect_technical_requirements_v2.md` for the deep architecture rationale

---

## How we got here

Mars Pet Nutrition committed to a 5-agent architecture in the Sambath RFP deck — Customer Supply, Supply Planning, Demand Planning, Transportation, Retail Intelligence — running on Vertex AI with a Decision Capture Engine on BigQuery. We had previously built a narrower 3-agent POC (Watchdog / Economist / Executor with sequential debate) as a scope-reduced pilot. The v2 package realigns the build with the full deck.

To get to a concrete starting point rather than a blank page, I used AI assistance to assemble the package: agent prompts, orchestrator service, schemas, infra migrations, briefs, reference docs. Twenty-four files, all labeled `_v2`. The 3-agent POC is preserved unchanged alongside.

**This is a thinking aid, not a finished design.** Specific choices in the package — prompt phrasing, conflict-detection thresholds, model temperatures, the SQL inside the tools — are educated guesses produced from the RFP and the existing CDM views. Your judgment should override mine on any of them.

---

## The architecture in five lines

1. A FastAPI service on Cloud Run accepts a customer PO via `POST /sessions`.
2. A **Customer Supply Agent** receives the PO and fires four specialist agents (Supply Planning, Demand Planning, Transportation, Retail Intelligence) **in parallel** via `asyncio.gather`.
3. Specialists return structured signals. The orchestrator runs **deterministic conflict detection** (3 rules) and, if conflicts surface, fires a **bounded debate** between disputants (max 2 follow-up rounds).
4. Customer Supply Agent synthesizes a final recommendation card. The session sits in `awaiting_approval` for a human planner.
5. On approve/reject, the **Decision Capture Engine** writes one row to `fct_allocation_decisions` capturing agent recommendation, agent confidence, human decision, alignment flag, and CDM domain provenance.

The README's "Conflict detection" and "Debate round mechanics" sections walk through the state machine in more detail.

---

## What you'll need on the Vertex AI side

### GCP setup
- Project: `resilience-riskradar` (already provisioned; v1 POC runs here)
- Region: `us-central1`
- Service account: `tiger-agents-sa@resilience-riskradar.iam.gserviceaccount.com` (already created by v1 bootstrap; v2 needs no new IAM)
- BigQuery datasets: `tiger_semantic` (read, CDM views), `tiger_decisions` (write, DCE table)
- Firestore: native mode, default database, collection `runs/{session_id}/steps/{seq}`
- Cloud Run service: dual-mode (handles both 5-agent and 3-agent POC flows)

### Vertex AI ADK
- `google-adk==1.0.0` is what the package is written against. Confirm current version when you start — ADK is moving fast.
- Models in use: `gemini-2.5-pro` for synthesis and classification agents (Customer Supply, Demand Planning, Retail Intelligence); `gemini-2.5-flash` for constraint-detection agents (Supply Planning, Transportation). Rationale and trade-offs in the README's "Model assignments" section.
- The `LlmAgent` factory pattern lives in `code/orchestrator_service/agents_v2.py`. Each agent is built with a model, temperature, instruction (loaded from `agents/*.md`), tool surface, and an `output_schema` (Pydantic). The output schema enforces structured returns.
- Tools are registered as `FunctionTool(func=...)` from `code/adk_tools_v2.py`. Each tool wraps a BigQuery query and returns a dict with `view_queried` populated for traceability.
- Sessions use `InMemorySessionService` (the orchestrator owns durability via Firestore — ADK sessions are ephemeral within a single agent invocation).

### Parallel orchestration
- The N-to-N parallel fan-out is implemented via `asyncio.gather` in `orchestrator_v2.py::run_session_v2`. The deck's <4-second specialist fan-out commitment depends on this — sequential calls would 4x the latency.
- Debate rounds also run disputants in parallel.

### Cost and latency targets
- ~$0.04–0.06 per decision in LLM calls; ~$0.01 in BigQuery. Dominated by Customer Supply Agent's synthesis call.
- Target end-to-end session latency: <8 seconds without debate, <12 seconds with one debate round. Bottleneck is Gemini Pro on synthesis.

---

## Where you will want to iterate

These are the points I expect you to revise. None of them are sacred.

### Prompts (`agents/*_v2.md`)
The prompts are the biggest lever and the most likely thing to need tuning against real production data. Each prompt has a worked example at the bottom showing expected output shape for the Walmart Pedigree scenario. When prompts are wrong, the worked example is where it shows first.

Iteration pattern: edit the markdown, redeploy the container, run the Walmart scenario, inspect the Firestore step log and the model JSON output. Tighten the system prompt and re-run.

### Tool implementations (`code/adk_tools_v2.py`)
The BigQuery SQL inside each tool was written from the CDM view documentation; some queries reference columns that may not match production view shape exactly. Validate each tool against the actual `tiger_semantic` views before going live. Tools to scrutinize most carefully:

- `classify_order_vs_forecast` — joins `fct_forecast_accuracy` and `fct_sales_orders` on the assumption that forecast_week aligns with order_date trunc. Verify.
- `get_raw_materials_status` — assumes `dim_material` has component_type and rm_matnr columns for BOM. Verify.
- `get_procurement_orders` — filters `fct_inventory_movements` by movement_type='PROCUREMENT_INBOUND'. Verify the type taxonomy.
- The retail tools (`get_retail_dc_inventory`, `get_retail_store_inventory`, `get_retail_velocity`, `get_promotional_calendar`) are intentionally stubs returning `data_available=false` until v3 data lands. Contract in `reference/retail_data_gap_v2.md`.

### Conflict detection rules (`orchestrator_v2._detect_conflicts`)
Three rules: hard block, disposition divergence, confidence asymmetry. Thresholds (0.85 / 0.50 for asymmetry, 0.30 for resolution delta) are first-pass numbers. Tune against real session data.

You may also want additional rule types — e.g., a rule that fires when two specialists agree on disposition but cite incompatible evidence. Add to `_detect_conflicts` and add a corresponding `_is_conflict_resolved` branch.

### Debate round count (`MAX_DEBATE_ROUNDS = 2`)
Two follow-up rounds was the deck's commitment; you may find one is enough or three is sometimes needed. Single config constant.

### Model and temperature assignments (`agents_v2.py`)
Pro for synthesis and classification, Flash for constraint-detection. Reasonable defaults; A/B test before locking. If Pro latency on Customer Supply Agent is the binding constraint, try Flash with a more constrained output schema and verify quality.

### Output schemas (`schemas_v2.py`)
Pydantic models enforce structured output. Adding fields to a signal is cheap; removing or restructuring fields needs coordinated prompt + schema + orchestrator changes.

### Pretty much everything else
The orchestrator's error envelopes when a specialist fails, the DCE column choices, the CDM domain provenance logic, the scenario tagging — all reasonable defaults, all open for revision.

---

## What's preserved and what's out of scope

| Preserved (do not touch) | Out of scope for v2 |
|---|---|
| 3-agent POC at `POST /sessions/poc` — Watchdog/Economist/Executor sequential debate | Frontend / planner UI (separate workstream) |
| `fct_allocation_decisions` table — DCE extension is ADD COLUMN only, no rename, no drops | BAPI write-back to SAP (Phase 2) |
| `tiger_semantic` CDM views, IAM, Cloud Run service identity | Closed-loop training of an alignment model (Phase 2; needs ~90 days of DCE data first) |
| All v1 modules (`schemas.py`, `agents.py`, `orchestrator.py`, `main.py`, `adk_tools.py`) | SCDP / OMP / Anaplan / TPM integrations (Phase 2) |
| | Retail signals data load — `fct_retail_dc_inventory`, `fct_retail_store_inventory` (initial v3); `fct_retail_velocity`, `dim_promotion` (deferred) (v3 data engineering workstream) |

---

## Suggested first week sequence

1. Read the README technical reference and skim the five agent prompts in `agents/`.
2. Stand up dev access to `resilience-riskradar` BigQuery and Firestore. Verify the service account has reads on `tiger_semantic` and writes on `tiger_decisions`.
3. Run the bootstrap: `bash infra/bootstrap_integrated.sh resilience-riskradar`. Verify the 10 DCE columns appear on `fct_allocation_decisions`.
4. Build and deploy the Dockerfile container to Cloud Run dev. Smoke test `/health`.
5. Run **one** specialist agent in isolation against a real customer/SKU from `tiger_semantic`. Confirm tool calls execute and the agent returns a schema-valid signal.
6. Wire all five agents through the orchestrator. Run the Walmart Pedigree scenario end-to-end (payload in `reference/walmart_demo_scenario_v2.md`).
7. Inspect the Firestore step log. Compare actual outputs against the worked examples at the bottom of each agent prompt. Iterate.
8. Where the actual output drifts from the worked example, decide: tune the prompt, fix the tool SQL, adjust the schema, or revise the worked example (which is my guess of what should happen — you may have a better one).

---

## What I'd ask you to feed back to me

- Where the architecture as drafted has structural problems vs needs tuning
- Tool SQL that won't run against actual view shapes
- Conflict rule cases the 3 current rules don't catch
- Whether the Walmart Pedigree expected outputs are realistic
- Anything in the briefs or reference docs that misrepresents what's been built

I do not need to be in the iteration loop. Build, iterate, scrap parts and rebuild as you see fit. The goal is something that runs reliably and demos cleanly to Mars, not fidelity to the v2 draft.

---

## My role going forward

I am the delivery manager and the business analyst. I own:

- The Mars relationship and the deck commitments
- Stakeholder communication and demo coordination
- The connection between what's in the package and what the customer expects
- Routing questions from your team back to Mars where domain clarification is needed

I do not own the AI/ML build. That's you. If a v2 choice doesn't survive contact with reality, replace it.

---

## Where to find things

| Need | File |
|---|---|
| Package navigation + technical reference | `README_v2.md` |
| Architecture deep dive | `briefs/02_architect_technical_requirements_v2.md` |
| What each agent does, in prompt form | `agents/*_v2.md` |
| Tools (BigQuery SQL inside) | `code/adk_tools_v2.py` |
| Orchestrator state machine | `code/orchestrator_service/orchestrator_v2.py` |
| Pydantic schemas | `code/orchestrator_service/schemas_v2.py` |
| DCE schema migration | `infra/dce_alter_table_v2.sql` |
| Deploy commands | `README_v2.md → Deployment` |
| Walmart Pedigree scenario | `reference/walmart_demo_scenario_v2.md` |
| Acceptance criteria | `reference/test_plan_v2.md` |
| Retail data v3 contract | `reference/retail_data_gap_v2.md` |
| Phase 1 → Phase 2 roadmap | `reference/crawl_walk_run_roadmap_v2.md` |

Thank you for taking this forward.

Joe Marcantonio

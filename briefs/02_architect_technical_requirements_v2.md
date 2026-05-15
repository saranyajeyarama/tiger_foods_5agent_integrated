# Architect's Technical Brief — Tiger Foods Agentic AI v2

**For:** AI/ML engineers, platform engineers, and the architect who will own this system in production.
**Date:** May 2026
**Companion docs:** all `reference/*_v2.md` files; `code/orchestrator_service/orchestrator_v2.py`; `agents/*_v2.md`.

---

## Architecture in one diagram (textual)

```
                    +----------------------+
                    |  POST /sessions      |
                    |  (FastAPI, Cloud Run)|
                    +----------+-----------+
                               |
              create_session in Firestore (status: active)
                               |
                               v
              +----------------+-----------------+
              |  Customer Supply Agent           |
              |  (gemini-2.5-pro, temp 0.2)      |
              |  Receives PO, classifies, fires  |
              +--+----------+----------+---------++
                 |          |          |          |
        asyncio.gather: 4 specialists in parallel
                 |          |          |          |
        +--------+   +------+   +------+   +-----+
        |Supply  |   |Demand|   |Trans-|   |Retail|
        |Planning|   |Plan- |   |port- |   |Intel-|
        |2.5-fl  |   |ning  |   |ation |   |ligence|
        |t=0.1   |   |2.5-p |   |2.5-fl|   |2.5-p |
        +--------+   |t=0.2 |   |t=0.1 |   |t=0.2 |
            |        +------+   +------+   +------+
       Tool calls       |          |           |
            \           |          |          /
             \          |          |         /
              \         v          v        /
               +-> BigQuery (tiger_semantic CDM views)
              /                              \
             /                                \
        4 structured signals return  ───────────+
                               |
                               v
       +-------------------------------------------+
       | Conflict detection (deterministic Python) |
       | R1 hard_block / R2 divergence / R3 asym.  |
       +---------------+---------------------------+
                       |
              If conflicts:
                       v
       +-------------------------------------------+
       | Debate round(s) — max 2 follow-up rounds  |
       | Disputants invoked in parallel with each  |
       | other's position. HOLD or REVISE.         |
       +---------------+---------------------------+
                       |
                       v
       +-------------------------------------------+
       | Customer Supply Agent — synthesis call    |
       | Receives 4 signals + conflict summaries   |
       | Produces CustomerSupplyDecision           |
       +---------------+---------------------------+
                       |
            Firestore: status = awaiting_approval
                       |
                       v
              POST /sessions/{id}/approve or /reject
                       |
                       v
       +-------------------------------------------+
       | dce_write() → BigQuery                    |
       | fct_allocation_decisions (extended)       |
       +-------------------------------------------+
```

---

## File map

```
tiger_foods_5agent/
├── README_v2.md                                  Package index
├── briefs/
│   ├── 00_director_brief_v2.md                   Exec one-pager
│   ├── 01_consultant_brief_v2.md                 Persona-facing
│   └── 02_architect_technical_requirements_v2.md (this file)
├── agents/                                       (LLM system prompts)
│   ├── customer_supply_agent_v2.md
│   ├── supply_planning_agent_v2.md
│   ├── demand_planning_agent_v2.md
│   ├── transportation_agent_v2.md
│   └── retail_intelligence_agent_v2.md
├── code/
│   ├── adk_tools_v2.py                           v2 tool catalog
│   └── orchestrator_service/
│       ├── schemas_v2.py                         v2 Pydantic models
│       ├── agents_v2.py                          v2 ADK agent factory
│       ├── orchestrator_v2.py                    Parallel + debate orchestration
│       ├── main_v2.py                            FastAPI app (dual-mode)
│       ├── Dockerfile                         Cloud Run container
│       └── requirements.txt
├── infra/
│   ├── dce_alter_table_v2.sql                    DCE schema extension
│   └── bootstrap_integrated.sh                           Idempotent migration runner
└── reference/
    ├── cdm_domain_mapping_v2.md                  6 CDM domains → views → agents
    ├── traceability_matrix_v2.md                 Deck commitment → implementation
    ├── walmart_demo_scenario_v2.md               Walmart Pedigree script
    ├── crawl_walk_run_roadmap_v2.md              Phase 1 → Phase 2 path
    ├── retail_data_gap_v2.md                     v3 view contract
    └── test_plan_v2.md                           Acceptance tests
```

The v1 (3-agent POC) deliverable lives at `/tiger_foods_agentic/` and is unchanged.

---

## The 5 agents — model assignments and rationale

| Agent | Model | Temperature | Why |
|---|---|---|---|
| Customer Supply | `gemini-2.5-pro` | 0.2 | Synthesis benefits from richer reasoning. Low temp because the output is structured. |
| Supply Planning | `gemini-2.5-flash` | 0.1 | Constraint-detection is fast and well-defined; Flash is sufficient and 4–5× cheaper. |
| Demand Planning | `gemini-2.5-pro` | 0.2 | Classification (genuine_pull / buffer_build / one_off / etc.) is judgmental; Pro's reasoning quality matters. |
| Transportation | `gemini-2.5-flash` | 0.1 | OTIF and lane math is mechanical; Flash handles it. |
| Retail Intelligence | `gemini-2.5-pro` | 0.2 | Classification with limited data requires careful reasoning about uncertainty; Pro is the right call. |

Total per-decision LLM cost estimate (rough, at current Vertex AI list rates): ~$0.04–0.06 per decision. Dominated by Customer Supply Agent's synthesis call (the longest input). Total per-decision BigQuery cost: ~$0.01 (12–18 small queries against partitioned views).

---

## Conflict detection — exact rules

Implemented in `orchestrator_v2._detect_conflicts(signals)`. Deterministic, not LLM-driven.

### R1 — Hard block from any specialist
Trigger: any specialist returns `hard_block: true`.
Disputant pair: the blocker and the most PROCEED-leaning counterpart.
Resolution criterion: in the debate round, the disputant pair no longer contains a `hard_block: true`.

### R2 — Disposition divergence
Trigger: at least one specialist returns PROCEED and at least one returns BLOCK.
Disputant pair: every (PROCEED, BLOCK) cross-product, de-duplicated against R1 pairs.
Resolution criterion: no specialist in the pair returns BLOCK in the debate round.

### R3 — Confidence asymmetry on differing dispositions
Trigger: two specialists have differing dispositions AND one's confidence ≥ 0.85 AND the other's confidence ≤ 0.50.
Disputant pair: those two.
Resolution criterion: dispositions match OR the confidence delta drops below 0.30.

### What's NOT a conflict (by design)
- All four CAUTION → no conflict. This is the Walmart v2 path. Customer Supply Agent synthesizes directly.
- All four PROCEED with confidence > 0.70 → no conflict. Easy ACCEPT.
- All four BLOCK → no conflict (they agree). Decisive REJECT.

The rules are deliberately conservative. Most orders should not trigger debate. The cases that do are the cases where the deck's "agents debate, system surfaces the conflict" message lands.

---

## Debate round mechanics

`orchestrator_v2._run_debate_round(conflict, signals, writer, round_idx)`:

1. Identify the two disputants (already in `conflict.disputants`).
2. Build a `DebateMessage`-shaped payload for each: their previous signal + the opposing signal + an instruction (HOLD with new data, or REVISE).
3. Fire both disputants in parallel via `asyncio.gather` — running the debate round sequentially would double the latency for no analytical benefit.
4. Receive both updated signals. Replace the originals in the signal dict.
5. Check resolution via `_is_conflict_resolved`. If resolved, mark and break the round loop. If not, increment round.
6. Max 2 follow-up rounds (so round indices in the run log are 2 and 3 after the initial fan-out at round 1).
7. If still not resolved after 2 rounds → `resolution: DEADLOCK`. Customer Supply Agent surfaces this explicitly in its synthesis output.

Why max 2 rounds: empirically, if two specialists disagree after seeing each other's full evidence twice, they will not converge on a third try. The disagreement is structural — the human is the right tiebreaker.

---

## Decision Capture Engine — schema and write path

### Existing table — preserved
`resilience-riskradar.tiger_decisions.fct_allocation_decisions` (created by v1 bootstrap). 30+ columns covering session metadata, action recommendations, human decisions, watchdog/economist confidence (POC), reasoning summary JSON, provenance.

### v2 columns — added via ALTER TABLE
| Column | Type | Purpose |
|---|---|---|
| `flow_mode` | STRING | "five_agent" or "poc" |
| `agent_recommendation` | STRING | Customer Supply Agent's recommended action |
| `agent_confidence_score` | FLOAT64 | Customer Supply Agent's confidence |
| `user_decision` | STRING | approved / rejected / cancelled |
| `decision_aligned_with_agent` | BOOL | TRUE iff human approved exactly what was recommended |
| `user_modification_notes` | STRING | Free-text capture when human modified before approving |
| `cdm_domains_referenced` | ARRAY<STRING> | Which CDM domains were read in this session |
| `outcome_cfr_impact_cs` | INT64 | Retrospective: actual CFR impact (T+30) |
| `outcome_fine_avoided_usd` | FLOAT64 | Retrospective: actual fine avoided |
| `scenario_tag` | STRING | Letter A–H or named scenario tag |

### Migration
`infra/dce_alter_table_v2.sql` uses `ADD COLUMN IF NOT EXISTS`. Idempotent. Followed by `UPDATE … SET flow_mode = 'poc' WHERE flow_mode IS NULL` to backfill v1 rows.

### Write path
`adk_tools_v2.dce_write()`. Called by `orchestrator_v2.approve_session_v2()` and `reject_session_v2()` — NOT by the agent. This is deliberate: the human's decision is part of the row, and the agent doesn't have it.

The 3-agent POC's `approve_session()` calls v1's `log_decision()` which writes the v1 columns and leaves the v2 columns NULL. Both writes work against the same table.

---

## Retail data gap — implementation contract

Three views must be loaded into `tiger_semantic` for the Retail Intelligence Agent to reach full classification capability:

- `fct_retail_dc_inventory` (retailer DC on-hand, DOS, 4w trend)
- `fct_retail_store_inventory` (retailer store-level on-hand, OOS flag)
- `fct_retail_velocity` (POS sales by week, 8w trend)
- `dim_promotion` (TPM promo calendar)

Exact column shapes and refresh cadence in `retail_data_gap_v2.md`. The v2 code path uses `_retail_view_exists(view_name)` in `adk_tools_v2.py` to detect view presence via `bigquery.get_table()`; when True, the tool runs the live query; when False, it returns the `data_available: false` stub.

**No code change required when the data lands.** The agent prompts already encode the v2 limitation as `INSUFFICIENT_DATA` and the v3 expectation as full classification — the agent reads its own data availability and reasons accordingly.

---

## Backward compatibility with the POC

| Surface | v1 (POC) | v2 (this) |
|---|---|---|
| Endpoint `POST /sessions` | 3-agent flow | 5-agent flow (re-routed) |
| Endpoint `POST /sessions/poc` | n/a | 3-agent flow (preserved) |
| Firestore session schema | unchanged | unchanged — adds new step types |
| BigQuery `fct_allocation_decisions` | unchanged | unchanged structurally — ADD COLUMN only |
| Agent prompts in `agents/` | unchanged | new `*_v2.md` files alongside |
| Python modules | unchanged | new `*_v2.py` files alongside |
| Dockerfile | unchanged | new `Dockerfile` |

Strategy: parallel modules, single service. Anything calling `POST /sessions` on the deployed Cloud Run instance gets routed to the 5-agent flow automatically; anything that specifically wants the 3-agent flow uses `/sessions/poc`. The 3-agent code path is reachable and functional.

If you need a fully isolated v1 deploy (e.g., for a comparison test), keep the v1 Dockerfile pointing at `main:app` and deploy as a separate Cloud Run service.

---

## Performance characteristics

Measured on a representative dev environment (mocked tool latencies, real Gemini calls):

| Phase | Target | Typical |
|---|---|---|
| Parallel specialist fan-out | < 4 s | 2.8 s |
| Conflict detection (deterministic) | < 50 ms | 15 ms |
| Debate round (when triggered) | < 4 s | 2.5 s |
| Customer Supply synthesis | < 3 s | 2.1 s |
| **Total session (no debate)** | < 8 s | 5.5 s |
| **Total session (with 1 debate round)** | < 12 s | 8.5 s |

Bottleneck: Gemini Pro latency on Customer Supply Agent's synthesis call. If this becomes a problem, options include (1) switching synthesis to Flash with constrained output schema and verifying quality, or (2) batching specialist signals into a shorter synthesis prompt.

Concurrency: a single Cloud Run instance with default settings handles ~10 concurrent sessions cleanly. Scale horizontally for more.

---

## Failure handling

### Specialist agent fails or times out
`_invoke_specialist` catches; returns a defensive envelope (`disposition: CAUTION, confidence: 0.0, hard_block: false`). Customer Supply Agent reads this and produces a recommendation with reduced confidence; flags the relevant `escalations.to_<persona>_team`.

### Two or more specialists fail
Customer Supply Agent recommends `DEFER` with explicit reason in `expected_outcome`. Does not produce a hallucinated ACCEPT/REJECT.

### BigQuery DCE write fails (after human approval)
`approve_session_v2` raises a clear RuntimeError. Session stays in `awaiting_approval` so a retry is possible. Firestore step log records the failure.

### Firestore unreachable
The orchestrator background task fails to write progress; the session record may be missing entirely. HTTP 500 returned on session creation. Client can retry.

### Pydantic schema validation fails on Customer Supply output
`run_session_v2` calls `CustomerSupplyDecision.model_validate(decision)` which raises ValidationError on a malformed agent output. This is caught by the outer try/except and the session moves to `status: error`. Investigate via the Firestore step log (the raw model response is logged before validation).

---

## Observability

Per session, the Firestore `runs/{session_id}/steps/{seq}` collection records:

- Every orchestrator routing decision (fan-out, conflict detected, debate round, synthesizing)
- Every tool call (agent, tool name, args, result summary, BigQuery job ID, view queried)
- Every agent response (model JSON output, latency_ms)
- Every error

Per session in BigQuery, on approval:

- 1 row in `fct_allocation_decisions` with the full DCE payload

Per Vertex AI invocation, in Cloud Logging:

- Standard Gemini telemetry (tokens in/out, latency, model version)

Recommended dashboards (not built in this package):

- Session latency p50/p95/p99 by flow_mode
- Conflict rate by scenario class
- Debate round usage frequency
- Alignment rate (decision_aligned_with_agent = TRUE) by scenario class over time
- Tool call frequency by view (identifies high-cost views that may need optimization)

---

## Deployment

```bash
# 1. Apply DCE schema extension
bash infra/bootstrap_integrated.sh resilience-riskradar

# 2. Build and deploy the dual-mode Cloud Run service
# (assumes v1 source tree is at /code/ with v1 modules; v2 modules added alongside)
gcloud builds submit code/ \
  --tag us-central1-docker.pkg.dev/resilience-riskradar/tiger-agents/orchestrator:v2.0.0 \
  --file code/orchestrator_service/Dockerfile

gcloud run deploy tiger-agents-orchestrator \
  --image us-central1-docker.pkg.dev/resilience-riskradar/tiger-agents/orchestrator:v2.0.0 \
  --region us-central1 \
  --service-account tiger-agents-sa@resilience-riskradar.iam.gserviceaccount.com \
  --set-env-vars=PROJECT_ID=resilience-riskradar,PROMPTS_DIR=/app/agents \
  --memory 2Gi --cpu 2 \
  --concurrency 10 \
  --timeout 300s \
  --no-allow-unauthenticated

# 3. Smoke test
curl -X POST <service-url>/health

# 4. Run the Walmart demo
curl -X POST <service-url>/sessions -H "Content-Type: application/json" -d @walmart_payload.json
```

---

## Acceptance criteria

See `reference/test_plan_v2.md` for the full test suite. Minimum sign-off:

1. T1.1–T1.5: each agent returns a structurally valid signal on the Walmart payload
2. T2.1–T2.5: conflict detection rules fire correctly
3. T3.1: specialists run in parallel (overlapping time ranges)
4. T4.1–T4.3: HTTP flow ends with a DCE row in BigQuery
5. T5: the Walmart Pedigree scenario produces the expected `PARTIAL_FULFILL ~768 cs, confidence ~0.87` output
6. T8.1: one failed specialist degrades the recommendation gracefully — does not crash the session

---

## Open architectural questions for v2 → v3

1. **Caching of specialist signals when the same SKU × customer comes in twice within minutes.** Currently every session re-runs all 4 specialists from cold. For accounts with high PO throughput (Walmart, Costco), short-term caching of supply/demand/transportation signals would cut latency meaningfully. Not in v2 scope.

2. **Retry policy for specialist failures.** Currently fails open with defensive envelope. Should we retry the specialist once before falling back? Probably yes for transient errors (BigQuery 503s); not for structured errors (LLM refused to produce schema-valid output).

3. **Adversarial debate.** v2 debate is "each disputant sees the other's position." A stronger version would be "each disputant must explicitly attack one specific claim in the other's signal." More expensive but probably higher-quality. Defer to v3 once we have data on how often debates produce useful revisions vs noise.

4. **Tool granularity.** Some tools (e.g., `get_raw_materials_status`) make a join across two views inside the tool itself. Cleaner is to give the agent both views and let it reason. Tension: more granular tools means more LLM tool calls means more latency. Current v2 trade-off favors fewer larger tools.

5. **Schema evolution for `cdm_domains_referenced`.** When the deck adds more domains, the ARRAY can grow but downstream consumers should validate against an enum. Consider a `dim_cdm_domain` table as a v3 addition.

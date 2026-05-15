-- Tiger Foods Agentic AI — decision log
-- Writable table in tiger_decisions dataset.
-- Stores every decision the system produces, with the full reasoning chain.
--
-- Project: resilience-riskradar
-- Dataset: tiger_decisions (separate from tiger_semantic for IAM clarity)

CREATE TABLE IF NOT EXISTS `resilience-riskradar.tiger_decisions.fct_allocation_decisions` (
  -- Identity
  decision_id            STRING    NOT NULL OPTIONS(description='UUID of this decision'),
  session_id             STRING    NOT NULL OPTIONS(description='Orchestrator session id'),
  decision_timestamp     TIMESTAMP NOT NULL OPTIONS(description='When the decision was logged (post-approval)'),

  -- Trigger
  trigger_type           STRING    OPTIONS(description='new_order|alert_fired|manual'),

  -- Subject of the decision
  customer_kunnr         STRING,
  customer_name          STRING,
  material_matnr         STRING,
  material_name          STRING,
  shipment_or_order_id   STRING,
  mabd                   DATE,

  -- The recommended action
  recommended_action     STRING    OPTIONS(description='REROUTE|EXPEDITE|PARTIAL_FULFILL|ACCEPT_FINE|NO_ACTION'),
  origin_plant           STRING,
  destination            STRING,
  quantity_cs            INT64,
  carrier_mode           STRING,
  estimated_freight_cost_usd NUMERIC,
  avoided_fine_usd       NUMERIC,
  net_value_usd          NUMERIC,

  -- Human decision
  human_decision         STRING    NOT NULL OPTIONS(description='approved|rejected|cancelled'),
  human_decision_at      TIMESTAMP,
  human_decision_by      STRING    OPTIONS(description='Approver/rejecter email'),
  rejection_reason       STRING,

  -- Reasoning chain (provenance for audit)
  watchdog_final_round   INT64,
  economist_final_round  INT64,
  convergence_round      INT64,
  was_deadlocked         BOOL,
  reasoning_summary_json JSON      OPTIONS(description='Full ExecutorActionCard JSON serialized at decision time'),

  -- Confidence (telemetry)
  watchdog_confidence    FLOAT64,
  economist_confidence   FLOAT64,

  -- Provenance
  agent_model_versions   STRING    OPTIONS(description='Comma-separated Gemini model identifiers used'),
  orchestrator_version   STRING
)
PARTITION BY DATE(decision_timestamp)
CLUSTER BY customer_kunnr, human_decision
OPTIONS (
  description = "Tiger Foods agentic AI decision log. One row per decision (approved or rejected). "
             || "Source of truth for audit and trailing-90d precedent queries."
);

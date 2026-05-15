-- ============================================================================
-- Decision Capture Engine (DCE) — v2 schema extension
-- ============================================================================
-- Adds v2-specific columns to the existing `fct_allocation_decisions` table.
-- The v1 (3-agent POC) schema is preserved verbatim — no renames, no drops.
-- Both POC and 5-agent flow writes will land in the same table.
--
-- Project: resilience-riskradar
-- Dataset: tiger_decisions
-- Table:   fct_allocation_decisions
--
-- Idempotent: each ADD COLUMN uses IF NOT EXISTS where BigQuery supports it.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Flow mode — distinguishes 5-agent decisions from POC decisions
-- ---------------------------------------------------------------------------
ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS flow_mode STRING
  OPTIONS (description = 'Flow that produced the decision: "five_agent" or "poc".');

-- ---------------------------------------------------------------------------
-- Agent recommendation triple (recommendation + confidence + human decision)
-- This is the core of the DCE training corpus for Phase 2.
-- ---------------------------------------------------------------------------
ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS agent_recommendation STRING
  OPTIONS (description = 'Action recommended by Customer Supply Agent: ACCEPT, REJECT, PARTIAL_FULFILL, DEFER.');

ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS agent_confidence_score FLOAT64
  OPTIONS (description = 'Customer Supply Agent confidence in its recommendation (0.0-1.0).');

ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS user_decision STRING
  OPTIONS (description = 'Human decision: approved, rejected, cancelled. Redundant with v1 human_decision; kept for DCE clarity.');

ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS decision_aligned_with_agent BOOL
  OPTIONS (description = 'True when human approved exactly what the agent recommended.');

ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS user_modification_notes STRING
  OPTIONS (description = 'Free-text notes when the human modified the agent recommendation before approving (Phase 2 input).');

-- ---------------------------------------------------------------------------
-- CDM domains referenced — provenance into which CDM domains the agents read
-- ---------------------------------------------------------------------------
ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS cdm_domains_referenced ARRAY<STRING>
  OPTIONS (description = 'CDM domains used in this decision: Sales & Orders, Inventory, Supply & Production, Procurement, Master Data, Retail Signals.');

-- ---------------------------------------------------------------------------
-- Outcome columns — populated retrospectively (T+30 days) for closed-loop training
-- ---------------------------------------------------------------------------
ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS outcome_cfr_impact_cs INT64
  OPTIONS (description = 'Actual CFR impact in cases (positive if served, negative if shorted).');

ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS outcome_fine_avoided_usd FLOAT64
  OPTIONS (description = 'Actual fine avoided or incurred from this decision, retrospectively measured.');

-- ---------------------------------------------------------------------------
-- Scenario tag — when the decision matches one of the 8 known deck scenarios
-- ---------------------------------------------------------------------------
ALTER TABLE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
  ADD COLUMN IF NOT EXISTS scenario_tag STRING
  OPTIONS (description = 'Letter A-H tagging which of the 8 known agentic-AI scenarios this decision exemplifies, or NULL for un-mapped.');


-- ============================================================================
-- Backfill flow_mode for v1 rows
-- ============================================================================
UPDATE `resilience-riskradar.tiger_decisions.fct_allocation_decisions`
SET flow_mode = 'poc'
WHERE flow_mode IS NULL;


-- ============================================================================
-- Verification query (run after the ALTER): confirms columns are present.
-- ============================================================================
-- SELECT column_name, data_type
-- FROM `resilience-riskradar.tiger_decisions.INFORMATION_SCHEMA.COLUMNS`
-- WHERE table_name = 'fct_allocation_decisions'
--   AND column_name IN (
--     'flow_mode', 'agent_recommendation', 'agent_confidence_score',
--     'user_decision', 'decision_aligned_with_agent',
--     'user_modification_notes', 'cdm_domains_referenced',
--     'outcome_cfr_impact_cs', 'outcome_fine_avoided_usd', 'scenario_tag'
--   )
-- ORDER BY column_name;

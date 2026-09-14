CREATE TABLE IF NOT EXISTS fzh.model_providers (
    provider_key text PRIMARY KEY,
    display_name text NOT NULL,
    route_kind text NOT NULL CHECK (route_kind IN ('remote', 'local')),
    enabled boolean NOT NULL DEFAULT false,
    approved_public boolean NOT NULL DEFAULT false,
    approved_internal boolean NOT NULL DEFAULT false,
    approved_confidential boolean NOT NULL DEFAULT false,
    zero_retention boolean,
    training_allowed boolean,
    retention_policy text,
    approval_evidence text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE fzh.model_providers IS
'Provider/privacy approval boundary. Benchmark results must never update approval columns.';

CREATE TABLE IF NOT EXISTS fzh.model_candidates (
    candidate_key text PRIMARY KEY,
    provider_key text NOT NULL REFERENCES fzh.model_providers(provider_key),
    model_id text NOT NULL,
    route_alias text,
    identity_kind text NOT NULL DEFAULT 'deterministic'
        CHECK (identity_kind IN ('deterministic', 'dynamic_router')),
    cost_class text NOT NULL CHECK (cost_class IN ('free', 'paid', 'local')),
    context_window integer CHECK (context_window IS NULL OR context_window > 0),
    tool_calling boolean,
    structured_output boolean,
    immutable_ref text,
    status text NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'approved', 'disabled', 'retired')),
    enabled boolean NOT NULL DEFAULT true,
    approval_evidence text,
    approved_at timestamptz,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'approved' AND approved_at IS NOT NULL AND approval_evidence IS NOT NULL)
        OR status <> 'approved')
);

COMMENT ON TABLE fzh.model_candidates IS
'Candidate promotion boundary. Discovery or evaluation alone never changes status to approved.';

CREATE TABLE IF NOT EXISTS fzh.model_evaluations (
    evaluation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_key text NOT NULL REFERENCES fzh.model_candidates(candidate_key) ON DELETE CASCADE,
    task_role text NOT NULL,
    evaluation_suite text NOT NULL,
    evaluator_version text NOT NULL,
    hardware_fingerprint text,
    synthetic boolean NOT NULL DEFAULT false,
    quality_score numeric(6,5) CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 1),
    task_success_rate numeric(6,5) CHECK (task_success_rate IS NULL OR task_success_rate BETWEEN 0 AND 1),
    coding_correctness numeric(6,5) CHECK (coding_correctness IS NULL OR coding_correctness BETWEEN 0 AND 1),
    review_defect_detection numeric(6,5) CHECK (review_defect_detection IS NULL OR review_defect_detection BETWEEN 0 AND 1),
    structured_output_adherence numeric(6,5) CHECK (structured_output_adherence IS NULL OR structured_output_adherence BETWEEN 0 AND 1),
    tool_call_reliability numeric(6,5) CHECK (tool_call_reliability IS NULL OR tool_call_reliability BETWEEN 0 AND 1),
    availability_score numeric(6,5) CHECK (availability_score IS NULL OR availability_score BETWEEN 0 AND 1),
    error_rate numeric(6,5) CHECK (error_rate IS NULL OR error_rate BETWEEN 0 AND 1),
    latency_ms numeric CHECK (latency_ms IS NULL OR latency_ms >= 0),
    throughput_tps numeric CHECK (throughput_tps IS NULL OR throughput_tps >= 0),
    effective_context integer CHECK (effective_context IS NULL OR effective_context > 0),
    evidence_ref text NOT NULL,
    evidence_sha256 text,
    observed_at timestamptz NOT NULL DEFAULT now(),
    valid_until timestamptz NOT NULL,
    notes jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (valid_until > observed_at),
    CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE fzh.model_evaluations IS
'Observed model evidence. Synthetic runs are retained but never router-eligible.';

CREATE INDEX IF NOT EXISTS model_evaluations_candidate_role_idx
    ON fzh.model_evaluations(candidate_key, task_role, observed_at DESC);
CREATE INDEX IF NOT EXISTS model_evaluations_valid_idx
    ON fzh.model_evaluations(valid_until, synthetic);

CREATE OR REPLACE FUNCTION fzh.rank_model_candidates(
    p_task_role text,
    p_classification text,
    p_hardware_fingerprint text DEFAULT NULL
)
RETURNS TABLE (
    candidate_key text,
    provider_key text,
    model_id text,
    route_alias text,
    route_kind text,
    cost_class text,
    evaluation_id bigint,
    score numeric,
    latency_ms numeric,
    throughput_tps numeric,
    observed_at timestamptz,
    valid_until timestamptz
)
LANGUAGE sql
STABLE
AS $$
WITH latest_valid AS (
    SELECT DISTINCT ON (e.candidate_key)
        e.*
    FROM fzh.model_evaluations e
    JOIN fzh.model_candidates c ON c.candidate_key = e.candidate_key
    JOIN fzh.model_providers p ON p.provider_key = c.provider_key
    WHERE e.task_role = p_task_role
      AND e.synthetic = false
      AND e.valid_until > now()
      AND (
          (p.route_kind = 'remote' AND e.hardware_fingerprint IS NULL)
          OR
          (p.route_kind = 'local'
              AND p_hardware_fingerprint IS NOT NULL
              AND e.hardware_fingerprint = p_hardware_fingerprint)
      )
    ORDER BY e.candidate_key, e.observed_at DESC, e.evaluation_id DESC
), eligible AS (
    SELECT
        c.candidate_key,
        c.provider_key,
        c.model_id,
        c.route_alias,
        p.route_kind,
        c.cost_class,
        e.evaluation_id,
        e.latency_ms,
        e.throughput_tps,
        e.observed_at,
        e.valid_until,
        (
            COALESCE(e.quality_score, 0) * 0.35
          + COALESCE(e.task_success_rate, 0) * 0.30
          + COALESCE(e.tool_call_reliability, 0) * 0.10
          + COALESCE(e.structured_output_adherence, 0) * 0.10
          + COALESCE(e.availability_score, 0) * 0.15
          - COALESCE(e.error_rate, 0) * 0.20
        )::numeric AS score
    FROM latest_valid e
    JOIN fzh.model_candidates c ON c.candidate_key = e.candidate_key
    JOIN fzh.model_providers p ON p.provider_key = c.provider_key
    WHERE c.status = 'approved'
      AND c.enabled = true
      AND p.enabled = true
      AND c.cost_class <> 'paid'
      AND CASE upper(p_classification)
          WHEN 'PUBLIC' THEN p.approved_public
          WHEN 'INTERNAL' THEN p.approved_internal
          WHEN 'CONFIDENTIAL' THEN
              p.approved_confidential
              AND (
                  p.route_kind = 'local'
                  OR (p.zero_retention IS TRUE AND p.training_allowed IS FALSE)
              )
          WHEN 'SECRET' THEN false
          ELSE false
      END
)
SELECT
    eligible.candidate_key,
    eligible.provider_key,
    eligible.model_id,
    eligible.route_alias,
    eligible.route_kind,
    eligible.cost_class,
    eligible.evaluation_id,
    eligible.score,
    eligible.latency_ms,
    eligible.throughput_tps,
    eligible.observed_at,
    eligible.valid_until
FROM eligible
ORDER BY eligible.score DESC,
         eligible.latency_ms ASC NULLS LAST,
         eligible.candidate_key ASC;
$$;

COMMENT ON FUNCTION fzh.rank_model_candidates(text, text, text) IS
'Filters by provider/privacy approval, candidate promotion, freshness, hardware scope and paid-fallback policy before ranking. SECRET and unknown classifications return no candidates.';

INSERT INTO fzh.system_metadata (key, value)
VALUES ('model_registry', '{"schema_version":1,"paid_fallback_enabled":false,"promotion":"explicit_only"}'::jsonb)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = now();

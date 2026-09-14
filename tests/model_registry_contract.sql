\set ON_ERROR_STOP on
BEGIN;

INSERT INTO fzh.model_providers (
    provider_key, display_name, route_kind, enabled,
    approved_public, approved_internal, approved_confidential,
    zero_retention, training_allowed, approval_evidence
) VALUES
    ('test-public', 'Public remote', 'remote', true, true, false, false, NULL, NULL, 'fixture'),
    ('test-internal', 'Internal remote', 'remote', true, true, true, false, NULL, NULL, 'fixture'),
    ('test-conf', 'Confidential remote', 'remote', true, true, true, true, true, false, 'fixture'),
    ('test-conf-unknown', 'Unknown privacy remote', 'remote', true, true, true, true, NULL, NULL, 'fixture'),
    ('test-local', 'Local Ollama fixture', 'local', true, true, true, true, true, false, 'fixture'),
    ('test-disabled', 'Disabled provider', 'remote', false, true, true, true, true, false, 'fixture')
ON CONFLICT (provider_key) DO NOTHING;

INSERT INTO fzh.model_candidates (
    candidate_key, provider_key, model_id, route_alias, identity_kind,
    cost_class, status, enabled, approval_evidence, approved_at
) VALUES
    ('test-free-router', 'test-public', 'openrouter/openrouter/free', 'fzh-free-auto', 'dynamic_router', 'free', 'approved', true, 'fixture', now()),
    ('test-internal-model', 'test-internal', 'fixture/internal', NULL, 'deterministic', 'free', 'approved', true, 'fixture', now()),
    ('test-conf-model', 'test-conf', 'fixture/conf', NULL, 'deterministic', 'free', 'approved', true, 'fixture', now()),
    ('test-conf-unknown-model', 'test-conf-unknown', 'fixture/conf-unknown', NULL, 'deterministic', 'free', 'approved', true, 'fixture', now()),
    ('test-local-model', 'test-local', 'fixture/local', NULL, 'deterministic', 'local', 'approved', true, 'fixture', now()),
    ('test-paid-model', 'test-conf', 'fixture/paid', NULL, 'deterministic', 'paid', 'approved', true, 'fixture', now()),
    ('test-disabled-candidate', 'test-conf', 'fixture/disabled-candidate', NULL, 'deterministic', 'free', 'disabled', false, NULL, NULL),
    ('test-unpromoted', 'test-conf', 'fixture/unpromoted', NULL, 'deterministic', 'free', 'candidate', true, NULL, NULL),
    ('test-disabled-provider-model', 'test-disabled', 'fixture/disabled-provider', NULL, 'deterministic', 'free', 'approved', true, 'fixture', now()),
    ('test-synthetic-model', 'test-conf', 'fixture/synthetic', NULL, 'deterministic', 'free', 'approved', true, 'fixture', now()),
    ('test-stale-model', 'test-conf', 'fixture/stale', NULL, 'deterministic', 'free', 'approved', true, 'fixture', now())
ON CONFLICT (candidate_key) DO NOTHING;

INSERT INTO fzh.model_evaluations (
    candidate_key, task_role, evaluation_suite, evaluator_version,
    hardware_fingerprint, synthetic, quality_score, task_success_rate,
    tool_call_reliability, structured_output_adherence, availability_score,
    error_rate, latency_ms, throughput_tps, evidence_ref, evidence_sha256,
    observed_at, valid_until
) VALUES
    ('test-free-router', 'planner', 'fixture', '1', NULL, false, 0.70, 0.80, 0.70, 0.70, 0.70, 0.05, 600, NULL, 'fixture:free', repeat('a', 64), now(), now() + interval '1 day'),
    ('test-internal-model', 'planner', 'fixture', '1', NULL, false, 0.75, 0.80, 0.75, 0.75, 0.80, 0.03, 500, NULL, 'fixture:internal', repeat('b', 64), now(), now() + interval '1 day'),
    ('test-conf-model', 'planner', 'fixture', '1', NULL, false, 0.90, 0.90, 0.90, 0.90, 0.90, 0.01, 450, NULL, 'fixture:conf', repeat('c', 64), now(), now() + interval '1 day'),
    ('test-conf-unknown-model', 'planner', 'fixture', '1', NULL, false, 0.99, 0.99, 0.99, 0.99, 0.99, 0.00, 100, NULL, 'fixture:unknown', repeat('d', 64), now(), now() + interval '1 day'),
    ('test-local-model', 'planner', 'fixture', '1', 'xps-fixture', false, 0.65, 0.75, 0.70, 0.70, 1.00, 0.00, 1200, 8.5, 'fixture:local', repeat('e', 64), now(), now() + interval '1 day'),
    ('test-paid-model', 'planner', 'fixture', '1', NULL, false, 1.00, 1.00, 1.00, 1.00, 1.00, 0.00, 50, NULL, 'fixture:paid', repeat('f', 64), now(), now() + interval '1 day'),
    ('test-disabled-candidate', 'planner', 'fixture', '1', NULL, false, 1.00, 1.00, 1.00, 1.00, 1.00, 0.00, 50, NULL, 'fixture:disabled-candidate', repeat('1', 64), now(), now() + interval '1 day'),
    ('test-unpromoted', 'planner', 'fixture', '1', NULL, false, 1.00, 1.00, 1.00, 1.00, 1.00, 0.00, 50, NULL, 'fixture:unpromoted', repeat('2', 64), now(), now() + interval '1 day'),
    ('test-disabled-provider-model', 'planner', 'fixture', '1', NULL, false, 1.00, 1.00, 1.00, 1.00, 1.00, 0.00, 50, NULL, 'fixture:disabled-provider', repeat('3', 64), now(), now() + interval '1 day'),
    ('test-synthetic-model', 'planner', 'fixture', '1', NULL, true, 1.00, 1.00, 1.00, 1.00, 1.00, 0.00, 50, NULL, 'fixture:synthetic', repeat('4', 64), now(), now() + interval '1 day'),
    ('test-stale-model', 'planner', 'fixture', '1', NULL, false, 1.00, 1.00, 1.00, 1.00, 1.00, 0.00, 50, NULL, 'fixture:stale', repeat('5', 64), now() - interval '2 day', now() - interval '1 day');

-- Discovery/fit evidence can be imported without creating or promoting a model.
INSERT INTO fzh.model_candidate_evidence (
    source_system, source_version, use_case, source_candidate_name,
    hardware_fingerprint, synthetic, linked_candidate_key,
    evidence_ref, evidence_sha256, source_payload, observed_at
) VALUES (
    'llmfit', '1.1.15', 'reasoning', 'fixture-not-a-production-model',
    'xps-fixture', false, NULL,
    'fixture:b025', repeat('6', 64), '{"fit":"good"}'::jsonb, now()
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM fzh.model_candidates
        WHERE candidate_key = 'fixture-not-a-production-model'
           OR model_id = 'fixture-not-a-production-model'
    ) THEN
        RAISE EXCEPTION 'candidate evidence must not auto-create/promote model candidates';
    END IF;

    IF (SELECT count(*) FROM fzh.rank_model_candidates('planner', 'SECRET', 'xps-fixture')) <> 0 THEN
        RAISE EXCEPTION 'SECRET must never return an LLM candidate';
    END IF;

    IF (SELECT count(*) FROM fzh.rank_model_candidates('planner', 'UNKNOWN', 'xps-fixture')) <> 0 THEN
        RAISE EXCEPTION 'unknown classification must fail closed';
    END IF;

    IF EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'PUBLIC', NULL)
        WHERE candidate_key IN ('test-local-model', 'test-paid-model', 'test-disabled-candidate',
                                'test-unpromoted', 'test-disabled-provider-model',
                                'test-synthetic-model', 'test-stale-model')
    ) THEN
        RAISE EXCEPTION 'PUBLIC ranking admitted an ineligible candidate';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'PUBLIC', NULL)
        WHERE candidate_key = 'test-free-router' AND route_alias = 'fzh-free-auto'
    ) THEN
        RAISE EXCEPTION 'approved dynamic free router fixture should be PUBLIC eligible';
    END IF;

    IF EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'INTERNAL', NULL)
        WHERE candidate_key = 'test-free-router'
    ) THEN
        RAISE EXCEPTION 'PUBLIC-only provider must not be INTERNAL eligible';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'INTERNAL', NULL)
        WHERE candidate_key IN ('test-internal-model', 'test-conf-model')
    ) THEN
        RAISE EXCEPTION 'approved INTERNAL providers should be eligible';
    END IF;

    IF EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'CONFIDENTIAL', 'xps-fixture')
        WHERE candidate_key = 'test-conf-unknown-model'
    ) THEN
        RAISE EXCEPTION 'unknown privacy properties must fail closed for CONFIDENTIAL';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'CONFIDENTIAL', 'xps-fixture')
        WHERE candidate_key = 'test-conf-model'
    ) THEN
        RAISE EXCEPTION 'zero-retention/no-training CONFIDENTIAL remote candidate missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'CONFIDENTIAL', 'xps-fixture')
        WHERE candidate_key = 'test-local-model'
    ) THEN
        RAISE EXCEPTION 'matching hardware-scoped local candidate missing';
    END IF;

    IF EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'CONFIDENTIAL', 'different-hardware')
        WHERE candidate_key = 'test-local-model'
    ) THEN
        RAISE EXCEPTION 'local evidence must not leak across hardware fingerprints';
    END IF;

    IF EXISTS (
        SELECT 1 FROM fzh.rank_model_candidates('planner', 'CONFIDENTIAL', 'xps-fixture')
        WHERE cost_class = 'paid'
    ) THEN
        RAISE EXCEPTION 'paid fallback must remain disabled';
    END IF;
END
$$;

ROLLBACK;

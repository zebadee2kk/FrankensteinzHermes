\set ON_ERROR_STOP on

TRUNCATE TABLE
    fzh.job_effects,
    fzh.job_gate_decisions,
    fzh.job_events,
    fzh.job_attempts,
    fzh.jobs
RESTART IDENTITY;

DO $$
BEGIN
    IF has_table_privilege('fzh_b032_worker', 'fzh.jobs', 'SELECT')
       OR has_table_privilege('fzh_b032_worker', 'fzh.jobs', 'INSERT')
       OR has_table_privilege('fzh_b032_worker', 'fzh.jobs', 'UPDATE')
       OR has_table_privilege('fzh_b032_worker', 'fzh.job_attempts', 'SELECT') THEN
        RAISE EXCEPTION 'B032 worker unexpectedly has direct ledger table privilege';
    END IF;

    IF NOT has_function_privilege('fzh_b032_worker', 'fzh.b032_lease_job(text,integer)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b032_worker', 'fzh.b032_record_gate(uuid,uuid,text,text,text,uuid,text)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b032_worker', 'fzh.b032_start_job(uuid,uuid)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b032_worker', 'fzh.b032_begin_candidate_effect(uuid,uuid)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b032_worker', 'fzh.b032_complete_job(uuid,uuid,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'B032 worker is missing an intended capability function';
    END IF;

    IF has_function_privilege('fzh_b032_worker', 'fzh.lease_next_job(text,integer,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b032_worker', 'fzh.complete_job(uuid,uuid,jsonb,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b032_worker', 'fzh.submit_job(text,text,jsonb,boolean,text,text,text,integer,integer,timestamptz,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'B032 worker unexpectedly has generic ledger function access';
    END IF;
END;
$$;

SELECT fzh.submit_job(
    'engineering.implement',
    'b032-privilege-contract',
    '{"objective":"privilege contract","base_commit":"0000000000000000000000000000000000000000","allowed_paths":["app/"]}'::jsonb,
    true,
    'L1',
    'workspace_branch_write',
    'INTERNAL',
    2,
    1,
    now(),
    'b032-privilege-contract'
);

SET ROLE fzh_b032_worker;

SELECT job_id, lease_token
FROM fzh.b032_lease_job('b032-contract-worker', 300)
\gset

SELECT fzh.b032_record_gate(
    :'job_id'::uuid,
    :'lease_token'::uuid,
    'allow',
    repeat('a', 64),
    repeat('b', 64),
    '55555555-5555-4555-8555-555555555555'::uuid,
    'risk_default:L1:allow'
);

SELECT fzh.b032_start_job(:'job_id'::uuid, :'lease_token'::uuid);
SELECT fzh.b032_heartbeat(:'job_id'::uuid, :'lease_token'::uuid, 300);
SELECT fzh.b032_begin_candidate_effect(:'job_id'::uuid, :'lease_token'::uuid);
SELECT fzh.b032_commit_candidate_effect(
    :'job_id'::uuid,
    :'lease_token'::uuid,
    '1111111111111111111111111111111111111111'
);
SELECT fzh.b032_complete_job(
    :'job_id'::uuid,
    :'lease_token'::uuid,
    '{"head_commit":"1111111111111111111111111111111111111111","evidence_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}'::jsonb
);

RESET ROLE;

SELECT CASE
    WHEN (SELECT status FROM fzh.jobs WHERE job_id = :'job_id'::uuid) = 'succeeded'
    THEN 1 ELSE 1 / 0 END AS succeeded_assertion;

SELECT CASE
    WHEN (SELECT count(*)
          FROM fzh.job_gate_decisions
          WHERE job_id = :'job_id'::uuid
            AND audit_event_id = '55555555-5555-4555-8555-555555555555'::uuid
            AND decision = 'allow') = 1
    THEN 1 ELSE 1 / 0 END AS gate_assertion;

SELECT CASE
    WHEN (SELECT state
          FROM fzh.job_effects
          WHERE job_id = :'job_id'::uuid
            AND effect_key = 'b032:candidate-commit:' || :'job_id') = 'committed'
    THEN 1 ELSE 1 / 0 END AS effect_assertion;

SELECT 'B032 worker privilege contract passed' AS result;

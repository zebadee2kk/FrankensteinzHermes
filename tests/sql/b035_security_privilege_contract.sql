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
    IF has_table_privilege('fzh_b035_security', 'fzh.jobs', 'SELECT')
       OR has_table_privilege('fzh_b035_security', 'fzh.jobs', 'UPDATE')
       OR has_table_privilege('fzh_b035_security', 'fzh.job_effects', 'SELECT') THEN
        RAISE EXCEPTION 'B035 security reviewer unexpectedly has direct ledger table privilege';
    END IF;

    IF NOT has_function_privilege('fzh_b035_security', 'fzh.b035_lease_job(text,integer)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b035_security', 'fzh.b035_start_job(uuid,uuid)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b035_security', 'fzh.b035_complete_job(uuid,uuid,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'B035 security reviewer is missing intended capability functions';
    END IF;

    IF has_function_privilege('fzh_b035_security', 'fzh.lease_next_job(text,integer,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b035_security', 'fzh.begin_job_effect(uuid,uuid,text,jsonb,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b035_security', 'fzh.complete_job(uuid,uuid,jsonb,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b035_security', 'fzh.b032_begin_candidate_effect(uuid,uuid)', 'EXECUTE')
       OR has_function_privilege('fzh_b035_security', 'fzh.b033_start_job(uuid,uuid)', 'EXECUTE')
       OR has_function_privilege('fzh_b035_security', 'fzh.b034_start_job(uuid,uuid)', 'EXECUTE')
       OR has_function_privilege('fzh_b035_security', 'fzh.b034_begin_profile_effect(uuid,uuid,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'B035 security reviewer unexpectedly inherited generic/B032/B033/B034 capability';
    END IF;
END;
$$;

SELECT fzh.submit_job(
    'engineering.qa', 'b035-must-not-lease-qa', '{"objective":"not security review"}'::jsonb,
    true, 'L1', 'sandboxed_qa_execute', 'INTERNAL', 2, 1, now(), 'b035-contract'
);
SELECT fzh.submit_job(
    'engineering.security_review', 'b035-security-contract',
    '{"qa_job_id":"22222222-2222-4222-8222-222222222222","objective":"security review","base_commit":"0000000000000000000000000000000000000000","head_commit":"1111111111111111111111111111111111111111","branch":"fzh/job-111111111111"}'::jsonb,
    false, 'L0', 'security_review_candidate', 'INTERNAL', 2, 1, now(), 'b035-contract'
);

SET ROLE fzh_b035_security;
SELECT job_id, lease_token, job_kind, side_effecting
FROM fzh.b035_lease_job('b035-contract-security', 300)
\gset

\if :{?job_id}
\else
  \echo 'B035 did not lease a security review job'
  \quit 3
\endif

SELECT CASE WHEN :'job_kind' = 'engineering.security_review' AND :'side_effecting' = 'f' THEN true ELSE false END AS leased_security_job
\gset
\if :leased_security_job
\else
  \echo 'B035 leased wrong job kind or side-effecting job'
  \quit 4
\endif

SELECT fzh.b035_start_job(:'job_id'::uuid, :'lease_token'::uuid);
SELECT fzh.b035_heartbeat(:'job_id'::uuid, :'lease_token'::uuid, 300);
SELECT fzh.b035_complete_job(
    :'job_id'::uuid,
    :'lease_token'::uuid,
    '{"security_status":"security_passed","evidence_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","manifest_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","promotion_authorized":false,"next_stage":"B036 release"}'::jsonb
);
RESET ROLE;

CREATE TEMP TABLE b035_contract_context(job_id uuid PRIMARY KEY);
INSERT INTO b035_contract_context(job_id) VALUES (:'job_id'::uuid);

DO $$
DECLARE
    v_job_id uuid;
    v_status text;
    v_security_status text;
    v_neighbor_bad integer;
    v_effects integer;
BEGIN
    SELECT job_id INTO v_job_id FROM b035_contract_context;
    SELECT status, result->>'security_status' INTO v_status, v_security_status
    FROM fzh.jobs WHERE job_id = v_job_id;
    IF v_status <> 'succeeded' OR v_security_status <> 'security_passed' THEN
        RAISE EXCEPTION 'B035 scoped lifecycle invalid: status=%, security_status=%', v_status, v_security_status;
    END IF;

    SELECT count(*) INTO v_neighbor_bad
    FROM fzh.jobs WHERE job_kind='engineering.qa' AND status <> 'queued';
    IF v_neighbor_bad <> 0 THEN
        RAISE EXCEPTION 'B035 touched neighboring QA job';
    END IF;

    SELECT count(*) INTO v_effects FROM fzh.job_effects WHERE job_id=v_job_id;
    IF v_effects <> 0 THEN
        RAISE EXCEPTION 'B035 read-only review unexpectedly created durable effects';
    END IF;
END;
$$;

SELECT 'B035 security reviewer privilege contract passed' AS result;

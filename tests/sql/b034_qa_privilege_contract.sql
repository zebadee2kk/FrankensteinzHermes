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
    IF has_table_privilege('fzh_b034_qa', 'fzh.jobs', 'SELECT')
       OR has_table_privilege('fzh_b034_qa', 'fzh.jobs', 'UPDATE')
       OR has_table_privilege('fzh_b034_qa', 'fzh.job_events', 'SELECT') THEN
        RAISE EXCEPTION 'B034 QA unexpectedly has direct ledger table privilege';
    END IF;

    IF NOT has_function_privilege('fzh_b034_qa', 'fzh.b034_lease_job(text,integer)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b034_qa', 'fzh.b034_record_gate(uuid,uuid,text,text,text,uuid,text)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b034_qa', 'fzh.b034_start_job(uuid,uuid)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b034_qa', 'fzh.b034_begin_profile_effect(uuid,uuid,text)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b034_qa', 'fzh.b034_commit_profile_effect(uuid,uuid,text,text)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b034_qa', 'fzh.b034_complete_job(uuid,uuid,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'B034 QA is missing an intended capability function';
    END IF;

    IF has_function_privilege('fzh_b034_qa', 'fzh.lease_next_job(text,integer,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b034_qa', 'fzh.begin_job_effect(uuid,uuid,text,jsonb,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b034_qa', 'fzh.commit_job_effect(uuid,uuid,text,text,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b034_qa', 'fzh.complete_job(uuid,uuid,jsonb,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b034_qa', 'fzh.b032_begin_candidate_effect(uuid,uuid)', 'EXECUTE')
       OR has_function_privilege('fzh_b034_qa', 'fzh.b033_start_job(uuid,uuid)', 'EXECUTE')
       OR has_function_privilege('fzh_b034_qa', 'fzh.b033_complete_job(uuid,uuid,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'B034 QA unexpectedly inherited generic/B032/B033 capability';
    END IF;
END;
$$;

-- Neighboring jobs prove leasing is constrained to engineering.qa.
SELECT fzh.submit_job(
    'engineering.implement', 'b034-must-not-lease-impl', '{"objective":"not QA"}'::jsonb,
    true, 'L1', 'workspace_branch_write', 'INTERNAL', 2, 1, now(), 'b034-contract'
);
SELECT fzh.submit_job(
    'engineering.review', 'b034-must-not-lease-review', '{"objective":"not QA"}'::jsonb,
    false, 'L0', 'review_candidate', 'INTERNAL', 2, 1, now(), 'b034-contract'
);
SELECT fzh.submit_job(
    'engineering.qa', 'b034-qa-contract',
    '{"review_job_id":"11111111-1111-4111-8111-111111111111","objective":"adversarial QA","base_commit":"0000000000000000000000000000000000000000","head_commit":"1111111111111111111111111111111111111111","branch":"fzh/job-111111111111"}'::jsonb,
    true, 'L1', 'sandboxed_qa_execute', 'INTERNAL', 2, 1, now(), 'b034-contract'
);

SET ROLE fzh_b034_qa;
SELECT job_id, lease_token, job_kind
FROM fzh.b034_lease_job('b034-contract-qa', 300)
\gset

\if :{?job_id}
\else
  \echo 'B034 QA did not lease a QA job'
  \quit 3
\endif

SELECT CASE WHEN :'job_kind' = 'engineering.qa' THEN true ELSE false END AS leased_qa_job
\gset
\if :leased_qa_job
\else
  \echo 'B034 QA leased wrong job kind'
  \quit 4
\endif

SELECT fzh.b034_record_gate(
    :'job_id'::uuid,
    :'lease_token'::uuid,
    'allow',
    repeat('a', 64),
    repeat('b', 64),
    '55555555-5555-4555-8555-555555555555'::uuid,
    'risk_default:L1:allow'
);
SELECT fzh.b034_start_job(:'job_id'::uuid, :'lease_token'::uuid);
SELECT fzh.b034_heartbeat(:'job_id'::uuid, :'lease_token'::uuid, 300);
SELECT fzh.b034_begin_profile_effect(:'job_id'::uuid, :'lease_token'::uuid, 'python-unit');
SELECT fzh.b034_commit_profile_effect(
    :'job_id'::uuid,
    :'lease_token'::uuid,
    'python-unit',
    repeat('c', 64)
);
SELECT fzh.b034_complete_job(
    :'job_id'::uuid,
    :'lease_token'::uuid,
    '{"qa_status":"qa_passed","evidence_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","manifest_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","promotion_authorized":false,"next_stage":"B035 security review"}'::jsonb
);
RESET ROLE;

CREATE TEMP TABLE b034_contract_context(job_id uuid PRIMARY KEY);
INSERT INTO b034_contract_context(job_id) VALUES (:'job_id'::uuid);

DO $$
DECLARE
    v_job_id uuid;
    v_status text;
    v_qa_status text;
    v_neighbor_bad integer;
    v_gate_count integer;
    v_effect_state text;
    v_effect_receipt text;
BEGIN
    SELECT job_id INTO v_job_id FROM b034_contract_context;
    SELECT status, result->>'qa_status' INTO v_status, v_qa_status
    FROM fzh.jobs WHERE job_id = v_job_id;
    IF v_status <> 'succeeded' OR v_qa_status <> 'qa_passed' THEN
        RAISE EXCEPTION 'B034 scoped QA lifecycle invalid: status=%, qa_status=%', v_status, v_qa_status;
    END IF;

    SELECT count(*) INTO v_neighbor_bad
    FROM fzh.jobs
    WHERE job_kind IN ('engineering.implement','engineering.review') AND status <> 'queued';
    IF v_neighbor_bad <> 0 THEN
        RAISE EXCEPTION 'B034 touched neighboring engineering stages';
    END IF;

    SELECT count(*) INTO v_gate_count
    FROM fzh.job_gate_decisions
    WHERE job_id = v_job_id
      AND audit_event_id = '55555555-5555-4555-8555-555555555555'::uuid
      AND decision = 'allow';
    IF v_gate_count <> 1 THEN
        RAISE EXCEPTION 'expected exactly one persisted B030 gate decision';
    END IF;

    SELECT state, external_receipt INTO v_effect_state, v_effect_receipt
    FROM fzh.job_effects
    WHERE job_id = v_job_id
      AND effect_key = 'b034:qa-profile:' || v_job_id::text || ':python-unit';
    IF v_effect_state <> 'committed' OR v_effect_receipt <> repeat('c', 64) THEN
        RAISE EXCEPTION 'B034 QA effect invalid: state=%, receipt=%', v_effect_state, v_effect_receipt;
    END IF;
END;
$$;

SELECT 'B034 QA privilege contract passed' AS result;

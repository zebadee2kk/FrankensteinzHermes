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
    IF has_table_privilege('fzh_b033_reviewer', 'fzh.jobs', 'SELECT')
       OR has_table_privilege('fzh_b033_reviewer', 'fzh.jobs', 'UPDATE')
       OR has_table_privilege('fzh_b033_reviewer', 'fzh.job_events', 'SELECT') THEN
        RAISE EXCEPTION 'B033 reviewer unexpectedly has direct ledger table privilege';
    END IF;

    IF NOT has_function_privilege('fzh_b033_reviewer', 'fzh.b033_lease_job(text,integer)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b033_reviewer', 'fzh.b033_start_job(uuid,uuid)', 'EXECUTE')
       OR NOT has_function_privilege('fzh_b033_reviewer', 'fzh.b033_complete_job(uuid,uuid,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'B033 reviewer is missing an intended capability function';
    END IF;

    IF has_function_privilege('fzh_b033_reviewer', 'fzh.lease_next_job(text,integer,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b033_reviewer', 'fzh.complete_job(uuid,uuid,jsonb,text)', 'EXECUTE')
       OR has_function_privilege('fzh_b033_reviewer', 'fzh.b032_begin_candidate_effect(uuid,uuid)', 'EXECUTE')
       OR has_function_privilege('fzh_b033_reviewer', 'fzh.b032_commit_candidate_effect(uuid,uuid,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'B033 reviewer unexpectedly inherited generic/B032 mutation capability';
    END IF;
END;
$$;

-- A nearby implementation job proves B033 leasing is constrained by job kind.
SELECT fzh.submit_job(
    'engineering.implement', 'b033-must-not-lease', '{"objective":"not review"}'::jsonb,
    true, 'L1', 'workspace_branch_write', 'INTERNAL', 2, 1, now(), 'b033-contract'
);
SELECT fzh.submit_job(
    'engineering.review', 'b033-review-contract',
    '{"implementation_job_id":"11111111-1111-4111-8111-111111111111","objective":"review candidate"}'::jsonb,
    false, 'L0', 'review_candidate', 'INTERNAL', 2, 1, now(), 'b033-contract'
);

SET ROLE fzh_b033_reviewer;
SELECT job_id, lease_token, job_kind
FROM fzh.b033_lease_job('b033-contract-reviewer', 300)
\gset

\if :{?job_id}
\else
  \echo 'B033 reviewer did not lease a review job'
  \quit 3
\endif

SELECT CASE WHEN :'job_kind' = 'engineering.review' THEN true ELSE false END AS leased_review_job
\gset
\if :leased_review_job
\else
  \echo 'B033 reviewer leased wrong job kind'
  \quit 4
\endif

SELECT fzh.b033_start_job(:'job_id'::uuid, :'lease_token'::uuid);
SELECT fzh.b033_heartbeat(:'job_id'::uuid, :'lease_token'::uuid, 300);
SELECT fzh.b033_complete_job(
    :'job_id'::uuid,
    :'lease_token'::uuid,
    '{"verdict":"approve","evidence_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","manifest_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","promotion_authorized":false}'::jsonb
);
RESET ROLE;

CREATE TEMP TABLE b033_contract_context(job_id uuid PRIMARY KEY);
INSERT INTO b033_contract_context(job_id) VALUES (:'job_id'::uuid);

DO $$
DECLARE
    v_job_id uuid;
    v_status text;
    v_verdict text;
    v_implement_status text;
BEGIN
    SELECT job_id INTO v_job_id FROM b033_contract_context;
    SELECT status, result->>'verdict' INTO v_status, v_verdict FROM fzh.jobs WHERE job_id = v_job_id;
    IF v_status <> 'succeeded' OR v_verdict <> 'approve' THEN
        RAISE EXCEPTION 'B033 scoped review lifecycle invalid: status=%, verdict=%', v_status, v_verdict;
    END IF;
    SELECT status INTO v_implement_status FROM fzh.jobs WHERE job_kind = 'engineering.implement';
    IF v_implement_status <> 'queued' THEN
        RAISE EXCEPTION 'B033 touched implementation job: %', v_implement_status;
    END IF;
END;
$$;

SELECT 'B033 reviewer privilege contract passed' AS result;

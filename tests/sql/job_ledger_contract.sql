\set ON_ERROR_STOP on

TRUNCATE TABLE fzh.job_effects, fzh.job_gate_decisions, fzh.job_events, fzh.job_attempts, fzh.jobs RESTART IDENTITY;

DO $$
DECLARE
    a uuid;
    b uuid;
    j fzh.jobs%ROWTYPE;
    att fzh.job_attempts%ROWTYPE;
    n integer;
BEGIN
    -- Stable ID + conflict-safe idempotency.
    SELECT (fzh.submit_job('test.idem','same','{"x":1}'::jsonb,false,'L0','read_status','PUBLIC',3,100,now(),'contract')).job_id INTO a;
    SELECT (fzh.submit_job('test.idem','same','{"x":1}'::jsonb,false,'L0','read_status','PUBLIC',3,100,now(),'contract')).job_id INTO b;
    IF a IS DISTINCT FROM b THEN RAISE EXCEPTION 'idempotent job IDs differ'; END IF;
    SELECT count(*) INTO n FROM fzh.jobs WHERE job_kind='test.idem';
    IF n <> 1 THEN RAISE EXCEPTION 'duplicate job created'; END IF;
    BEGIN
        PERFORM fzh.submit_job('test.idem','same','{"x":2}'::jsonb,false,'L0','read_status','PUBLIC',3,100,now(),'contract');
        RAISE EXCEPTION 'expected idempotency conflict';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    BEGIN
        UPDATE fzh.jobs SET max_attempts=20 WHERE job_id=a;
        RAISE EXCEPTION 'expected immutable budget rejection';
    EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
    END;

    -- Basic non-side-effect execution and terminal protection.
    PERFORM fzh.submit_job('test.simple','1','{}'::jsonb,false,'L0','read_status','PUBLIC',2,10,now(),'contract');
    PERFORM * FROM fzh.lease_next_job('worker-simple',300,'test.simple');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.simple';
    IF j.status <> 'leased' OR j.attempt_count <> 1 OR j.lease_count <> 1 THEN RAISE EXCEPTION 'simple lease accounting wrong'; END IF;
    PERFORM fzh.start_job(j.job_id,j.lease_token,'worker-simple');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.simple';
    PERFORM fzh.complete_job(j.job_id,j.lease_token,'{"ok":true}'::jsonb,'worker-simple');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.simple';
    IF j.status <> 'succeeded' OR j.terminal_at IS NULL THEN RAISE EXCEPTION 'simple job not terminal success'; END IF;
    PERFORM * FROM fzh.lease_next_job('other',300,'test.simple'); GET DIAGNOSTICS n=ROW_COUNT;
    IF n <> 0 THEN RAISE EXCEPTION 'terminal job re-leased'; END IF;
    SELECT * INTO att FROM fzh.job_attempts WHERE job_id=j.job_id LIMIT 1;
    BEGIN
        UPDATE fzh.job_attempts SET worker_id='tamper' WHERE attempt_id=att.attempt_id;
        RAISE EXCEPTION 'expected append-only attempt rejection';
    EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
    END;
    BEGIN
        UPDATE fzh.job_events SET actor='tamper' WHERE job_id=j.job_id;
        RAISE EXCEPTION 'expected append-only event rejection';
    EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
    END;

    -- Approval waits without consuming retry budget, while lease sequence remains auditable.
    PERFORM fzh.submit_job('test.approval','1','{}'::jsonb,true,'L2','production_deploy','INTERNAL',3,20,now(),'contract');
    PERFORM * FROM fzh.lease_next_job('worker-approval',300,'test.approval');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.approval';
    PERFORM fzh.record_job_gate_decision(j.job_id,j.lease_token,'approval_required',repeat('a',64),repeat('b',64),'11111111-1111-4111-8111-111111111111'::uuid,'risk_default:L2:approval_required','worker-approval');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.approval';
    IF j.status <> 'waiting_approval' OR j.attempt_count <> 0 OR j.lease_count <> 1 OR j.lease_token IS NOT NULL THEN
        RAISE EXCEPTION 'approval budget/lease state wrong: status %, budget %, leases %',j.status,j.attempt_count,j.lease_count;
    END IF;
    PERFORM * FROM fzh.lease_next_job('other',300,'test.approval'); GET DIAGNOSTICS n=ROW_COUNT;
    IF n <> 0 THEN RAISE EXCEPTION 'waiting approval job leased'; END IF;

    -- Denied jobs are terminal and never execute.
    PERFORM fzh.submit_job('test.denied','1','{}'::jsonb,true,'L3','credential_export','SECRET',2,20,now(),'contract');
    PERFORM * FROM fzh.lease_next_job('worker-deny',300,'test.denied');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.denied';
    PERFORM fzh.record_job_gate_decision(j.job_id,j.lease_token,'deny',repeat('c',64),repeat('d',64),'22222222-2222-4222-8222-222222222222'::uuid,'hard_deny_action:credential_export','worker-deny');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.denied';
    IF j.status <> 'denied' OR j.terminal_at IS NULL THEN RAISE EXCEPTION 'deny not terminal'; END IF;

    -- A side-effecting job cannot start without gate allow evidence.
    PERFORM fzh.submit_job('test.ambiguous','1','{}'::jsonb,true,'L1','workspace_branch_write','INTERNAL',3,30,now(),'contract');
    PERFORM * FROM fzh.lease_next_job('worker-effect',300,'test.ambiguous');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.ambiguous';
    BEGIN
        PERFORM fzh.start_job(j.job_id,j.lease_token,'worker-effect');
        RAISE EXCEPTION 'expected gate evidence rejection';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM='expected gate evidence rejection' THEN RAISE; END IF;
    END;
    PERFORM fzh.record_job_gate_decision(j.job_id,j.lease_token,'allow',repeat('e',64),repeat('f',64),'33333333-3333-4333-8333-333333333333'::uuid,'risk_default:L1:allow','worker-effect');
    PERFORM fzh.start_job(j.job_id,j.lease_token,'worker-effect');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.ambiguous';
    PERFORM fzh.begin_job_effect(j.job_id,j.lease_token,'effect:ambiguous:1','{}'::jsonb,'worker-effect');
    UPDATE fzh.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=j.job_id;
    PERFORM * FROM fzh.reap_expired_jobs('contract-reaper');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.ambiguous';
    IF j.status <> 'reconciliation_required' THEN RAISE EXCEPTION 'ambiguous side effect auto-retried: %',j.status; END IF;
    PERFORM * FROM fzh.lease_next_job('other',300,'test.ambiguous'); GET DIAGNOSTICS n=ROW_COUNT;
    IF n <> 0 THEN RAISE EXCEPTION 'reconciliation job leased'; END IF;

    -- Safe crashes retry only to the finite budget, then dead-letter.
    PERFORM fzh.submit_job('test.retry','1','{}'::jsonb,false,'L0','read_status','PUBLIC',2,40,now(),'contract');
    PERFORM * FROM fzh.lease_next_job('retry-1',300,'test.retry');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.retry';
    UPDATE fzh.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=j.job_id;
    PERFORM * FROM fzh.reap_expired_jobs('contract-reaper');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.retry';
    IF j.status <> 'retry_wait' OR j.attempt_count <> 1 THEN RAISE EXCEPTION 'first safe expiry not retryable'; END IF;
    PERFORM * FROM fzh.lease_next_job('retry-2',300,'test.retry');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.retry';
    UPDATE fzh.jobs SET lease_expires_at=now()-interval '1 second' WHERE job_id=j.job_id;
    PERFORM * FROM fzh.reap_expired_jobs('contract-reaper');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.retry';
    IF j.status <> 'dead_letter' OR j.attempt_count <> 2 OR j.terminal_at IS NULL THEN RAISE EXCEPTION 'retry exhaustion failed'; END IF;

    -- Successful side effects require durable effect intent + commit receipt.
    PERFORM fzh.submit_job('test.effect-success','1','{}'::jsonb,true,'L1','workspace_branch_write','INTERNAL',2,50,now(),'contract');
    PERFORM * FROM fzh.lease_next_job('worker-success',300,'test.effect-success');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.effect-success';
    PERFORM fzh.record_job_gate_decision(j.job_id,j.lease_token,'allow',repeat('1',64),repeat('2',64),'44444444-4444-4444-8444-444444444444'::uuid,'risk_default:L1:allow','worker-success');
    PERFORM fzh.start_job(j.job_id,j.lease_token,'worker-success');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.effect-success';
    PERFORM fzh.begin_job_effect(j.job_id,j.lease_token,'effect:success:1','{}'::jsonb,'worker-success');
    BEGIN
        PERFORM fzh.complete_job(j.job_id,j.lease_token,'{}'::jsonb,'worker-success');
        RAISE EXCEPTION 'expected uncommitted effect rejection';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM='expected uncommitted effect rejection' THEN RAISE; END IF;
    END;
    PERFORM fzh.commit_job_effect(j.job_id,j.lease_token,'effect:success:1','receipt-123','worker-success');
    PERFORM fzh.complete_job(j.job_id,j.lease_token,'{"ok":true}'::jsonb,'worker-success');
    SELECT * INTO j FROM fzh.jobs WHERE job_kind='test.effect-success';
    IF j.status <> 'succeeded' THEN RAISE EXCEPTION 'committed side effect did not succeed'; END IF;

    BEGIN
        UPDATE fzh.job_gate_decisions SET reason='tampered';
        RAISE EXCEPTION 'expected append-only gate decision rejection';
    EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
    END;

    SELECT count(*) INTO n FROM fzh.job_attempts; IF n < 7 THEN RAISE EXCEPTION 'too few attempt rows: %',n; END IF;
    SELECT count(*) INTO n FROM fzh.job_events; IF n < 15 THEN RAISE EXCEPTION 'too few event rows: %',n; END IF;
END;
$$;

SELECT 'job ledger SQL contract passed' AS result;

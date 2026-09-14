\set ON_ERROR_STOP on

TRUNCATE TABLE
    fzh.job_effects,
    fzh.job_gate_decisions,
    fzh.job_events,
    fzh.job_attempts,
    fzh.jobs
RESTART IDENTITY;

DO $$
DECLARE
    v_job1 uuid;
    v_job2 uuid;
    v_job fzh.jobs%ROWTYPE;
    v_attempt fzh.job_attempts%ROWTYPE;
    v_count integer;
    v_status text;
    v_found boolean;
BEGIN
    -- Idempotent submission returns the same stable job ID.
    SELECT (fzh.submit_job(
        'test.idempotent', 'same-key', '{"task":"one"}'::jsonb,
        false, 'L0', 'read_status', 'PUBLIC', 3, 100, now(), 'contract'
    )).job_id INTO v_job1;
    SELECT (fzh.submit_job(
        'test.idempotent', 'same-key', '{"task":"one"}'::jsonb,
        false, 'L0', 'read_status', 'PUBLIC', 3, 100, now(), 'contract'
    )).job_id INTO v_job2;
    IF v_job1 IS DISTINCT FROM v_job2 THEN
        RAISE EXCEPTION 'idempotent submission returned different job IDs';
    END IF;
    SELECT count(*) INTO v_count FROM fzh.jobs WHERE job_kind = 'test.idempotent';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'duplicate idempotent submission created % jobs', v_count;
    END IF;

    -- Reusing the key for a different immutable definition must fail loudly.
    BEGIN
        PERFORM fzh.submit_job(
            'test.idempotent', 'same-key', '{"task":"different"}'::jsonb,
            false, 'L0', 'read_status', 'PUBLIC', 3, 100, now(), 'contract'
        );
        RAISE EXCEPTION 'expected idempotency definition conflict';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;

    -- A worker/direct update cannot reset its finite retry budget.
    BEGIN
        UPDATE fzh.jobs SET max_attempts = 20 WHERE job_id = v_job1;
        RAISE EXCEPTION 'expected immutable retry budget rejection';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        NULL;
    END;

    -- Non-side-effecting work can run without a gate decision and becomes terminal.
    PERFORM fzh.submit_job(
        'test.simple', 'simple-1', '{"task":"observe"}'::jsonb,
        false, 'L0', 'read_status', 'PUBLIC', 2, 10, now(), 'contract'
    );
    PERFORM * FROM fzh.lease_next_job('worker-simple', 300, 'test.simple');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.simple' AND idempotency_key = 'simple-1';
    IF v_job.status <> 'leased' OR v_job.attempt_count <> 1 THEN
        RAISE EXCEPTION 'simple job was not leased correctly: %, attempts=%', v_job.status, v_job.attempt_count;
    END IF;
    PERFORM fzh.start_job(v_job.job_id, v_job.lease_token, 'worker-simple');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_id = v_job.job_id;
    PERFORM fzh.complete_job(v_job.job_id, v_job.lease_token, '{"ok":true}'::jsonb, 'worker-simple');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.simple' AND idempotency_key = 'simple-1';
    IF v_job.status <> 'succeeded' OR v_job.terminal_at IS NULL THEN
        RAISE EXCEPTION 'simple job did not become succeeded';
    END IF;
    PERFORM * FROM fzh.lease_next_job('worker-other', 300, 'test.simple');
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'terminal job was leased again';
    END IF;

    -- Append-only attempt/event evidence must reject modification.
    SELECT * INTO v_attempt FROM fzh.job_attempts WHERE job_id = v_job.job_id LIMIT 1;
    BEGIN
        UPDATE fzh.job_attempts SET worker_id = 'tampered' WHERE attempt_id = v_attempt.attempt_id;
        RAISE EXCEPTION 'expected append-only attempt rejection';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        NULL;
    END;
    BEGIN
        UPDATE fzh.job_events SET actor = 'tampered' WHERE job_id = v_job.job_id;
        RAISE EXCEPTION 'expected append-only event rejection';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        NULL;
    END;

    -- Approval-required is a waiting state and does not spin/retry.
    PERFORM fzh.submit_job(
        'test.approval', 'approval-1', '{"task":"deploy"}'::jsonb,
        true, 'L2', 'production_deploy', 'INTERNAL', 3, 20, now(), 'contract'
    );
    PERFORM * FROM fzh.lease_next_job('worker-approval', 300, 'test.approval');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.approval';
    PERFORM fzh.record_job_gate_decision(
        v_job.job_id, v_job.lease_token, 'approval_required', repeat('a', 64), repeat('b', 64),
        '11111111-1111-4111-8111-111111111111'::uuid, 'risk_default:L2:approval_required', 'worker-approval'
    );
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.approval';
    IF v_job.status <> 'waiting_approval' OR v_job.attempt_count <> 1 OR v_job.lease_token IS NOT NULL THEN
        RAISE EXCEPTION 'approval job state invalid: %, attempts=%', v_job.status, v_job.attempt_count;
    END IF;
    PERFORM * FROM fzh.lease_next_job('worker-other', 300, 'test.approval');
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'waiting approval job was leased';
    END IF;

    -- Gate deny is terminal and never executes.
    PERFORM fzh.submit_job(
        'test.denied', 'deny-1', '{"task":"secret-export"}'::jsonb,
        true, 'L3', 'credential_export', 'SECRET', 2, 20, now(), 'contract'
    );
    PERFORM * FROM fzh.lease_next_job('worker-denied', 300, 'test.denied');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.denied';
    PERFORM fzh.record_job_gate_decision(
        v_job.job_id, v_job.lease_token, 'deny', repeat('c', 64), repeat('d', 64),
        '22222222-2222-4222-8222-222222222222'::uuid, 'hard_deny_action:credential_export', 'worker-denied'
    );
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.denied';
    IF v_job.status <> 'denied' OR v_job.terminal_at IS NULL THEN
        RAISE EXCEPTION 'denied job did not become terminal';
    END IF;

    -- Side-effecting jobs cannot start without an Action Gate allow linked to this attempt.
    PERFORM fzh.submit_job(
        'test.effect-ambiguous', 'effect-ambiguous-1', '{"task":"external-write"}'::jsonb,
        true, 'L1', 'workspace_branch_write', 'INTERNAL', 3, 30, now(), 'contract'
    );
    PERFORM * FROM fzh.lease_next_job('worker-effect', 300, 'test.effect-ambiguous');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.effect-ambiguous';
    BEGIN
        PERFORM fzh.start_job(v_job.job_id, v_job.lease_token, 'worker-effect');
        RAISE EXCEPTION 'expected missing gate evidence rejection';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM = 'expected missing gate evidence rejection' THEN
            RAISE;
        END IF;
    END;
    PERFORM fzh.record_job_gate_decision(
        v_job.job_id, v_job.lease_token, 'allow', repeat('e', 64), repeat('f', 64),
        '33333333-3333-4333-8333-333333333333'::uuid, 'risk_default:L1:allow', 'worker-effect'
    );
    PERFORM fzh.start_job(v_job.job_id, v_job.lease_token, 'worker-effect');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.effect-ambiguous';
    PERFORM fzh.begin_job_effect(v_job.job_id, v_job.lease_token, 'effect:test:ambiguous:1', '{"target":"remote"}'::jsonb, 'worker-effect');
    UPDATE fzh.jobs SET lease_expires_at = now() - interval '1 second' WHERE job_id = v_job.job_id;
    PERFORM * FROM fzh.reap_expired_jobs('contract-reaper');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.effect-ambiguous';
    IF v_job.status <> 'reconciliation_required' THEN
        RAISE EXCEPTION 'ambiguous side effect was auto-retried instead of quarantined: %', v_job.status;
    END IF;
    PERFORM * FROM fzh.lease_next_job('worker-other', 300, 'test.effect-ambiguous');
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'reconciliation-required job was leased';
    END IF;

    -- Safe pre-effect crashes retry only within the immutable finite budget.
    PERFORM fzh.submit_job(
        'test.retry', 'retry-1', '{"task":"safe"}'::jsonb,
        false, 'L0', 'read_status', 'PUBLIC', 2, 40, now(), 'contract'
    );
    PERFORM * FROM fzh.lease_next_job('worker-retry-1', 300, 'test.retry');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.retry';
    UPDATE fzh.jobs SET lease_expires_at = now() - interval '1 second' WHERE job_id = v_job.job_id;
    PERFORM * FROM fzh.reap_expired_jobs('contract-reaper');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.retry';
    IF v_job.status <> 'retry_wait' OR v_job.attempt_count <> 1 THEN
        RAISE EXCEPTION 'first safe lease expiry did not schedule retry';
    END IF;
    PERFORM * FROM fzh.lease_next_job('worker-retry-2', 300, 'test.retry');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.retry';
    IF v_job.attempt_count <> 2 THEN
        RAISE EXCEPTION 'second attempt count incorrect';
    END IF;
    UPDATE fzh.jobs SET lease_expires_at = now() - interval '1 second' WHERE job_id = v_job.job_id;
    PERFORM * FROM fzh.reap_expired_jobs('contract-reaper');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.retry';
    IF v_job.status <> 'dead_letter' OR v_job.attempt_count <> 2 OR v_job.terminal_at IS NULL THEN
        RAISE EXCEPTION 'retry exhaustion did not dead-letter job';
    END IF;

    -- A side-effecting success requires a committed effect claim.
    PERFORM fzh.submit_job(
        'test.effect-success', 'effect-success-1', '{"task":"branch-write"}'::jsonb,
        true, 'L1', 'workspace_branch_write', 'INTERNAL', 2, 50, now(), 'contract'
    );
    PERFORM * FROM fzh.lease_next_job('worker-effect-success', 300, 'test.effect-success');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.effect-success';
    PERFORM fzh.record_job_gate_decision(
        v_job.job_id, v_job.lease_token, 'allow', repeat('1', 64), repeat('2', 64),
        '44444444-4444-4444-8444-444444444444'::uuid, 'risk_default:L1:allow', 'worker-effect-success'
    );
    PERFORM fzh.start_job(v_job.job_id, v_job.lease_token, 'worker-effect-success');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.effect-success';
    PERFORM fzh.begin_job_effect(v_job.job_id, v_job.lease_token, 'effect:test:success:1', '{}'::jsonb, 'worker-effect-success');
    BEGIN
        PERFORM fzh.complete_job(v_job.job_id, v_job.lease_token, '{"ok":true}'::jsonb, 'worker-effect-success');
        RAISE EXCEPTION 'expected uncommitted effect rejection';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM = 'expected uncommitted effect rejection' THEN
            RAISE;
        END IF;
    END;
    PERFORM fzh.commit_job_effect(v_job.job_id, v_job.lease_token, 'effect:test:success:1', 'receipt-123', 'worker-effect-success');
    PERFORM fzh.complete_job(v_job.job_id, v_job.lease_token, '{"ok":true}'::jsonb, 'worker-effect-success');
    SELECT * INTO v_job FROM fzh.jobs WHERE job_kind = 'test.effect-success';
    IF v_job.status <> 'succeeded' THEN
        RAISE EXCEPTION 'committed side-effecting job did not succeed';
    END IF;

    -- Gate evidence is append-only as well.
    BEGIN
        UPDATE fzh.job_gate_decisions SET reason = 'tampered';
        RAISE EXCEPTION 'expected append-only gate decision rejection';
    EXCEPTION WHEN SQLSTATE '55000' THEN
        NULL;
    END;

    -- Core evidence counts prove the ledger actually recorded attempts/transitions.
    SELECT count(*) INTO v_count FROM fzh.job_attempts;
    IF v_count < 7 THEN
        RAISE EXCEPTION 'expected multiple immutable attempts, found %', v_count;
    END IF;
    SELECT count(*) INTO v_count FROM fzh.job_events;
    IF v_count < 15 THEN
        RAISE EXCEPTION 'expected lifecycle events, found %', v_count;
    END IF;
END;
$$;

SELECT 'job ledger SQL contract passed' AS result;

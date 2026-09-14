ALTER TABLE fzh.jobs
    ADD COLUMN IF NOT EXISTS lease_count integer NOT NULL DEFAULT 0 CHECK (lease_count >= 0);

COMMENT ON COLUMN fzh.jobs.lease_count IS
'Monotonic lease/acquisition sequence. Unlike attempt_count it is never refunded when a lease stops for human approval.';
COMMENT ON COLUMN fzh.jobs.attempt_count IS
'Finite retry-budget consumption. A pre-execution approval_required decision refunds the current lease because no work executed.';
COMMENT ON COLUMN fzh.job_attempts.attempt_number IS
'Monotonic lease/acquisition sequence for immutable attempt evidence; it is not the retry-budget counter.';

CREATE OR REPLACE FUNCTION fzh.lease_next_job(
    p_worker_id text,
    p_lease_seconds integer DEFAULT 300,
    p_job_kind text DEFAULT NULL
)
RETURNS TABLE (
    job_id uuid,
    attempt_id bigint,
    lease_token uuid,
    job_kind text,
    payload jsonb,
    side_effecting boolean,
    risk_level text,
    action_type text,
    data_classification text,
    attempt_number integer,
    max_attempts integer,
    lease_expires_at timestamptz
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
    v_attempt_id bigint;
    v_token uuid := gen_random_uuid();
    v_expiry timestamptz;
BEGIN
    IF p_lease_seconds < 15 OR p_lease_seconds > 3600 THEN
        RAISE EXCEPTION 'lease seconds must be between 15 and 3600';
    END IF;
    IF p_worker_id IS NULL OR length(btrim(p_worker_id)) = 0 THEN
        RAISE EXCEPTION 'worker id is required';
    END IF;

    SELECT j.* INTO v_job
    FROM fzh.jobs j
    WHERE j.status IN ('queued', 'retry_wait')
      AND j.next_eligible_at <= now()
      AND j.attempt_count < j.max_attempts
      AND (p_job_kind IS NULL OR j.job_kind = p_job_kind)
    ORDER BY j.priority ASC, j.next_eligible_at ASC, j.created_at ASC, j.job_id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    v_expiry := now() + make_interval(secs => p_lease_seconds);

    UPDATE fzh.jobs j
    SET status = 'leased',
        attempt_count = j.attempt_count + 1,
        lease_count = j.lease_count + 1,
        lease_owner = p_worker_id,
        lease_token = v_token,
        lease_expires_at = v_expiry,
        updated_at = now()
    WHERE j.job_id = v_job.job_id
    RETURNING * INTO v_job;

    INSERT INTO fzh.job_attempts(job_id, attempt_number, worker_id, lease_token, initial_lease_expires_at)
    VALUES (v_job.job_id, v_job.lease_count, p_worker_id, v_token, v_expiry)
    RETURNING job_attempts.attempt_id INTO v_attempt_id;

    INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
    VALUES (
        v_job.job_id, v_attempt_id, 'leased', 'queued_or_retry_wait', 'leased', p_worker_id,
        jsonb_build_object(
            'lease_expires_at', v_expiry,
            'lease_sequence', v_job.lease_count,
            'retry_budget_consumed', v_job.attempt_count,
            'max_attempts', v_job.max_attempts
        )
    );

    RETURN QUERY SELECT
        v_job.job_id, v_attempt_id, v_token, v_job.job_kind, v_job.payload,
        v_job.side_effecting, v_job.risk_level, v_job.action_type,
        v_job.data_classification, v_job.lease_count, v_job.max_attempts, v_expiry;
END;
$$;

CREATE OR REPLACE FUNCTION fzh.record_job_gate_decision(
    p_job_id uuid,
    p_lease_token uuid,
    p_decision text,
    p_request_sha256 text,
    p_policy_sha256 text,
    p_audit_event_id uuid,
    p_reason text,
    p_actor text DEFAULT 'worker'
)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
    v_attempt_id bigint;
    v_new_status text;
BEGIN
    IF p_decision NOT IN ('allow', 'approval_required', 'deny') THEN
        RAISE EXCEPTION 'invalid gate decision';
    END IF;
    IF p_request_sha256 !~ '^[0-9a-f]{64}$' OR p_policy_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid gate hash';
    END IF;

    SELECT * INTO v_job
    FROM fzh.jobs
    WHERE job_id = p_job_id
    FOR UPDATE;

    IF NOT FOUND OR v_job.status <> 'leased' OR v_job.lease_token IS DISTINCT FROM p_lease_token
       OR v_job.lease_expires_at <= now() THEN
        RAISE EXCEPTION 'job does not hold a live leased token';
    END IF;

    SELECT attempt_id INTO v_attempt_id
    FROM fzh.job_attempts
    WHERE job_id = p_job_id AND lease_token = p_lease_token;

    INSERT INTO fzh.job_gate_decisions(
        job_id, attempt_id, decision, request_sha256, policy_sha256, audit_event_id, reason
    ) VALUES (
        p_job_id, v_attempt_id, p_decision, p_request_sha256, p_policy_sha256, p_audit_event_id, p_reason
    );

    IF p_decision = 'allow' THEN
        INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
        VALUES (p_job_id, v_attempt_id, 'gate_allowed', 'leased', 'leased', p_actor,
                jsonb_build_object('request_sha256', p_request_sha256, 'policy_sha256', p_policy_sha256, 'audit_event_id', p_audit_event_id));
        RETURN 'leased';
    END IF;

    IF p_decision = 'approval_required' THEN
        v_new_status := 'waiting_approval';
    ELSE
        v_new_status := 'denied';
    END IF;

    UPDATE fzh.jobs
    SET status = v_new_status,
        attempt_count = CASE
            WHEN v_new_status = 'waiting_approval' THEN GREATEST(attempt_count - 1, 0)
            ELSE attempt_count
        END,
        lease_owner = NULL,
        lease_token = NULL,
        lease_expires_at = NULL,
        terminal_at = CASE WHEN v_new_status = 'denied' THEN now() ELSE NULL END,
        updated_at = now()
    WHERE job_id = p_job_id;

    INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
    VALUES (
        p_job_id, v_attempt_id,
        CASE WHEN v_new_status = 'waiting_approval' THEN 'waiting_approval' ELSE 'gate_denied' END,
        'leased', v_new_status, p_actor,
        jsonb_build_object(
            'request_sha256', p_request_sha256,
            'policy_sha256', p_policy_sha256,
            'audit_event_id', p_audit_event_id,
            'reason', p_reason,
            'retry_budget_refunded', v_new_status = 'waiting_approval'
        )
    );

    RETURN v_new_status;
END;
$$;

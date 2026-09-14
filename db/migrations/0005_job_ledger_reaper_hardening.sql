CREATE OR REPLACE FUNCTION fzh.fail_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_error_code text,
    p_error_summary text,
    p_retryable boolean DEFAULT true,
    p_retry_delay_seconds integer DEFAULT 60,
    p_actor text DEFAULT 'worker'
)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
    v_attempt_id bigint;
    v_status text;
BEGIN
    SELECT j.* INTO v_job
    FROM fzh.jobs j
    WHERE j.job_id = p_job_id
    FOR UPDATE;

    IF NOT FOUND
       OR v_job.status NOT IN ('leased', 'running')
       OR v_job.lease_token IS DISTINCT FROM p_lease_token
       OR v_job.lease_expires_at <= now() THEN
        RAISE EXCEPTION 'job does not hold a live supplied lease';
    END IF;

    SELECT a.attempt_id INTO v_attempt_id
    FROM fzh.job_attempts a
    WHERE a.job_id = p_job_id AND a.lease_token = p_lease_token;

    IF EXISTS (
        SELECT 1 FROM fzh.job_effects e
        WHERE e.job_id = p_job_id AND e.attempt_id = v_attempt_id
    ) THEN
        v_status := 'reconciliation_required';
    ELSIF p_retryable AND v_job.attempt_count < v_job.max_attempts THEN
        v_status := 'retry_wait';
    ELSIF p_retryable THEN
        v_status := 'dead_letter';
    ELSE
        v_status := 'failed';
    END IF;

    UPDATE fzh.jobs j
    SET status = v_status,
        last_error_code = p_error_code,
        last_error_summary = left(p_error_summary, 2048),
        next_eligible_at = CASE
            WHEN v_status = 'retry_wait' THEN now() + make_interval(secs => GREATEST(0, p_retry_delay_seconds))
            ELSE j.next_eligible_at
        END,
        lease_owner = NULL,
        lease_token = NULL,
        lease_expires_at = NULL,
        terminal_at = CASE WHEN v_status IN ('failed', 'dead_letter') THEN now() ELSE NULL END,
        updated_at = now()
    WHERE j.job_id = p_job_id;

    INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
    VALUES (
        p_job_id, v_attempt_id, 'attempt_failed', v_job.status, v_status, p_actor,
        jsonb_build_object(
            'error_code', p_error_code,
            'error_summary', left(p_error_summary, 2048),
            'retryable', p_retryable
        )
    );
    RETURN v_status;
END;
$$;

CREATE OR REPLACE FUNCTION fzh.reap_expired_jobs(
    p_actor text DEFAULT 'lease-reaper'
)
RETURNS TABLE(job_id uuid, new_status text)
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
    v_attempt_id bigint;
    v_status text;
BEGIN
    FOR v_job IN
        SELECT j.*
        FROM fzh.jobs j
        WHERE j.status IN ('leased', 'running')
          AND j.lease_expires_at <= now()
        ORDER BY j.lease_expires_at, j.job_id
        FOR UPDATE SKIP LOCKED
    LOOP
        SELECT a.attempt_id INTO v_attempt_id
        FROM fzh.job_attempts a
        WHERE a.job_id = v_job.job_id
          AND a.lease_token = v_job.lease_token;

        IF EXISTS (
            SELECT 1 FROM fzh.job_effects e
            WHERE e.job_id = v_job.job_id
              AND e.attempt_id = v_attempt_id
        ) THEN
            v_status := 'reconciliation_required';
        ELSIF v_job.attempt_count < v_job.max_attempts THEN
            v_status := 'retry_wait';
        ELSE
            v_status := 'dead_letter';
        END IF;

        UPDATE fzh.jobs j
        SET status = v_status,
            next_eligible_at = CASE WHEN v_status = 'retry_wait' THEN now() ELSE j.next_eligible_at END,
            last_error_code = 'lease_expired',
            last_error_summary = 'worker lease expired before terminal acknowledgement',
            lease_owner = NULL,
            lease_token = NULL,
            lease_expires_at = NULL,
            terminal_at = CASE WHEN v_status = 'dead_letter' THEN now() ELSE NULL END,
            updated_at = now()
        WHERE j.job_id = v_job.job_id;

        INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
        VALUES (
            v_job.job_id, v_attempt_id, 'lease_expired', v_job.status, v_status, p_actor,
            jsonb_build_object('retry_budget_consumed', v_job.attempt_count, 'lease_sequence', v_job.lease_count)
        );

        job_id := v_job.job_id;
        new_status := v_status;
        RETURN NEXT;
    END LOOP;
END;
$$;

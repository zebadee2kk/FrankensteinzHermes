CREATE TABLE IF NOT EXISTS fzh.jobs (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_kind text NOT NULL CHECK (job_kind ~ '^[a-z0-9][a-z0-9_.-]{0,127}$'),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    side_effecting boolean NOT NULL DEFAULT false,
    risk_level text NOT NULL CHECK (risk_level IN ('L0', 'L1', 'L2', 'L3')),
    action_type text NOT NULL CHECK (length(action_type) BETWEEN 1 AND 128),
    data_classification text NOT NULL CHECK (data_classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'SECRET')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'leased', 'running', 'waiting_approval', 'retry_wait',
        'reconciliation_required', 'succeeded', 'failed', 'denied',
        'cancelled', 'dead_letter'
    )),
    priority integer NOT NULL DEFAULT 100 CHECK (priority BETWEEN 0 AND 1000),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND max_attempts),
    next_eligible_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_token uuid,
    lease_expires_at timestamptz,
    result jsonb,
    last_error_code text,
    last_error_summary text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    terminal_at timestamptz,
    UNIQUE (job_kind, idempotency_key),
    CHECK (
        (lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)
        OR
        (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    CHECK (
        (status IN ('leased', 'running') AND lease_token IS NOT NULL)
        OR
        (status NOT IN ('leased', 'running') AND lease_token IS NULL)
    ),
    CHECK (
        (status IN ('succeeded', 'failed', 'denied', 'cancelled', 'dead_letter') AND terminal_at IS NOT NULL)
        OR
        (status NOT IN ('succeeded', 'failed', 'denied', 'cancelled', 'dead_letter') AND terminal_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS jobs_ready_idx
    ON fzh.jobs(priority, next_eligible_at, created_at)
    WHERE status IN ('queued', 'retry_wait');
CREATE INDEX IF NOT EXISTS jobs_lease_expiry_idx
    ON fzh.jobs(lease_expires_at)
    WHERE status IN ('leased', 'running');

CREATE TABLE IF NOT EXISTS fzh.job_attempts (
    attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES fzh.jobs(job_id) ON DELETE RESTRICT,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    worker_id text NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 128),
    lease_token uuid NOT NULL,
    leased_at timestamptz NOT NULL DEFAULT now(),
    initial_lease_expires_at timestamptz NOT NULL,
    UNIQUE (job_id, attempt_number),
    UNIQUE (job_id, lease_token)
);

CREATE TABLE IF NOT EXISTS fzh.job_gate_decisions (
    gate_decision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES fzh.jobs(job_id) ON DELETE RESTRICT,
    attempt_id bigint NOT NULL REFERENCES fzh.job_attempts(attempt_id) ON DELETE RESTRICT,
    decision text NOT NULL CHECK (decision IN ('allow', 'approval_required', 'deny')),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    policy_sha256 text NOT NULL CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
    audit_event_id uuid NOT NULL,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 512),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (attempt_id)
);

CREATE TABLE IF NOT EXISTS fzh.job_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES fzh.jobs(job_id) ON DELETE RESTRICT,
    attempt_id bigint REFERENCES fzh.job_attempts(attempt_id) ON DELETE RESTRICT,
    event_type text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 128),
    from_status text,
    to_status text,
    actor text NOT NULL CHECK (length(actor) BETWEEN 1 AND 128),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_events_job_idx
    ON fzh.job_events(job_id, event_id);

CREATE TABLE IF NOT EXISTS fzh.job_effects (
    effect_key text PRIMARY KEY CHECK (length(effect_key) BETWEEN 1 AND 256),
    job_id uuid NOT NULL REFERENCES fzh.jobs(job_id) ON DELETE RESTRICT,
    attempt_id bigint NOT NULL REFERENCES fzh.job_attempts(attempt_id) ON DELETE RESTRICT,
    state text NOT NULL DEFAULT 'started' CHECK (state IN ('started', 'committed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    committed_at timestamptz,
    external_receipt text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (
        (state = 'started' AND committed_at IS NULL)
        OR
        (state = 'committed' AND committed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS job_effects_attempt_idx
    ON fzh.job_effects(job_id, attempt_id);

CREATE OR REPLACE FUNCTION fzh.reject_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS job_attempts_append_only ON fzh.job_attempts;
CREATE TRIGGER job_attempts_append_only
BEFORE UPDATE OR DELETE ON fzh.job_attempts
FOR EACH ROW EXECUTE FUNCTION fzh.reject_append_only_mutation();

DROP TRIGGER IF EXISTS job_gate_decisions_append_only ON fzh.job_gate_decisions;
CREATE TRIGGER job_gate_decisions_append_only
BEFORE UPDATE OR DELETE ON fzh.job_gate_decisions
FOR EACH ROW EXECUTE FUNCTION fzh.reject_append_only_mutation();

DROP TRIGGER IF EXISTS job_events_append_only ON fzh.job_events;
CREATE TRIGGER job_events_append_only
BEFORE UPDATE OR DELETE ON fzh.job_events
FOR EACH ROW EXECUTE FUNCTION fzh.reject_append_only_mutation();

CREATE OR REPLACE FUNCTION fzh.protect_job_immutable_fields()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.job_kind IS DISTINCT FROM OLD.job_kind
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.side_effecting IS DISTINCT FROM OLD.side_effecting
       OR NEW.risk_level IS DISTINCT FROM OLD.risk_level
       OR NEW.action_type IS DISTINCT FROM OLD.action_type
       OR NEW.data_classification IS DISTINCT FROM OLD.data_classification
       OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'immutable job definition fields cannot be changed' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS jobs_immutable_definition ON fzh.jobs;
CREATE TRIGGER jobs_immutable_definition
BEFORE UPDATE ON fzh.jobs
FOR EACH ROW EXECUTE FUNCTION fzh.protect_job_immutable_fields();

CREATE OR REPLACE FUNCTION fzh.protect_effect_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'job_effects are not deletable' USING ERRCODE = '55000';
    END IF;
    IF NEW.effect_key IS DISTINCT FROM OLD.effect_key
       OR NEW.job_id IS DISTINCT FROM OLD.job_id
       OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR OLD.state = 'committed'
       OR NOT (OLD.state = 'started' AND NEW.state = 'committed')
       OR NEW.committed_at IS NULL THEN
        RAISE EXCEPTION 'job_effects only permit started -> committed transition' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS job_effects_transition_guard ON fzh.job_effects;
CREATE TRIGGER job_effects_transition_guard
BEFORE UPDATE OR DELETE ON fzh.job_effects
FOR EACH ROW EXECUTE FUNCTION fzh.protect_effect_transition();

CREATE OR REPLACE FUNCTION fzh.submit_job(
    p_job_kind text,
    p_idempotency_key text,
    p_payload jsonb,
    p_side_effecting boolean,
    p_risk_level text,
    p_action_type text,
    p_data_classification text,
    p_max_attempts integer DEFAULT 3,
    p_priority integer DEFAULT 100,
    p_not_before timestamptz DEFAULT now(),
    p_actor text DEFAULT 'submitter'
)
RETURNS fzh.jobs
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
BEGIN
    INSERT INTO fzh.jobs(
        job_kind, idempotency_key, payload, side_effecting, risk_level,
        action_type, data_classification, max_attempts, priority, next_eligible_at
    )
    VALUES (
        p_job_kind, p_idempotency_key, COALESCE(p_payload, '{}'::jsonb), p_side_effecting,
        p_risk_level, p_action_type, p_data_classification, p_max_attempts,
        p_priority, p_not_before
    )
    ON CONFLICT (job_kind, idempotency_key) DO NOTHING
    RETURNING * INTO v_job;

    IF FOUND THEN
        INSERT INTO fzh.job_events(job_id, event_type, from_status, to_status, actor, details)
        VALUES (v_job.job_id, 'submitted', NULL, 'queued', p_actor, jsonb_build_object('idempotency_key', p_idempotency_key));
        RETURN v_job;
    END IF;

    SELECT * INTO v_job
    FROM fzh.jobs
    WHERE job_kind = p_job_kind AND idempotency_key = p_idempotency_key;

    IF v_job.payload IS DISTINCT FROM COALESCE(p_payload, '{}'::jsonb)
       OR v_job.side_effecting IS DISTINCT FROM p_side_effecting
       OR v_job.risk_level IS DISTINCT FROM p_risk_level
       OR v_job.action_type IS DISTINCT FROM p_action_type
       OR v_job.data_classification IS DISTINCT FROM p_data_classification
       OR v_job.max_attempts IS DISTINCT FROM p_max_attempts THEN
        RAISE EXCEPTION 'idempotency key reused with different immutable job definition'
            USING ERRCODE = '23505';
    END IF;

    RETURN v_job;
END;
$$;

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
        lease_owner = p_worker_id,
        lease_token = v_token,
        lease_expires_at = v_expiry,
        updated_at = now()
    WHERE j.job_id = v_job.job_id
    RETURNING * INTO v_job;

    INSERT INTO fzh.job_attempts(job_id, attempt_number, worker_id, lease_token, initial_lease_expires_at)
    VALUES (v_job.job_id, v_job.attempt_count, p_worker_id, v_token, v_expiry)
    RETURNING job_attempts.attempt_id INTO v_attempt_id;

    INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
    VALUES (
        v_job.job_id, v_attempt_id, 'leased', 'queued_or_retry_wait', 'leased', p_worker_id,
        jsonb_build_object('lease_expires_at', v_expiry, 'attempt_number', v_job.attempt_count)
    );

    RETURN QUERY SELECT
        v_job.job_id, v_attempt_id, v_token, v_job.job_kind, v_job.payload,
        v_job.side_effecting, v_job.risk_level, v_job.action_type,
        v_job.data_classification, v_job.attempt_count, v_job.max_attempts, v_expiry;
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
        v_new_status := 'leased';
        INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
        VALUES (p_job_id, v_attempt_id, 'gate_allowed', 'leased', 'leased', p_actor,
                jsonb_build_object('request_sha256', p_request_sha256, 'policy_sha256', p_policy_sha256, 'audit_event_id', p_audit_event_id));
        RETURN v_new_status;
    END IF;

    IF p_decision = 'approval_required' THEN
        v_new_status := 'waiting_approval';
    ELSE
        v_new_status := 'denied';
    END IF;

    UPDATE fzh.jobs
    SET status = v_new_status,
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
        jsonb_build_object('request_sha256', p_request_sha256, 'policy_sha256', p_policy_sha256, 'audit_event_id', p_audit_event_id, 'reason', p_reason)
    );

    RETURN v_new_status;
END;
$$;

CREATE OR REPLACE FUNCTION fzh.start_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_actor text DEFAULT 'worker'
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
    v_attempt_id bigint;
BEGIN
    SELECT * INTO v_job FROM fzh.jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND OR v_job.status <> 'leased' OR v_job.lease_token IS DISTINCT FROM p_lease_token
       OR v_job.lease_expires_at <= now() THEN
        RAISE EXCEPTION 'job does not hold a live leased token';
    END IF;

    SELECT attempt_id INTO v_attempt_id
    FROM fzh.job_attempts
    WHERE job_id = p_job_id AND lease_token = p_lease_token;

    IF v_job.side_effecting AND NOT EXISTS (
        SELECT 1 FROM fzh.job_gate_decisions g
        WHERE g.attempt_id = v_attempt_id AND g.decision = 'allow'
    ) THEN
        RAISE EXCEPTION 'side-effecting job cannot start without Action Gate allow evidence';
    END IF;

    UPDATE fzh.jobs SET status = 'running', updated_at = now() WHERE job_id = p_job_id;
    INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor)
    VALUES (p_job_id, v_attempt_id, 'started', 'leased', 'running', p_actor);
    RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION fzh.heartbeat_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_lease_seconds integer DEFAULT 300
)
RETURNS timestamptz
LANGUAGE plpgsql
AS $$
DECLARE
    v_expiry timestamptz;
BEGIN
    IF p_lease_seconds < 15 OR p_lease_seconds > 3600 THEN
        RAISE EXCEPTION 'lease seconds must be between 15 and 3600';
    END IF;
    v_expiry := now() + make_interval(secs => p_lease_seconds);
    UPDATE fzh.jobs
    SET lease_expires_at = v_expiry, updated_at = now()
    WHERE job_id = p_job_id
      AND lease_token = p_lease_token
      AND status IN ('leased', 'running')
      AND lease_expires_at > now();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'cannot heartbeat non-live lease';
    END IF;
    RETURN v_expiry;
END;
$$;

CREATE OR REPLACE FUNCTION fzh.begin_job_effect(
    p_job_id uuid,
    p_lease_token uuid,
    p_effect_key text,
    p_details jsonb DEFAULT '{}'::jsonb,
    p_actor text DEFAULT 'worker'
)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
    v_attempt_id bigint;
    v_effect fzh.job_effects%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM fzh.jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND OR v_job.status <> 'running' OR v_job.lease_token IS DISTINCT FROM p_lease_token
       OR v_job.lease_expires_at <= now() THEN
        RAISE EXCEPTION 'job does not hold a live running lease';
    END IF;
    IF NOT v_job.side_effecting THEN
        RAISE EXCEPTION 'effect tracking is only valid for side-effecting jobs';
    END IF;

    SELECT attempt_id INTO v_attempt_id
    FROM fzh.job_attempts WHERE job_id = p_job_id AND lease_token = p_lease_token;

    IF NOT EXISTS (
        SELECT 1 FROM fzh.job_gate_decisions g
        WHERE g.attempt_id = v_attempt_id AND g.decision = 'allow'
    ) THEN
        RAISE EXCEPTION 'effect cannot begin without Action Gate allow evidence';
    END IF;

    INSERT INTO fzh.job_effects(effect_key, job_id, attempt_id, details)
    VALUES (p_effect_key, p_job_id, v_attempt_id, COALESCE(p_details, '{}'::jsonb))
    ON CONFLICT (effect_key) DO NOTHING
    RETURNING * INTO v_effect;

    IF FOUND THEN
        INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
        VALUES (p_job_id, v_attempt_id, 'effect_started', 'running', 'running', p_actor,
                jsonb_build_object('effect_key', p_effect_key));
        RETURN v_effect.state;
    END IF;

    SELECT * INTO v_effect FROM fzh.job_effects WHERE effect_key = p_effect_key;
    IF v_effect.job_id IS DISTINCT FROM p_job_id OR v_effect.attempt_id IS DISTINCT FROM v_attempt_id THEN
        RAISE EXCEPTION 'effect idempotency key already belongs to another job/attempt' USING ERRCODE = '23505';
    END IF;
    RETURN v_effect.state;
END;
$$;

CREATE OR REPLACE FUNCTION fzh.commit_job_effect(
    p_job_id uuid,
    p_lease_token uuid,
    p_effect_key text,
    p_external_receipt text DEFAULT NULL,
    p_actor text DEFAULT 'worker'
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_attempt_id bigint;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM fzh.jobs
        WHERE job_id = p_job_id AND status = 'running' AND lease_token = p_lease_token AND lease_expires_at > now()
    ) THEN
        RAISE EXCEPTION 'job does not hold a live running lease';
    END IF;
    SELECT attempt_id INTO v_attempt_id
    FROM fzh.job_attempts WHERE job_id = p_job_id AND lease_token = p_lease_token;

    UPDATE fzh.job_effects
    SET state = 'committed', committed_at = now(), external_receipt = p_external_receipt
    WHERE effect_key = p_effect_key
      AND job_id = p_job_id
      AND attempt_id = v_attempt_id
      AND state = 'started';

    IF NOT FOUND THEN
        IF EXISTS (
            SELECT 1 FROM fzh.job_effects
            WHERE effect_key = p_effect_key AND job_id = p_job_id AND attempt_id = v_attempt_id AND state = 'committed'
        ) THEN
            RETURN true;
        END IF;
        RAISE EXCEPTION 'effect claim not found for current attempt';
    END IF;

    INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
    VALUES (p_job_id, v_attempt_id, 'effect_committed', 'running', 'running', p_actor,
            jsonb_build_object('effect_key', p_effect_key, 'external_receipt', p_external_receipt));
    RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION fzh.complete_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_result jsonb DEFAULT '{}'::jsonb,
    p_actor text DEFAULT 'worker'
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
    v_attempt_id bigint;
BEGIN
    SELECT * INTO v_job FROM fzh.jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND OR v_job.status <> 'running' OR v_job.lease_token IS DISTINCT FROM p_lease_token
       OR v_job.lease_expires_at <= now() THEN
        RAISE EXCEPTION 'job does not hold a live running lease';
    END IF;
    SELECT attempt_id INTO v_attempt_id
    FROM fzh.job_attempts WHERE job_id = p_job_id AND lease_token = p_lease_token;

    IF v_job.side_effecting THEN
        IF NOT EXISTS (SELECT 1 FROM fzh.job_effects WHERE job_id = p_job_id AND attempt_id = v_attempt_id) THEN
            RAISE EXCEPTION 'side-effecting job cannot complete without an effect idempotency record';
        END IF;
        IF EXISTS (SELECT 1 FROM fzh.job_effects WHERE job_id = p_job_id AND attempt_id = v_attempt_id AND state <> 'committed') THEN
            RAISE EXCEPTION 'side-effecting job has uncommitted effect records';
        END IF;
    END IF;

    UPDATE fzh.jobs
    SET status = 'succeeded', result = COALESCE(p_result, '{}'::jsonb),
        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
        terminal_at = now(), updated_at = now()
    WHERE job_id = p_job_id;

    INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
    VALUES (p_job_id, v_attempt_id, 'succeeded', 'running', 'succeeded', p_actor,
            jsonb_build_object('result', COALESCE(p_result, '{}'::jsonb)));
    RETURN true;
END;
$$;

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
    SELECT * INTO v_job FROM fzh.jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND OR v_job.status NOT IN ('leased', 'running') OR v_job.lease_token IS DISTINCT FROM p_lease_token THEN
        RAISE EXCEPTION 'job does not hold the supplied active lease';
    END IF;
    SELECT attempt_id INTO v_attempt_id
    FROM fzh.job_attempts WHERE job_id = p_job_id AND lease_token = p_lease_token;

    IF EXISTS (SELECT 1 FROM fzh.job_effects WHERE job_id = p_job_id AND attempt_id = v_attempt_id) THEN
        v_status := 'reconciliation_required';
    ELSIF p_retryable AND v_job.attempt_count < v_job.max_attempts THEN
        v_status := 'retry_wait';
    ELSIF p_retryable THEN
        v_status := 'dead_letter';
    ELSE
        v_status := 'failed';
    END IF;

    UPDATE fzh.jobs
    SET status = v_status,
        last_error_code = p_error_code,
        last_error_summary = left(p_error_summary, 2048),
        next_eligible_at = CASE WHEN v_status = 'retry_wait' THEN now() + make_interval(secs => GREATEST(0, p_retry_delay_seconds)) ELSE next_eligible_at END,
        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
        terminal_at = CASE WHEN v_status IN ('failed', 'dead_letter') THEN now() ELSE NULL END,
        updated_at = now()
    WHERE job_id = p_job_id;

    INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
    VALUES (p_job_id, v_attempt_id, 'attempt_failed', v_job.status, v_status, p_actor,
            jsonb_build_object('error_code', p_error_code, 'error_summary', left(p_error_summary, 2048), 'retryable', p_retryable));
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
        SELECT * FROM fzh.jobs
        WHERE status IN ('leased', 'running') AND lease_expires_at <= now()
        ORDER BY lease_expires_at, job_id
        FOR UPDATE SKIP LOCKED
    LOOP
        SELECT attempt_id INTO v_attempt_id
        FROM fzh.job_attempts
        WHERE job_id = v_job.job_id AND lease_token = v_job.lease_token;

        IF EXISTS (SELECT 1 FROM fzh.job_effects WHERE job_id = v_job.job_id AND attempt_id = v_attempt_id) THEN
            v_status := 'reconciliation_required';
        ELSIF v_job.attempt_count < v_job.max_attempts THEN
            v_status := 'retry_wait';
        ELSE
            v_status := 'dead_letter';
        END IF;

        UPDATE fzh.jobs
        SET status = v_status,
            next_eligible_at = CASE WHEN v_status = 'retry_wait' THEN now() ELSE next_eligible_at END,
            last_error_code = 'lease_expired',
            last_error_summary = 'worker lease expired before terminal acknowledgement',
            lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
            terminal_at = CASE WHEN v_status = 'dead_letter' THEN now() ELSE NULL END,
            updated_at = now()
        WHERE fzh.jobs.job_id = v_job.job_id;

        INSERT INTO fzh.job_events(job_id, attempt_id, event_type, from_status, to_status, actor, details)
        VALUES (v_job.job_id, v_attempt_id, 'lease_expired', v_job.status, v_status, p_actor,
                jsonb_build_object('attempt_number', v_job.attempt_count));

        job_id := v_job.job_id;
        new_status := v_status;
        RETURN NEXT;
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION fzh.cancel_job(
    p_job_id uuid,
    p_reason text,
    p_actor text DEFAULT 'operator'
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_job fzh.jobs%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM fzh.jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'job not found';
    END IF;
    IF v_job.status NOT IN ('queued', 'retry_wait', 'waiting_approval', 'reconciliation_required') THEN
        RAISE EXCEPTION 'job in status % cannot be cancelled by safe cancel path', v_job.status;
    END IF;
    UPDATE fzh.jobs
    SET status = 'cancelled', terminal_at = now(), updated_at = now(),
        last_error_code = 'cancelled', last_error_summary = left(p_reason, 2048)
    WHERE job_id = p_job_id;
    INSERT INTO fzh.job_events(job_id, event_type, from_status, to_status, actor, details)
    VALUES (p_job_id, 'cancelled', v_job.status, 'cancelled', p_actor, jsonb_build_object('reason', left(p_reason, 2048)));
    RETURN true;
END;
$$;

COMMENT ON TABLE fzh.jobs IS
'Canonical autonomous job state. Immutable definition fields and finite retry budget are protected by trigger.';
COMMENT ON TABLE fzh.job_attempts IS
'Append-only lease/attempt evidence. One row per acquired attempt.';
COMMENT ON TABLE fzh.job_gate_decisions IS
'Append-only Action Gate admission evidence linked to a specific attempt.';
COMMENT ON TABLE fzh.job_events IS
'Append-only job lifecycle evidence. Mutable operational state remains in fzh.jobs.';
COMMENT ON TABLE fzh.job_effects IS
'Durable side-effect idempotency claims. An expired attempt with any effect claim is quarantined for reconciliation instead of auto-retry.';

INSERT INTO fzh.system_metadata (key, value)
VALUES ('job_ledger', '{"schema_version":1,"lease_model":"recoverable","effect_ambiguity":"reconciliation_required","retry_budget":"immutable"}'::jsonb)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = now();

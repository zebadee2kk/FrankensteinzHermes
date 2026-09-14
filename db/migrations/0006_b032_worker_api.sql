DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fzh_b032_worker') THEN
        CREATE ROLE fzh_b032_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA fzh TO fzh_b032_worker;

CREATE OR REPLACE FUNCTION fzh.b032_lease_job(
    p_worker_id text,
    p_lease_seconds integer DEFAULT 900
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
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT * FROM fzh.lease_next_job(p_worker_id, p_lease_seconds, 'engineering.implement');
$$;

CREATE OR REPLACE FUNCTION fzh.b032_record_gate(
    p_job_id uuid,
    p_lease_token uuid,
    p_decision text,
    p_request_sha256 text,
    p_policy_sha256 text,
    p_audit_event_id uuid,
    p_reason text
)
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT fzh.record_job_gate_decision(
        p_job_id, p_lease_token, p_decision, p_request_sha256,
        p_policy_sha256, p_audit_event_id, p_reason, 'b032-engineering-worker'
    );
$$;

CREATE OR REPLACE FUNCTION fzh.b032_start_job(p_job_id uuid, p_lease_token uuid)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT fzh.start_job(p_job_id, p_lease_token, 'b032-engineering-worker');
$$;

CREATE OR REPLACE FUNCTION fzh.b032_heartbeat(p_job_id uuid, p_lease_token uuid, p_lease_seconds integer DEFAULT 900)
RETURNS timestamptz
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT fzh.heartbeat_job(p_job_id, p_lease_token, p_lease_seconds);
$$;

CREATE OR REPLACE FUNCTION fzh.b032_begin_candidate_effect(p_job_id uuid, p_lease_token uuid)
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT fzh.begin_job_effect(
        p_job_id,
        p_lease_token,
        'b032:candidate-commit:' || p_job_id::text,
        jsonb_build_object('kind', 'local_candidate_commit'),
        'b032-engineering-worker'
    );
$$;

CREATE OR REPLACE FUNCTION fzh.b032_commit_candidate_effect(
    p_job_id uuid,
    p_lease_token uuid,
    p_head_commit text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    IF p_head_commit !~ '^[0-9a-f]{40}$' THEN
        RAISE EXCEPTION 'invalid candidate commit SHA';
    END IF;
    RETURN fzh.commit_job_effect(
        p_job_id,
        p_lease_token,
        'b032:candidate-commit:' || p_job_id::text,
        p_head_commit,
        'b032-engineering-worker'
    );
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b032_complete_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_result jsonb
)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT fzh.complete_job(p_job_id, p_lease_token, COALESCE(p_result, '{}'::jsonb), 'b032-engineering-worker');
$$;

CREATE OR REPLACE FUNCTION fzh.b032_fail_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_error_code text,
    p_error_summary text,
    p_retryable boolean DEFAULT true,
    p_retry_delay_seconds integer DEFAULT 60
)
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT fzh.fail_job(
        p_job_id, p_lease_token, left(p_error_code, 128), left(p_error_summary, 2048),
        p_retryable, p_retry_delay_seconds, 'b032-engineering-worker'
    );
$$;

REVOKE ALL ON FUNCTION fzh.b032_lease_job(text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b032_record_gate(uuid, uuid, text, text, text, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b032_start_job(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b032_heartbeat(uuid, uuid, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b032_begin_candidate_effect(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b032_commit_candidate_effect(uuid, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b032_complete_job(uuid, uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b032_fail_job(uuid, uuid, text, text, boolean, integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION fzh.b032_lease_job(text, integer) TO fzh_b032_worker;
GRANT EXECUTE ON FUNCTION fzh.b032_record_gate(uuid, uuid, text, text, text, uuid, text) TO fzh_b032_worker;
GRANT EXECUTE ON FUNCTION fzh.b032_start_job(uuid, uuid) TO fzh_b032_worker;
GRANT EXECUTE ON FUNCTION fzh.b032_heartbeat(uuid, uuid, integer) TO fzh_b032_worker;
GRANT EXECUTE ON FUNCTION fzh.b032_begin_candidate_effect(uuid, uuid) TO fzh_b032_worker;
GRANT EXECUTE ON FUNCTION fzh.b032_commit_candidate_effect(uuid, uuid, text) TO fzh_b032_worker;
GRANT EXECUTE ON FUNCTION fzh.b032_complete_job(uuid, uuid, jsonb) TO fzh_b032_worker;
GRANT EXECUTE ON FUNCTION fzh.b032_fail_job(uuid, uuid, text, text, boolean, integer) TO fzh_b032_worker;

-- The worker role receives no direct table privileges. All mutations are through
-- the fixed SECURITY DEFINER surface above.
REVOKE ALL ON ALL TABLES IN SCHEMA fzh FROM fzh_b032_worker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA fzh FROM fzh_b032_worker;

INSERT INTO fzh.system_metadata(key, value)
VALUES ('b032_worker_api', '{"schema_version":1,"role":"fzh_b032_worker","direct_table_access":false,"job_kind":"engineering.implement"}'::jsonb)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

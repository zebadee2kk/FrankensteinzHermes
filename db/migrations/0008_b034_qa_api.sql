DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fzh_b034_qa') THEN
        CREATE ROLE fzh_b034_qa NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA fzh TO fzh_b034_qa;

CREATE OR REPLACE FUNCTION fzh._b034_assert_qa_job(p_job_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
DECLARE v_kind text;
BEGIN
    SELECT job_kind INTO v_kind FROM fzh.jobs WHERE job_id = p_job_id;
    IF v_kind IS DISTINCT FROM 'engineering.qa' THEN
        RAISE EXCEPTION 'B034 capability requires engineering.qa job';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION fzh._b034_assert_qa_job(uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION fzh.b034_lease_job(p_worker_id text, p_lease_seconds integer DEFAULT 900)
RETURNS TABLE (
    job_id uuid, attempt_id bigint, lease_token uuid, job_kind text, payload jsonb,
    side_effecting boolean, risk_level text, action_type text, data_classification text,
    attempt_number integer, max_attempts integer, lease_expires_at timestamptz
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT * FROM fzh.lease_next_job(p_worker_id, p_lease_seconds, 'engineering.qa');
$$;

CREATE OR REPLACE FUNCTION fzh.b034_record_gate(
    p_job_id uuid, p_lease_token uuid, p_decision text, p_request_sha256 text,
    p_policy_sha256 text, p_audit_event_id uuid, p_reason text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b034_assert_qa_job(p_job_id);
    RETURN fzh.record_job_gate_decision(
        p_job_id, p_lease_token, p_decision, p_request_sha256, p_policy_sha256,
        p_audit_event_id, p_reason, 'b034-adversarial-qa'
    );
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b034_start_job(p_job_id uuid, p_lease_token uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b034_assert_qa_job(p_job_id);
    RETURN fzh.start_job(p_job_id, p_lease_token, 'b034-adversarial-qa');
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b034_heartbeat(p_job_id uuid, p_lease_token uuid, p_lease_seconds integer DEFAULT 900)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b034_assert_qa_job(p_job_id);
    RETURN fzh.heartbeat_job(p_job_id, p_lease_token, p_lease_seconds);
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b034_complete_job(p_job_id uuid, p_lease_token uuid, p_result jsonb)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
DECLARE v_status text;
BEGIN
    PERFORM fzh._b034_assert_qa_job(p_job_id);
    v_status := p_result->>'qa_status';
    IF v_status NOT IN ('qa_passed', 'qa_failed', 'reject') THEN
        RAISE EXCEPTION 'invalid B034 QA status';
    END IF;
    IF COALESCE((p_result->>'promotion_authorized')::boolean, false) <> false THEN
        RAISE EXCEPTION 'B034 cannot authorize promotion';
    END IF;
    RETURN fzh.complete_job(p_job_id, p_lease_token, COALESCE(p_result, '{}'::jsonb), 'b034-adversarial-qa');
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b034_fail_job(
    p_job_id uuid, p_lease_token uuid, p_error_code text, p_error_summary text,
    p_retryable boolean DEFAULT true, p_retry_delay_seconds integer DEFAULT 60
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b034_assert_qa_job(p_job_id);
    RETURN fzh.fail_job(
        p_job_id, p_lease_token, left(p_error_code,128), left(p_error_summary,2048),
        p_retryable, p_retry_delay_seconds, 'b034-adversarial-qa'
    );
END;
$$;

REVOKE ALL ON FUNCTION fzh.b034_lease_job(text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b034_record_gate(uuid,uuid,text,text,text,uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b034_start_job(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b034_heartbeat(uuid,uuid,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b034_complete_job(uuid,uuid,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b034_fail_job(uuid,uuid,text,text,boolean,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION fzh.b034_lease_job(text,integer) TO fzh_b034_qa;
GRANT EXECUTE ON FUNCTION fzh.b034_record_gate(uuid,uuid,text,text,text,uuid,text) TO fzh_b034_qa;
GRANT EXECUTE ON FUNCTION fzh.b034_start_job(uuid,uuid) TO fzh_b034_qa;
GRANT EXECUTE ON FUNCTION fzh.b034_heartbeat(uuid,uuid,integer) TO fzh_b034_qa;
GRANT EXECUTE ON FUNCTION fzh.b034_complete_job(uuid,uuid,jsonb) TO fzh_b034_qa;
GRANT EXECUTE ON FUNCTION fzh.b034_fail_job(uuid,uuid,text,text,boolean,integer) TO fzh_b034_qa;

REVOKE ALL ON ALL TABLES IN SCHEMA fzh FROM fzh_b034_qa;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA fzh FROM fzh_b034_qa;

INSERT INTO fzh.system_metadata(key, value)
VALUES ('b034_qa_api', '{"schema_version":1,"role":"fzh_b034_qa","direct_table_access":false,"generic_function_access":false,"candidate_ref_mutation":false,"job_kind":"engineering.qa"}'::jsonb)
ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now();

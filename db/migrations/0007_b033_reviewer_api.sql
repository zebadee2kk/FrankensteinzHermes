DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fzh_b033_reviewer') THEN
        CREATE ROLE fzh_b033_reviewer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA fzh TO fzh_b033_reviewer;

CREATE OR REPLACE FUNCTION fzh._b033_assert_review_job(p_job_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
DECLARE
    v_kind text;
BEGIN
    SELECT job_kind INTO v_kind FROM fzh.jobs WHERE job_id = p_job_id;
    IF v_kind IS DISTINCT FROM 'engineering.review' THEN
        RAISE EXCEPTION 'B033 capability requires engineering.review job';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION fzh._b033_assert_review_job(uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION fzh.b033_lease_job(
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
    SELECT * FROM fzh.lease_next_job(p_worker_id, p_lease_seconds, 'engineering.review');
$$;

CREATE OR REPLACE FUNCTION fzh.b033_start_job(p_job_id uuid, p_lease_token uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b033_assert_review_job(p_job_id);
    RETURN fzh.start_job(p_job_id, p_lease_token, 'b033-independent-reviewer');
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b033_heartbeat(p_job_id uuid, p_lease_token uuid, p_lease_seconds integer DEFAULT 900)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b033_assert_review_job(p_job_id);
    RETURN fzh.heartbeat_job(p_job_id, p_lease_token, p_lease_seconds);
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b033_complete_job(p_job_id uuid, p_lease_token uuid, p_result jsonb)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
DECLARE
    v_verdict text;
BEGIN
    PERFORM fzh._b033_assert_review_job(p_job_id);
    v_verdict := p_result->>'verdict';
    IF v_verdict NOT IN ('approve', 'changes_required', 'reject') THEN
        RAISE EXCEPTION 'invalid B033 verdict';
    END IF;
    IF COALESCE((p_result->>'promotion_authorized')::boolean, false) <> false THEN
        RAISE EXCEPTION 'B033 cannot authorize promotion';
    END IF;
    RETURN fzh.complete_job(p_job_id, p_lease_token, COALESCE(p_result, '{}'::jsonb), 'b033-independent-reviewer');
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b033_fail_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_error_code text,
    p_error_summary text,
    p_retryable boolean DEFAULT true,
    p_retry_delay_seconds integer DEFAULT 60
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b033_assert_review_job(p_job_id);
    RETURN fzh.fail_job(
        p_job_id, p_lease_token, left(p_error_code, 128), left(p_error_summary, 2048),
        p_retryable, p_retry_delay_seconds, 'b033-independent-reviewer'
    );
END;
$$;

REVOKE ALL ON FUNCTION fzh.b033_lease_job(text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b033_start_job(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b033_heartbeat(uuid, uuid, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b033_complete_job(uuid, uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b033_fail_job(uuid, uuid, text, text, boolean, integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION fzh.b033_lease_job(text, integer) TO fzh_b033_reviewer;
GRANT EXECUTE ON FUNCTION fzh.b033_start_job(uuid, uuid) TO fzh_b033_reviewer;
GRANT EXECUTE ON FUNCTION fzh.b033_heartbeat(uuid, uuid, integer) TO fzh_b033_reviewer;
GRANT EXECUTE ON FUNCTION fzh.b033_complete_job(uuid, uuid, jsonb) TO fzh_b033_reviewer;
GRANT EXECUTE ON FUNCTION fzh.b033_fail_job(uuid, uuid, text, text, boolean, integer) TO fzh_b033_reviewer;

REVOKE ALL ON ALL TABLES IN SCHEMA fzh FROM fzh_b033_reviewer;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA fzh FROM fzh_b033_reviewer;

INSERT INTO fzh.system_metadata(key, value)
VALUES (
    'b033_reviewer_api',
    '{"schema_version":1,"role":"fzh_b033_reviewer","direct_table_access":false,"generic_function_access":false,"code_mutation":false,"job_kind":"engineering.review"}'::jsonb
)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

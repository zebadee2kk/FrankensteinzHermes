DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fzh_b035_security') THEN
        CREATE ROLE fzh_b035_security NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA fzh TO fzh_b035_security;

CREATE OR REPLACE FUNCTION fzh._b035_assert_security_job(p_job_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
DECLARE
    v_kind text;
    v_side_effecting boolean;
BEGIN
    SELECT job_kind, side_effecting INTO v_kind, v_side_effecting
    FROM fzh.jobs WHERE job_id = p_job_id;
    IF v_kind IS DISTINCT FROM 'engineering.security_review' THEN
        RAISE EXCEPTION 'B035 capability requires engineering.security_review job';
    END IF;
    IF v_side_effecting IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'B035 security review must be non-side-effecting';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION fzh._b035_assert_security_job(uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION fzh.b035_lease_job(p_worker_id text, p_lease_seconds integer DEFAULT 900)
RETURNS TABLE (
    job_id uuid, attempt_id bigint, lease_token uuid, job_kind text, payload jsonb,
    side_effecting boolean, risk_level text, action_type text, data_classification text,
    attempt_number integer, max_attempts integer, lease_expires_at timestamptz
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
    SELECT * FROM fzh.lease_next_job(p_worker_id, p_lease_seconds, 'engineering.security_review');
$$;

CREATE OR REPLACE FUNCTION fzh.b035_start_job(p_job_id uuid, p_lease_token uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b035_assert_security_job(p_job_id);
    RETURN fzh.start_job(p_job_id, p_lease_token, 'b035-security-reviewer');
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b035_heartbeat(p_job_id uuid, p_lease_token uuid, p_lease_seconds integer DEFAULT 900)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b035_assert_security_job(p_job_id);
    RETURN fzh.heartbeat_job(p_job_id, p_lease_token, p_lease_seconds);
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b035_complete_job(p_job_id uuid, p_lease_token uuid, p_result jsonb)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
DECLARE
    v_status text;
BEGIN
    PERFORM fzh._b035_assert_security_job(p_job_id);
    v_status := p_result->>'security_status';
    IF v_status NOT IN ('security_passed', 'security_failed', 'reject') THEN
        RAISE EXCEPTION 'invalid B035 security status';
    END IF;
    IF COALESCE((p_result->>'promotion_authorized')::boolean, false) <> false THEN
        RAISE EXCEPTION 'B035 cannot authorize promotion';
    END IF;
    RETURN fzh.complete_job(p_job_id, p_lease_token, COALESCE(p_result, '{}'::jsonb), 'b035-security-reviewer');
END;
$$;

CREATE OR REPLACE FUNCTION fzh.b035_fail_job(
    p_job_id uuid, p_lease_token uuid, p_error_code text, p_error_summary text,
    p_retryable boolean DEFAULT true, p_retry_delay_seconds integer DEFAULT 60
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, fzh
AS $$
BEGIN
    PERFORM fzh._b035_assert_security_job(p_job_id);
    RETURN fzh.fail_job(
        p_job_id, p_lease_token, left(p_error_code,128), left(p_error_summary,2048),
        p_retryable, p_retry_delay_seconds, 'b035-security-reviewer'
    );
END;
$$;

REVOKE ALL ON FUNCTION fzh.b035_lease_job(text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b035_start_job(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b035_heartbeat(uuid,uuid,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b035_complete_job(uuid,uuid,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION fzh.b035_fail_job(uuid,uuid,text,text,boolean,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION fzh.b035_lease_job(text,integer) TO fzh_b035_security;
GRANT EXECUTE ON FUNCTION fzh.b035_start_job(uuid,uuid) TO fzh_b035_security;
GRANT EXECUTE ON FUNCTION fzh.b035_heartbeat(uuid,uuid,integer) TO fzh_b035_security;
GRANT EXECUTE ON FUNCTION fzh.b035_complete_job(uuid,uuid,jsonb) TO fzh_b035_security;
GRANT EXECUTE ON FUNCTION fzh.b035_fail_job(uuid,uuid,text,text,boolean,integer) TO fzh_b035_security;

REVOKE ALL ON ALL TABLES IN SCHEMA fzh FROM fzh_b035_security;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA fzh FROM fzh_b035_security;

INSERT INTO fzh.system_metadata(key, value)
VALUES ('b035_security_api', '{"schema_version":1,"role":"fzh_b035_security","direct_table_access":false,"generic_function_access":false,"candidate_mutation":false,"job_kind":"engineering.security_review"}'::jsonb)
ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now();

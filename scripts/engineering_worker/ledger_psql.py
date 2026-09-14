#!/usr/bin/env python3
"""B032 adapter for the constrained fzh_b032_worker PostgreSQL API.

The login identity is provisioned out-of-band and granted membership in the
NOLOGIN `fzh_b032_worker` capability role. This process never issues raw table
DML; every operation maps to one SECURITY DEFINER function from migration 0006.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_INPUT = 128 * 1024
MAX_OUTPUT = 128 * 1024


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def validate_uuid(value: object, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc


def validate_request(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("request must be object")
    op = value.get("operation")
    allowed = {"record_gate", "start", "heartbeat", "begin_effect", "commit_effect", "complete", "fail"}
    if op not in allowed:
        raise ValueError("unsupported operation")
    validate_uuid(value.get("job_id"), "job_id")
    validate_uuid(value.get("lease_token"), "lease_token")
    if op == "record_gate":
        if value.get("decision") not in ("allow", "approval_required", "deny"):
            raise ValueError("invalid decision")
        for key in ("request_sha256", "policy_sha256"):
            if not isinstance(value.get(key), str) or not HEX64.fullmatch(value[key]):
                raise ValueError(f"invalid {key}")
        validate_uuid(value.get("audit_event_id"), "audit_event_id")
        if not isinstance(value.get("reason"), str) or not value["reason"] or len(value["reason"]) > 512:
            raise ValueError("invalid reason")
    elif op == "commit_effect":
        if not isinstance(value.get("head_commit"), str) or not HEX40.fullmatch(value["head_commit"]):
            raise ValueError("invalid head_commit")
    elif op == "complete":
        if not isinstance(value.get("result"), dict):
            raise ValueError("complete result must be object")
    elif op == "fail":
        if not isinstance(value.get("error_code"), str) or not value["error_code"] or len(value["error_code"]) > 128:
            raise ValueError("invalid error_code")
        if not isinstance(value.get("error_summary"), str) or len(value["error_summary"]) > 2048:
            raise ValueError("invalid error_summary")
        if not isinstance(value.get("retryable"), bool):
            raise ValueError("retryable must be boolean")
        delay = value.get("retry_delay_seconds", 60)
        if not isinstance(delay, int) or not 0 <= delay <= 3600:
            raise ValueError("invalid retry_delay_seconds")
    elif op == "heartbeat":
        seconds = value.get("lease_seconds", 900)
        if not isinstance(seconds, int) or not 15 <= seconds <= 3600:
            raise ValueError("invalid lease_seconds")
    return value


def sql_for(operation: str) -> str:
    common = "WITH r AS (SELECT :'request'::jsonb AS j) "
    expressions = {
        "record_gate": "SELECT json_build_object('status', fzh.b032_record_gate((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->>'decision',j->>'request_sha256',j->>'policy_sha256',(j->>'audit_event_id')::uuid,j->>'reason')) FROM r;",
        "start": "SELECT json_build_object('status', CASE WHEN fzh.b032_start_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid) THEN 'running' ELSE 'error' END) FROM r;",
        "heartbeat": "SELECT json_build_object('lease_expires_at', fzh.b032_heartbeat((j->>'job_id')::uuid,(j->>'lease_token')::uuid,COALESCE((j->>'lease_seconds')::integer,900))) FROM r;",
        "begin_effect": "SELECT json_build_object('state', fzh.b032_begin_candidate_effect((j->>'job_id')::uuid,(j->>'lease_token')::uuid)) FROM r;",
        "commit_effect": "SELECT json_build_object('committed', fzh.b032_commit_candidate_effect((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->>'head_commit')) FROM r;",
        "complete": "SELECT json_build_object('status', CASE WHEN fzh.b032_complete_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->'result') THEN 'succeeded' ELSE 'error' END) FROM r;",
        "fail": "SELECT json_build_object('status', fzh.b032_fail_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->>'error_code',j->>'error_summary',COALESCE((j->>'retryable')::boolean,true),COALESCE((j->>'retry_delay_seconds')::integer,60))) FROM r;",
    }
    return "SET ROLE fzh_b032_worker;\n" + common + expressions[operation] + "\n"


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return fail("ledger request too large")
    try:
        request = validate_request(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        return fail(f"invalid ledger request: {exc}")

    env = os.environ.copy()
    env.setdefault("PGCONNECT_TIMEOUT", "5")
    cmd = [
        "psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--quiet",
        "--no-align", "--tuples-only", "--set", "request=" + json.dumps(request, sort_keys=True, separators=(",", ":")),
    ]
    try:
        cp = subprocess.run(cmd, input=sql_for(request["operation"]).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=10, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return fail(f"ledger database unavailable: {exc.__class__.__name__}")
    if cp.returncode != 0:
        return fail("ledger operation failed: " + cp.stderr.decode("utf-8", errors="replace")[-1000:])
    if len(cp.stdout) > MAX_OUTPUT:
        return fail("ledger response too large")
    line = cp.stdout.decode("utf-8", errors="strict").strip().splitlines()
    if len(line) != 1:
        return fail("unexpected ledger response")
    try:
        response = json.loads(line[0])
    except json.JSONDecodeError:
        return fail("ledger response is not JSON")
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

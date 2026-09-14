#!/usr/bin/env python3
"""Adapter for the constrained fzh_b035_security PostgreSQL capability API."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

MAX_INPUT = 128 * 1024
MAX_OUTPUT = 128 * 1024


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def valid_uuid(value: object, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc


def validate(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("request must be object")
    op = value.get("operation")
    if op not in {"start", "heartbeat", "complete", "fail"}:
        raise ValueError("unsupported operation")
    valid_uuid(value.get("job_id"), "job_id")
    valid_uuid(value.get("lease_token"), "lease_token")
    if op == "heartbeat":
        seconds = value.get("lease_seconds", 900)
        if not isinstance(seconds, int) or not 15 <= seconds <= 3600:
            raise ValueError("invalid lease_seconds")
    elif op == "complete":
        result = value.get("result")
        if not isinstance(result, dict) or result.get("security_status") not in {"security_passed", "security_failed", "reject"}:
            raise ValueError("invalid security completion result")
        if result.get("promotion_authorized") not in (None, False):
            raise ValueError("B035 cannot authorize promotion")
    elif op == "fail":
        if not isinstance(value.get("error_code"), str) or not value["error_code"] or len(value["error_code"]) > 128:
            raise ValueError("invalid error_code")
        if not isinstance(value.get("error_summary"), str) or len(value["error_summary"]) > 2048:
            raise ValueError("invalid error_summary")
        if not isinstance(value.get("retryable"), bool):
            raise ValueError("retryable must be boolean")
        delay = value.get("retry_delay_seconds", 60)
        if not isinstance(delay, int) or not 0 <= delay <= 3600:
            raise ValueError("invalid retry delay")
    return value


def sql_for(op: str) -> str:
    common = "WITH r AS (SELECT :'request'::jsonb AS j) "
    expressions = {
        "start": "SELECT json_build_object('status', CASE WHEN fzh.b035_start_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid) THEN 'running' ELSE 'error' END) FROM r;",
        "heartbeat": "SELECT json_build_object('lease_expires_at', fzh.b035_heartbeat((j->>'job_id')::uuid,(j->>'lease_token')::uuid,COALESCE((j->>'lease_seconds')::integer,900))) FROM r;",
        "complete": "SELECT json_build_object('status', CASE WHEN fzh.b035_complete_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->'result') THEN 'succeeded' ELSE 'error' END) FROM r;",
        "fail": "SELECT json_build_object('status', fzh.b035_fail_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->>'error_code',j->>'error_summary',COALESCE((j->>'retryable')::boolean,true),COALESCE((j->>'retry_delay_seconds')::integer,60))) FROM r;",
    }
    return "SET ROLE fzh_b035_security;\n" + common + expressions[op] + "\n"


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return fail("security ledger request too large")
    try:
        request = validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        return fail(f"invalid security ledger request: {exc}")
    env = os.environ.copy()
    env.setdefault("PGCONNECT_TIMEOUT", "5")
    cmd = ["psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--quiet", "--no-align", "--tuples-only",
           "--set", "request=" + json.dumps(request, sort_keys=True, separators=(",", ":"))]
    try:
        cp = subprocess.run(cmd, input=sql_for(request["operation"]).encode(), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, timeout=10, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return fail(f"security ledger database unavailable: {exc.__class__.__name__}")
    if cp.returncode != 0:
        return fail("security ledger operation failed: " + cp.stderr.decode("utf-8", errors="replace")[-1000:])
    if len(cp.stdout) > MAX_OUTPUT:
        return fail("security ledger response too large")
    lines = cp.stdout.decode("utf-8", errors="strict").strip().splitlines()
    if len(lines) != 1:
        return fail("unexpected security ledger response")
    try:
        response = json.loads(lines[0])
    except json.JSONDecodeError:
        return fail("security ledger response is not JSON")
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

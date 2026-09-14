#!/usr/bin/env python3
"""Adapter for the constrained fzh_b034_qa PostgreSQL API."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_PROFILE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
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
    if op not in {"record_gate", "start", "heartbeat", "begin_effect", "commit_effect", "complete", "fail"}:
        raise ValueError("unsupported operation")
    valid_uuid(value.get("job_id"), "job_id")
    valid_uuid(value.get("lease_token"), "lease_token")
    if op == "record_gate":
        if value.get("decision") not in {"allow", "approval_required", "deny"}:
            raise ValueError("invalid gate decision")
        for key in ("request_sha256", "policy_sha256"):
            if not isinstance(value.get(key), str) or not HEX64.fullmatch(value[key]):
                raise ValueError(f"invalid {key}")
        valid_uuid(value.get("audit_event_id"), "audit_event_id")
        if not isinstance(value.get("reason"), str) or not value["reason"] or len(value["reason"]) > 512:
            raise ValueError("invalid gate reason")
    elif op == "heartbeat":
        seconds = value.get("lease_seconds", 900)
        if not isinstance(seconds, int) or not 15 <= seconds <= 3600:
            raise ValueError("invalid lease_seconds")
    elif op in {"begin_effect", "commit_effect"}:
        profile_id = value.get("profile_id")
        if not isinstance(profile_id, str) or not SAFE_PROFILE.fullmatch(profile_id):
            raise ValueError("invalid profile_id")
        if op == "commit_effect":
            digest = value.get("result_sha256")
            if not isinstance(digest, str) or not HEX64.fullmatch(digest):
                raise ValueError("invalid result_sha256")
    elif op == "complete":
        result = value.get("result")
        if not isinstance(result, dict) or result.get("qa_status") not in {"qa_passed", "qa_failed", "reject"}:
            raise ValueError("invalid QA completion result")
        if result.get("promotion_authorized") not in (None, False):
            raise ValueError("B034 cannot authorize promotion")
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
        "record_gate": "SELECT json_build_object('status', fzh.b034_record_gate((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->>'decision',j->>'request_sha256',j->>'policy_sha256',(j->>'audit_event_id')::uuid,j->>'reason')) FROM r;",
        "start": "SELECT json_build_object('status', CASE WHEN fzh.b034_start_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid) THEN 'running' ELSE 'error' END) FROM r;",
        "heartbeat": "SELECT json_build_object('lease_expires_at', fzh.b034_heartbeat((j->>'job_id')::uuid,(j->>'lease_token')::uuid,COALESCE((j->>'lease_seconds')::integer,900))) FROM r;",
        "begin_effect": "SELECT json_build_object('state', fzh.b034_begin_profile_effect((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->>'profile_id')) FROM r;",
        "commit_effect": "SELECT json_build_object('committed', fzh.b034_commit_profile_effect((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->>'profile_id',j->>'result_sha256')) FROM r;",
        "complete": "SELECT json_build_object('status', CASE WHEN fzh.b034_complete_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->'result') THEN 'succeeded' ELSE 'error' END) FROM r;",
        "fail": "SELECT json_build_object('status', fzh.b034_fail_job((j->>'job_id')::uuid,(j->>'lease_token')::uuid,j->>'error_code',j->>'error_summary',COALESCE((j->>'retryable')::boolean,true),COALESCE((j->>'retry_delay_seconds')::integer,60))) FROM r;",
    }
    return "SET ROLE fzh_b034_qa;\n" + common + expressions[op] + "\n"


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return fail("QA ledger request too large")
    try:
        request = validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        return fail(f"invalid QA ledger request: {exc}")
    env = os.environ.copy()
    env.setdefault("PGCONNECT_TIMEOUT", "5")
    cmd = ["psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--quiet", "--no-align", "--tuples-only",
           "--set", "request=" + json.dumps(request, sort_keys=True, separators=(",", ":"))]
    try:
        cp = subprocess.run(cmd, input=sql_for(request["operation"]).encode(), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, timeout=10, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return fail(f"QA ledger database unavailable: {exc.__class__.__name__}")
    if cp.returncode != 0:
        return fail("QA ledger operation failed: " + cp.stderr.decode("utf-8", errors="replace")[-1000:])
    if len(cp.stdout) > MAX_OUTPUT:
        return fail("QA ledger response too large")
    lines = cp.stdout.decode("utf-8", errors="strict").strip().splitlines()
    if len(lines) != 1:
        return fail("unexpected QA ledger response")
    try:
        response = json.loads(lines[0])
    except json.JSONDecodeError:
        return fail("QA ledger response is not JSON")
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

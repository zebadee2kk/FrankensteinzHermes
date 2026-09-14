#!/usr/bin/env python3
"""B034 coordinator entrypoint adding durable B031 effects around sandbox execution.

The implementation core remains in coordinator_core.py.  This entrypoint wraps
only the side-effect boundary: each selected QA profile must begin a B031 effect
immediately before untrusted candidate code enters the sandbox and commit that
effect with a digest of the sandbox result before the job can complete.
"""
from __future__ import annotations

import sys

import coordinator_core as core

_original_call_ledger = core.call_ledger
_original_call_sandbox = core.call_sandbox
_context: dict[str, object] = {}


def tracked_call_ledger(config, request, cwd, started, wall):
    if request.get("job_id") and request.get("lease_token"):
        _context.update(
            config=config,
            job_id=request["job_id"],
            lease_token=request["lease_token"],
            cwd=cwd,
        )
    return _original_call_ledger(config, request, cwd, started, wall)


def durable_call_sandbox(config, job_id, workspace, profile_id, source, started, wall):
    if _context.get("job_id") != job_id or not _context.get("lease_token"):
        raise core.QAError("qa_effect_context_missing")

    begin = _original_call_ledger(
        _context["config"],
        {
            "operation": "begin_effect",
            "job_id": _context["job_id"],
            "lease_token": _context["lease_token"],
            "profile_id": profile_id,
        },
        _context["cwd"],
        started,
        wall,
    )
    if begin.get("state") != "started":
        raise core.QAError("qa_effect_begin_failed")

    # If this call raises or the process dies after it returns but before the
    # effect commit, B031 sees an uncommitted durable effect and can quarantine
    # the attempt instead of silently replaying hostile candidate code.
    result = _original_call_sandbox(config, job_id, workspace, profile_id, source, started, wall)
    result_sha256 = core.sha256_bytes(core.canonical(result))

    committed = _original_call_ledger(
        _context["config"],
        {
            "operation": "commit_effect",
            "job_id": _context["job_id"],
            "lease_token": _context["lease_token"],
            "profile_id": profile_id,
            "result_sha256": result_sha256,
        },
        _context["cwd"],
        started,
        wall,
    )
    if committed.get("committed") is not True:
        raise core.QAError("qa_effect_commit_failed")
    return result


core.call_ledger = tracked_call_ledger
core.call_sandbox = durable_call_sandbox


if __name__ == "__main__":
    try:
        raise SystemExit(core.main())
    except core.QAError as exc:
        print(f"B034 QA: {exc}", file=sys.stderr)
        raise SystemExit(2)

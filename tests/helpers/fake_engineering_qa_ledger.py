#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    log = pathlib.Path(sys.argv[1])
    try:
        request = json.load(sys.stdin)
    except Exception:
        return 2
    if not isinstance(request, dict) or request.get("operation") not in {"record_gate", "start", "heartbeat", "complete", "fail"}:
        return 2
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n")
    op = request["operation"]
    if op == "record_gate":
        decision = request.get("decision")
        response = {"status": {"allow": "leased", "approval_required": "waiting_approval", "deny": "denied"}.get(decision, "error")}
    elif op == "start":
        response = {"status": "running"}
    elif op == "heartbeat":
        response = {"lease_expires_at": "2099-01-01T00:00:00+00:00"}
    elif op == "complete":
        response = {"status": "succeeded"}
    else:
        response = {"status": "retry_wait" if request.get("retryable", True) else "failed"}
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

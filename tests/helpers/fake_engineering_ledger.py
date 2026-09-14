#!/usr/bin/env python3
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(2)
log_path = Path(sys.argv[1])
request = json.load(sys.stdin)
operation = request.get("operation")
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(request, sort_keys=True) + "\n")
responses = {
    "record_gate": {"status": request.get("decision") == "allow" and "leased" or ("waiting_approval" if request.get("decision") == "approval_required" else "denied")},
    "start": {"status": "running"},
    "heartbeat": {"lease_expires_at": "2099-01-01T00:00:00+00:00"},
    "begin_effect": {"state": "started"},
    "commit_effect": {"committed": True},
    "complete": {"status": "succeeded"},
    "fail": {"status": "reconciliation_required" if request.get("effect_started") else "retry_wait"},
}
if operation not in responses:
    print(json.dumps({"error": "unsupported operation"}))
    raise SystemExit(2)
print(json.dumps(responses[operation], sort_keys=True))

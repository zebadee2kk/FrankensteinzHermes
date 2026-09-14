#!/usr/bin/env python3
import hashlib
import json
import sys

request = json.load(sys.stdin)
raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
print(json.dumps({
    "schema_version": 1,
    "request_id": request.get("request_id"),
    "decision": "allow",
    "reason": "risk_default:L1:allow",
    "request_sha256": hashlib.sha256(raw).hexdigest(),
    "policy_sha256": "a" * 64,
    "event_id": "11111111-1111-4111-8111-111111111111",
    "observed_at_utc": "2026-09-14T00:00:00+00:00"
}, sort_keys=True))

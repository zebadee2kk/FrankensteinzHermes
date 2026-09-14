#!/usr/bin/env python3
import json
import sys
_ = json.load(sys.stdin)
print(json.dumps({
    "schema_version": 1,
    "decision": "deny",
    "reason": "ci_forced_deny",
    "request_sha256": "b" * 64,
    "policy_sha256": "a" * 64,
    "event_id": "22222222-2222-4222-8222-222222222222",
    "observed_at_utc": "2026-09-14T00:00:00+00:00"
}, sort_keys=True))
raise SystemExit(30)

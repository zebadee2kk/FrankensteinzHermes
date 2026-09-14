#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
import time

ALLOWED_KEYS = {"operation", "job_id", "workspace", "profile_id"}


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    scenario_path = pathlib.Path(sys.argv[1])
    request_log = pathlib.Path(sys.argv[2])
    try:
        request = json.load(sys.stdin)
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    except Exception:
        return 2
    if not isinstance(request, dict) or set(request) != ALLOWED_KEYS or request.get("operation") != "run_profile":
        return 2
    workspace = pathlib.Path(str(request.get("workspace", "")))
    profile_id = request.get("profile_id")
    if not workspace.is_dir() or not isinstance(profile_id, str):
        return 2
    request_log.parent.mkdir(parents=True, exist_ok=True)
    with request_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n")
    entry = scenario.get(profile_id, "pass") if isinstance(scenario, dict) else "pass"
    if entry == "infra_error":
        print("simulated sandbox infrastructure failure", file=sys.stderr)
        return 3
    status = entry if entry in {"pass", "fail", "timeout", "output_exhausted"} else "pass"
    result = {
        "schema_version": 1,
        "profile_id": profile_id,
        "status": status,
        "returncode": 0 if status == "pass" else 1,
        "duration_seconds": 0.01,
        "stdout": f"fake {profile_id} {status}\n",
        "stderr": "",
        "network": "unshared",
        "workspace_mode": "tmpfs-copy-from-readonly-candidate",
        "resource_policy": {
            "memory_max": "64M", "tasks_max": 16, "cpu_quota": "50%",
            "timeout_seconds": 5, "max_output_bytes": 65536,
        },
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

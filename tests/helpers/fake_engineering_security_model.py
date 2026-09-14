#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    response_path = pathlib.Path(sys.argv[1])
    call_log = pathlib.Path(sys.argv[2])
    try:
        prompt = json.load(sys.stdin)
    except Exception:
        return 2
    call_log.parent.mkdir(parents=True, exist_ok=True)
    with call_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"changed_paths": prompt.get("changed_paths", []), "security_job_id": prompt.get("security_job_id")}, sort_keys=True) + "\n")
    if not response_path.is_file():
        return 3
    raw = response_path.read_text(encoding="utf-8")
    if raw.startswith("RAW:"):
        sys.stdout.write(raw[4:])
        return 0
    try:
        review = json.loads(raw)
    except json.JSONDecodeError:
        return 4
    print(json.dumps({"review": review, "model": "fake-security-model", "usage": {"completion_tokens": 64}}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
        selection = json.loads(response_path.read_text(encoding="utf-8"))
    except Exception:
        return 2
    call_log.parent.mkdir(parents=True, exist_ok=True)
    with call_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"qa_job_id": prompt.get("qa_job_id"), "allowed_profile_ids": prompt.get("allowed_profile_ids")}, sort_keys=True) + "\n")
    print(json.dumps({
        "selection": selection,
        "model": "ci/fake-qa-planner",
        "usage": {"prompt_tokens": 120, "completion_tokens": 40},
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

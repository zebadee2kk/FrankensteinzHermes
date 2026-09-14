#!/usr/bin/env python3
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(2)
_ = json.load(sys.stdin)
patch = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"model": "ci/fake-model", "patch": patch, "usage": {"prompt_tokens": 10, "completion_tokens": 20}}, sort_keys=True))

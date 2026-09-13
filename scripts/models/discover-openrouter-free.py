#!/usr/bin/env python3
"""List currently zero-token-cost OpenRouter text/chat models.

This is discovery input for benchmarking/registry work, not an automatic
production routing mutation. The stable production alias remains fzh-free-auto.
"""

from __future__ import annotations

import json
import sys
import urllib.request

URL = "https://openrouter.ai/api/v1/models"


def is_zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def main() -> int:
    request = urllib.request.Request(
        URL,
        headers={"User-Agent": "FrankensteinzHermes/free-model-discovery"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)

    rows = []
    for model in payload.get("data", []):
        pricing = model.get("pricing") or {}
        if not (is_zero(pricing.get("prompt")) and is_zero(pricing.get("completion"))):
            continue
        rows.append(
            {
                "id": model.get("id"),
                "name": model.get("name"),
                "context_length": model.get("context_length"),
                "supported_parameters": model.get("supported_parameters") or [],
            }
        )

    rows.sort(key=lambda item: item.get("id") or "")
    json.dump(
        {
            "source": URL,
            "count": len(rows),
            "models": rows,
            "note": "Discovery only; do not auto-promote models without evaluation and policy checks.",
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

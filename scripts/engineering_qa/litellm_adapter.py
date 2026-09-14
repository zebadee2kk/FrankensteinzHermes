#!/usr/bin/env python3
"""B034 QA model adapter: proposes only allowlisted profile IDs/hypotheses."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MAX_INPUT_BYTES = 512 * 1024
MAX_HTTP_BYTES = 512 * 1024


def fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def extract(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("QA selection must be object")
    return value


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return fail("QA prompt too large")
    try:
        prompt = json.loads(raw)
    except json.JSONDecodeError:
        return fail("invalid QA prompt JSON")
    base = os.environ.get("FZH_LITELLM_BASE_URL", "http://127.0.0.1:4000").rstrip("/")
    key = os.environ.get("FZH_LITELLM_API_KEY")
    alias = os.environ.get("FZH_MODEL_ALIAS", "fzh-free-auto")
    try:
        max_tokens = int(os.environ.get("FZH_MODEL_MAX_OUTPUT_TOKENS", "2048"))
    except ValueError:
        return fail("invalid QA output-token budget")
    if not key:
        return fail("FZH_LITELLM_API_KEY is required")
    if not 128 <= max_tokens <= 4096:
        return fail("QA output-token budget outside supported range")
    system = (
        "You are an adversarial QA planner with no execution tools. Return only JSON with schema_version=1, summary, and profiles. "
        "Each profile item contains only id and hypothesis. Choose IDs exclusively from allowed_profile_ids. Never output commands, shell, images, paths, environment variables, network settings, or a pass/fail verdict."
    )
    body = json.dumps({
        "model": alias, "temperature": 0, "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(prompt, sort_keys=True)},
        ],
    }, separators=(",", ":")).encode()
    request = urllib.request.Request(
        f"{base}/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=110) as response:
            data = response.read(MAX_HTTP_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        return fail(f"LiteLLM QA request failed: {exc.__class__.__name__}", 3)
    if len(data) > MAX_HTTP_BYTES:
        return fail("LiteLLM QA response too large")
    try:
        payload = json.loads(data)
        selection = extract(payload["choices"][0]["message"]["content"])
        result = {"selection": selection, "model": payload.get("model", alias)}
        if isinstance(payload.get("usage"), dict):
            result["usage"] = payload["usage"]
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return fail(f"invalid LiteLLM/QA response: {exc}")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

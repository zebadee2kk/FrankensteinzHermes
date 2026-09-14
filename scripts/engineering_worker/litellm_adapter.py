#!/usr/bin/env python3
"""Minimal OpenAI-compatible adapter for the B032 worker.

Reads one JSON prompt from stdin and writes one JSON object containing a unified
`patch`. Provider credentials remain behind LiteLLM; this adapter receives only
the local LiteLLM client credential.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MAX_INPUT_BYTES = 512 * 1024
MAX_HTTP_BYTES = 1024 * 1024


def fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    value = json.loads(text)
    if not isinstance(value, dict) or not isinstance(value.get("patch"), str):
        raise ValueError("model content must be a JSON object with string patch")
    return value


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return fail("prompt exceeds adapter input limit")
    try:
        prompt = json.loads(raw)
    except json.JSONDecodeError:
        return fail("invalid prompt JSON")

    base = os.environ.get("FZH_LITELLM_BASE_URL", "http://127.0.0.1:4000").rstrip("/")
    key = os.environ.get("FZH_LITELLM_API_KEY")
    alias = os.environ.get("FZH_MODEL_ALIAS", "fzh-free-auto")
    max_tokens = int(os.environ.get("FZH_MODEL_MAX_OUTPUT_TOKENS", "4096"))
    if not key:
        return fail("FZH_LITELLM_API_KEY is required")
    if not (128 <= max_tokens <= 8192):
        return fail("output-token budget outside supported range")

    body = json.dumps({
        "model": alias,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": "You are a bounded code implementation worker. Return only a JSON object with one string field named patch. The patch must be a standard unified git diff and must obey the supplied path constraints. Do not include markdown fences."
            },
            {"role": "user", "content": json.dumps(prompt, sort_keys=True)},
        ],
    }, separators=(",", ":")).encode()

    request = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=110) as response:
            data = response.read(MAX_HTTP_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        return fail(f"LiteLLM request failed: {exc.__class__.__name__}", 3)
    if len(data) > MAX_HTTP_BYTES:
        return fail("LiteLLM response exceeds adapter limit")

    try:
        payload = json.loads(data)
        choice = payload["choices"][0]["message"]["content"]
        result = extract_json(choice)
        result["model"] = payload.get("model", alias)
        usage = payload.get("usage")
        if isinstance(usage, dict):
            result["usage"] = usage
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return fail(f"invalid LiteLLM/model response: {exc}")

    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

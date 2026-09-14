#!/usr/bin/env python3
"""OpenAI-compatible LiteLLM adapter for B035 structured security findings."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MAX_INPUT = 1024 * 1024
MAX_HTTP = 1024 * 1024


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
    if not isinstance(value, dict):
        raise ValueError("review must be a JSON object")
    return value


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return fail("security prompt exceeds adapter input limit")
    try:
        prompt = json.loads(raw)
    except json.JSONDecodeError:
        return fail("invalid security prompt JSON")

    base = os.environ.get("FZH_LITELLM_BASE_URL", "http://127.0.0.1:4000").rstrip("/")
    key = os.environ.get("FZH_LITELLM_API_KEY")
    alias = os.environ.get("FZH_MODEL_ALIAS", "fzh-free-auto")
    max_tokens = int(os.environ.get("FZH_MODEL_MAX_OUTPUT_TOKENS", "2048"))
    if not key:
        return fail("FZH_LITELLM_API_KEY is required")
    if not 128 <= max_tokens <= 4096:
        return fail("security output-token budget outside supported range")

    schema_instruction = (
        "Return only JSON with schema_version=1, non-empty summary, and findings array. "
        "Every finding must contain exactly severity, category, path, line, rationale, evidence, remediation. "
        "Severity is critical/high/medium/low/info. Do not output commands, tools, a verdict, approval, merge, release or deployment instructions."
    )
    body = json.dumps({
        "model": alias,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": "You are an independent bounded code-security reviewer. " + schema_instruction},
            {"role": "user", "content": json.dumps(prompt, sort_keys=True)},
        ],
    }, separators=(",", ":")).encode()
    request = urllib.request.Request(
        f"{base}/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=110) as response:
            data = response.read(MAX_HTTP + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        return fail(f"LiteLLM security request failed: {exc.__class__.__name__}", 3)
    if len(data) > MAX_HTTP:
        return fail("LiteLLM security response exceeds adapter limit")
    try:
        payload = json.loads(data)
        content = payload["choices"][0]["message"]["content"]
        review = extract_json(content)
        result = {"review": review, "model": payload.get("model", alias)}
        if isinstance(payload.get("usage"), dict):
            result["usage"] = payload["usage"]
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return fail(f"invalid LiteLLM/security-model response: {exc}")
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

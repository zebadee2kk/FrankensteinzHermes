#!/usr/bin/env python3
"""OpenAI-compatible B033 reviewer adapter.

The model receives review-only context and must return structured findings. It
has no tools and no mutation capability. Provider credentials remain behind the
local LiteLLM gateway.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MAX_INPUT_BYTES = 2_500_000
MAX_HTTP_BYTES = 1_000_000


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
        raise ValueError("review must be JSON object")
    return value


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return fail("review prompt exceeds adapter input limit")
    try:
        prompt = json.loads(raw)
    except json.JSONDecodeError:
        return fail("invalid review prompt JSON")

    base = os.environ.get("FZH_LITELLM_BASE_URL", "http://127.0.0.1:4000").rstrip("/")
    key = os.environ.get("FZH_LITELLM_API_KEY")
    alias = os.environ.get("FZH_MODEL_ALIAS", "fzh-free-auto")
    try:
        max_tokens = int(os.environ.get("FZH_MODEL_MAX_OUTPUT_TOKENS", "4096"))
    except ValueError:
        return fail("invalid output-token budget")
    if not key:
        return fail("FZH_LITELLM_API_KEY is required")
    if not 128 <= max_tokens <= 8192:
        return fail("output-token budget outside supported range")

    system = (
        "You are an independent code reviewer. You did not implement the change and must not assume the implementation worker was correct. "
        "You have no tools and may not request code mutation. Return only JSON with schema_version=1, a concise summary, and findings. "
        "Each finding must contain severity (info|low|medium|high|critical), category (correctness|regression_risk|task_compliance|maintainability|scope_expansion|missing_tests|concurrency_state|error_handling|evidence_consistency|security_obvious), rationale, remediation, and optional path/line. "
        "Only reference a path from changed_paths. Do not include a verdict; deterministic policy derives it. Do not use markdown fences."
    )
    body = json.dumps({
        "model": alias,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(prompt, sort_keys=True)},
        ],
    }, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=110) as response:
            data = response.read(MAX_HTTP_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        return fail(f"LiteLLM review request failed: {exc.__class__.__name__}", 3)
    if len(data) > MAX_HTTP_BYTES:
        return fail("LiteLLM review response exceeds adapter limit")
    try:
        payload = json.loads(data)
        content = payload["choices"][0]["message"]["content"]
        review = extract_json(content)
        result = {"review": review, "model": payload.get("model", alias)}
        if isinstance(payload.get("usage"), dict):
            result["usage"] = payload["usage"]
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return fail(f"invalid LiteLLM/reviewer response: {exc}")
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

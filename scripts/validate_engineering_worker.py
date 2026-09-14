#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
worker = (ROOT / "scripts/engineering_worker/worker.py").read_text(encoding="utf-8")
adapter = (ROOT / "scripts/engineering_worker/litellm_adapter.py").read_text(encoding="utf-8")
config = (ROOT / "config/engineering-worker.example.json").read_text(encoding="utf-8")
tests = ROOT / "tests/test_engineering_worker.py"
runbook = ROOT / "docs/operations/ENGINEERING-WORKER.md"

for fragment in (
    'action_type": "workspace_branch_write"',
    'f"fzh/job-{short}"',
    'production_branch_denied',
    'source_detached_head_denied',
    'source_repo_dirty',
    'path_not_allowed',
    'symlink_patch_denied',
    'submodule_patch_denied',
    'tool_budget_exhausted',
    'wall_clock_budget_exhausted',
    'model_call_budget_exhausted',
    'promotion_authorized": False',
    'next_stage": "B033 independent reviewer"',
    'cleanup_worktree',
):
    if fragment not in worker:
        raise SystemExit(f"B032 worker missing invariant: {fragment}")

for fragment in (
    'FZH_MODEL_MAX_OUTPUT_TOKENS',
    'max_tokens',
    'http://127.0.0.1:4000',
    '/v1/chat/completions',
):
    if fragment not in adapter:
        raise SystemExit(f"LiteLLM adapter missing invariant: {fragment}")

for fragment in (
    '"max_calls": 2',
    '"max_wall_seconds": 600',
    '"max_tool_commands": 24',
    '"max_output_tokens": 4096',
    '".github/"',
    '"policy/"',
    '"scripts/action_gate/"',
):
    if fragment not in config:
        raise SystemExit(f"B032 example configuration missing invariant: {fragment}")

if not tests.exists() or not runbook.exists():
    raise SystemExit("B032 tests and runbook are required")

print("engineering worker static invariants passed")

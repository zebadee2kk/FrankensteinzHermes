#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
reviewer = (ROOT / "scripts/engineering_reviewer/reviewer.py").read_text(encoding="utf-8")
adapter = (ROOT / "scripts/engineering_reviewer/litellm_adapter.py").read_text(encoding="utf-8")
ledger = (ROOT / "scripts/engineering_reviewer/ledger_psql.py").read_text(encoding="utf-8")
migration = (ROOT / "db/migrations/0007_b033_reviewer_api.sql").read_text(encoding="utf-8")
config = (ROOT / "config/engineering-reviewer.example.json").read_text(encoding="utf-8")

for fragment in (
    'READ_ONLY_GIT = {"rev-parse", "merge-base", "rev-list", "diff", "show", "cat-file"}',
    "implementation_manifest_anchor_mismatch",
    "implementation_evidence_anchor_mismatch",
    "candidate_not_single_commit_on_base",
    "candidate_branch_head_mismatch",
    "changed_paths_evidence_mismatch",
    "review_scope_violation",
    '"promotion_authorized": False',
    '"B034 adversarial QA"',
    "derive_verdict",
    "review_model_schema_invalid",
):
    if fragment not in reviewer:
        raise SystemExit(f"reviewer missing invariant: {fragment}")

for forbidden in (
    '["git", "checkout"', '["git", "switch"', '["git", "commit"',
    '["git", "branch"', '["git", "push"', 'merge_pull_request', 'docker ', 'sudo ',
):
    if forbidden in reviewer:
        raise SystemExit(f"reviewer contains forbidden mutation surface: {forbidden}")

for fragment in (
    "fzh_b033_reviewer", "NOLOGIN", "SECURITY DEFINER",
    "B033 capability requires engineering.review job",
    "REVOKE ALL ON ALL TABLES IN SCHEMA fzh FROM fzh_b033_reviewer",
    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA fzh FROM fzh_b033_reviewer",
    "B033 cannot authorize promotion",
):
    if fragment not in migration:
        raise SystemExit(f"B033 migration missing boundary: {fragment}")

for fragment in ("fzh-free-auto", "max_prompt_bytes", "max_output_tokens", "max_git_commands", "max_diff_bytes"):
    if fragment not in config:
        raise SystemExit(f"reviewer config missing budget/routing invariant: {fragment}")

if "You are an independent code reviewer" not in adapter or "Do not include a verdict" not in adapter:
    raise SystemExit("reviewer adapter must preserve independent findings-only model boundary")
if "SET ROLE fzh_b033_reviewer" not in ledger:
    raise SystemExit("review ledger adapter must assume constrained reviewer role")

print("engineering reviewer static invariants passed")

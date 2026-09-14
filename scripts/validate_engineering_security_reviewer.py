#!/usr/bin/env python3
"""Static authority invariants for the B035 security reviewer."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
worker = (ROOT / "scripts/engineering_security_reviewer/worker.py").read_text(encoding="utf-8")
ledger = (ROOT / "scripts/engineering_security_reviewer/ledger_psql.py").read_text(encoding="utf-8")
model = (ROOT / "scripts/engineering_security_reviewer/litellm_adapter.py").read_text(encoding="utf-8")
migration = (ROOT / "db/migrations/0009_b035_security_reviewer_api.sql").read_text(encoding="utf-8")
config = json.loads((ROOT / "config/engineering-security-review.example.json").read_text(encoding="utf-8"))

for token in (
    'job_kind") != "engineering.security_review"',
    'qa_evidence_sha256', 'qa_manifest_sha256', 'qa_not_passed',
    'candidate_executed": False', 'promotion_authorized": False',
    'B036 release', 'security_failed', 'security_passed',
    'private-key material', 'hard-coded credential', 'pull_request_target',
    'Candidate introduces or changes a symbolic link',
    'Candidate introduces or changes a Git submodule entry',
):
    assert token in worker, f"missing B035 security invariant: {token}"

assert 'READ_ONLY_GIT = {"rev-parse", "rev-list", "diff", "ls-tree", "show", "status"}' in worker
for forbidden in (
    'git push', 'git merge', 'git reset', 'git switch', 'git checkout',
    'git commit', 'gh pr merge', 'docker run', 'podman run', 'subprocess.Popen',
):
    assert forbidden not in worker, f"forbidden B035 mutation/execution path: {forbidden}"

# Model has findings-only authority and no tool/verdict schema.
assert 'Do not output commands, tools, a verdict, approval, merge, release or deployment instructions.' in model
assert 'set(raw) != {"severity", "category", "path", "line", "rationale", "evidence", "remediation"}' in worker
assert 'derive_status(' in worker
assert 'security_status' in worker

# B035 database role is explicitly non-side-effecting and stage constrained.
for token in (
    'fzh_b035_security', 'engineering.security_review',
    'B035 security review must be non-side-effecting',
    'REVOKE ALL ON ALL TABLES IN SCHEMA fzh FROM fzh_b035_security',
    'REVOKE ALL ON ALL SEQUENCES IN SCHEMA fzh FROM fzh_b035_security',
    'B035 cannot authorize promotion',
):
    assert token in migration, f"missing B035 DB boundary: {token}"
assert 'begin_effect' not in ledger and 'commit_effect' not in ledger
assert 'SET ROLE fzh_b035_security' in ledger

assert config["schema_version"] == 1
assert config["policy"]["medium_failure_threshold"] >= 1
assert config["budgets"]["max_git_commands"] > 0
assert config["budgets"]["max_scanned_files"] > 0
assert config["budgets"]["max_scanned_bytes"] > 0

print("engineering security reviewer static authority invariants passed")

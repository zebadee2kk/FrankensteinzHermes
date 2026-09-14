#!/usr/bin/env python3
"""Static authority invariants for B034 adversarial QA."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
entrypoint = (ROOT / "scripts/engineering_qa/worker.py").read_text(encoding="utf-8")
worker = (ROOT / "scripts/engineering_qa/coordinator_core.py").read_text(encoding="utf-8")
sandbox = (ROOT / "scripts/engineering_qa/bwrap_sandbox_adapter.py").read_text(encoding="utf-8")
ledger = (ROOT / "scripts/engineering_qa/ledger_psql.py").read_text(encoding="utf-8")
migration = (ROOT / "db/migrations/0008_b034_qa_api.sql").read_text(encoding="utf-8")
config = json.loads((ROOT / "config/engineering-qa.example.json").read_text(encoding="utf-8"))
profiles = json.loads((ROOT / "config/qa-profiles.example.json").read_text(encoding="utf-8"))

required_worker = [
    'job_kind") != "engineering.qa"',
    'review_evidence_sha256',
    'review_manifest_sha256',
    'review_not_approved',
    'promotion_authorized": False',
    'sandboxed_qa_execute',
    'required_profile_ids',
    'allowed_profile_ids',
    'rejected_model_profile_ids',
    'candidate_executed',
    'B035 security review',
]
for token in required_worker:
    assert token in worker, f"missing B034 coordinator invariant: {token}"

# Reviewer/planner output is restricted to IDs + hypotheses; no arbitrary command shape.
assert 'set(item) != {"id", "hypothesis"}' in worker
assert '"operation": "run_profile"' in worker
assert '"profile_id": profile_id' in worker
assert '"command"' not in worker.split('def call_sandbox', 1)[1].split('def write_bundle', 1)[0]

# Source-repository reads are allowlisted; candidate execution happens only in the standalone clone/sandbox.
assert 'READ_ONLY_GIT = {"rev-parse", "merge-base", "rev-list", "status"}' in worker
assert '["git", "clone", "--quiet", "--no-checkout", "--local"' in worker
assert '["git", "checkout", "--quiet", "--detach", head]' in worker
for forbidden in ('git push', 'git merge', 'git reset', 'git branch -f', 'gh pr merge'):
    assert forbidden not in worker, f"forbidden promotion/mutation path present: {forbidden}"

# Entrypoint owns the durable execution boundary.  Candidate code cannot enter
# the sandbox until a stage-specific B031 effect has started, and each returned
# sandbox result is digest-bound before that effect commits.
for token in (
    '"operation": "begin_effect"',
    '"operation": "commit_effect"',
    'result_sha256 = core.sha256_bytes(core.canonical(result))',
    'core.call_sandbox = durable_call_sandbox',
    'qa_effect_begin_failed',
    'qa_effect_commit_failed',
):
    assert token in entrypoint, f"missing durable QA effect invariant: {token}"
assert entrypoint.index('"operation": "begin_effect"') < entrypoint.index('_original_call_sandbox(')
assert entrypoint.index('_original_call_sandbox(') < entrypoint.index('"operation": "commit_effect"')

required_sandbox = [
    '--unshare-all', '--clearenv', '--ro-bind', '--tmpfs',
    'NoNewPrivileges=yes', 'RestrictSUIDSGID=yes',
    'MemoryMax=', 'TasksMax=', 'CPUQuota=', 'RuntimeMaxSec=',
    'root-owned and non-group/world-writable',
    'tmpfs-copy-from-readonly-candidate',
]
for token in required_sandbox:
    assert token in sandbox, f"missing sandbox invariant: {token}"
for forbidden in ('docker.sock', 'podman.sock', '--share-net', '--bind', '/var/run/docker'):
    if forbidden == '--bind':
        assert '"--bind"' not in sandbox
    else:
        assert forbidden not in sandbox, f"forbidden sandbox authority present: {forbidden}"

assert 'PROFILE_CONFIG = pathlib.Path("/etc/frankensteinzhermes/qa-profiles.json")' in sandbox
assert 'WORKSPACE_ROOT = pathlib.Path("/var/lib/frankensteinzhermes/qa-workspaces")' in sandbox

# Database adapter/role are stage-specific and cannot authorize promotion or
# invoke generic effect mutators directly.
assert 'SET ROLE fzh_b034_qa' in ledger
for token in ('b034_begin_profile_effect', 'b034_commit_profile_effect'):
    assert token in ledger and token in migration, f"missing scoped effect capability: {token}"
assert 'fzh_b034_qa' in migration
assert "engineering.qa" in migration
assert 'B034 cannot authorize promotion' in migration
assert 'REVOKE ALL ON FUNCTION fzh.b034_begin_profile_effect' in migration
assert 'REVOKE ALL ON FUNCTION fzh.b034_commit_profile_effect' in migration
assert 'REVOKE ALL ON ALL TABLES IN SCHEMA fzh FROM fzh_b034_qa' in migration
assert 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA fzh FROM fzh_b034_qa' in migration

assert config["schema_version"] == 1
assert config["sandbox"]["allowed_profile_ids"]
assert set(config["sandbox"]["baseline_profile_ids"]).issubset(config["sandbox"]["allowed_profile_ids"])
assert profiles["schema_version"] == 1 and profiles["profiles"]
for profile_id, profile in profiles["profiles"].items():
    assert profile_id in config["sandbox"]["allowed_profile_ids"]
    assert isinstance(profile.get("command"), list) and profile["command"]
    for key in ("timeout_seconds", "memory_max", "tasks_max", "cpu_quota", "max_output_bytes"):
        assert key in profile, f"profile {profile_id} missing {key}"

print("engineering QA static authority invariants passed")

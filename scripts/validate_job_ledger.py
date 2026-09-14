#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
migration = (ROOT / "db/migrations/0003_job_ledger.sql").read_text(encoding="utf-8")
hardening = (ROOT / "db/migrations/0004_job_ledger_budget_accounting.sql").read_text(encoding="utf-8")
contract = ROOT / "tests/sql/job_ledger_contract.sql"
runbook = ROOT / "docs/operations/JOB-LEDGER.md"
cli = ROOT / "scripts/jobs/ledger.sh"

required = [
    "UNIQUE (job_kind, idempotency_key)",
    "FOR UPDATE SKIP LOCKED",
    "fzh.job_attempts",
    "fzh.job_gate_decisions",
    "fzh.job_events",
    "fzh.job_effects",
    "reconciliation_required",
    "side-effecting job cannot start without Action Gate allow evidence",
    "side-effecting job cannot complete without an effect idempotency record",
    "lease_expired",
    "dead_letter",
    "max_attempts IS DISTINCT FROM OLD.max_attempts",
    "reject_append_only_mutation",
]
for fragment in required:
    if fragment not in migration:
        raise SystemExit(f"job ledger migration missing invariant: {fragment}")

for fragment in (
    "lease_count",
    "retry_budget_refunded",
    "GREATEST(attempt_count - 1, 0)",
):
    if fragment not in hardening:
        raise SystemExit(f"job ledger budget hardening missing invariant: {fragment}")

for path in (contract, runbook, cli):
    if not path.exists():
        raise SystemExit(f"required B031 artefact missing: {path.relative_to(ROOT)}")

runbook_text = runbook.read_text(encoding="utf-8")
if "cannot mathematically guarantee exactly-once" not in runbook_text:
    raise SystemExit("runbook must state the external exactly-once limitation explicitly")
if "must not receive Docker membership" not in runbook_text:
    raise SystemExit("runbook must retain the Hermes least-privilege boundary")

contract_text = contract.read_text(encoding="utf-8")
for fragment in (
    "attempt_count <> 0 OR j.lease_count <> 1",
    "reconciliation_required",
    "expected immutable budget rejection",
    "expected append-only gate decision rejection",
):
    if fragment not in contract_text:
        raise SystemExit(f"job ledger contract missing assertion: {fragment}")

print("job ledger static invariants passed")

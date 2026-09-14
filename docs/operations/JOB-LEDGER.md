# B031 — Autonomous job ledger

The job ledger is the durable control plane for autonomous work. PostgreSQL is authoritative; workers may cache or poll, but they may not invent parallel job state.

## Why leases instead of `running=true`

A worker can disappear at any point. A lease gives ownership a deadline. If the worker stops heartbeating, the reaper can determine whether retry is safe.

The ledger distinguishes three cases:

1. **No side effect started** — an expired lease may retry within the finite budget.
2. **An external side effect was started** — the job becomes `reconciliation_required`; it is not auto-replayed.
3. **Budget exhausted** — the job becomes `dead_letter`.

This avoids the dangerous assumption that every interrupted job is safe to repeat.

## Idempotency model

Each job has `(job_kind, idempotency_key)` uniqueness. Resubmitting the same immutable definition returns the existing job. Reusing the same key with a different payload/risk/action/classification/budget fails loudly.

For side effects, the worker must also create an `effect_key` before performing the external action. Where the external API supports idempotency keys, use the same durable effect key externally as well.

B031 cannot mathematically guarantee exactly-once behaviour for arbitrary external systems that provide no transaction or idempotency primitive. Instead it guarantees that ambiguous work is never silently retried: it is quarantined for reconciliation.

## Retry budget and approval

`attempt_count` is the finite retry-budget counter. `lease_count` is the monotonic acquisition/audit sequence.

A worker lease initially consumes retry budget. If Action Gate returns `approval_required` before execution starts, the budget is refunded while `lease_count` remains monotonic. This lets an approved job resume later without losing an execution attempt.

Expired leases and ordinary execution failures consume retry budget. Workers cannot change `max_attempts`; immutable job-definition fields are protected by a database trigger.

## Action Gate linkage

Side-effecting work cannot enter `running` until the current lease attempt has an append-only gate record with:

- `decision=allow`;
- B030 request SHA-256;
- B030 policy SHA-256;
- B030 audit event UUID;
- reason.

`approval_required` moves the job to `waiting_approval`. `deny` moves it to terminal `denied`. B031 does not invent an approval token system; a later tightly-scoped approval mechanism must deliberately release waiting work.

## Append-only evidence

The following tables are append-only:

- `fzh.job_attempts`;
- `fzh.job_gate_decisions`;
- `fzh.job_events`.

`fzh.job_effects` permits only the transition `started -> committed`; it cannot be deleted or reassigned. Mutable operational state lives in `fzh.jobs`.

## Worker sequence

For a future side-effecting B032 worker:

```text
submit/idempotency check
  -> lease_next_job
  -> call B030 Action Gate
  -> record_job_gate_decision
  -> start_job
  -> begin_job_effect(effect_key)
  -> perform external action using effect_key where supported
  -> commit_job_effect(receipt)
  -> complete_job
```

A worker must heartbeat long-running work before the lease expires.

For non-side-effecting work, `start_job` does not require Action Gate evidence, although later policy may still choose to gate particular reads.

## Recovery

Run the deterministic reaper periodically:

```bash
bash scripts/jobs/ledger.sh reap
```

Review quarantined jobs:

```sql
SELECT job_id, job_kind, idempotency_key, status, attempt_count, lease_count,
       last_error_code, last_error_summary, updated_at
FROM fzh.jobs
WHERE status IN ('reconciliation_required', 'dead_letter', 'waiting_approval')
ORDER BY updated_at;
```

Do not manually change a quarantined job to `queued`. Reconciliation/resume semantics need explicit evidence and will be added as a controlled owner/approval path rather than a raw status edit.

## Operator CLI

The shell wrapper is for deployment/CI/operator use and talks through the existing Docker/PostgreSQL administration path:

```bash
bash scripts/jobs/ledger.sh show <job-uuid>
bash scripts/jobs/ledger.sh events <job-uuid>
bash scripts/jobs/ledger.sh lease diagnostic-worker 300 optional.kind
```

It is **not** the Hermes runtime access model. `fzh-hermes` must not receive Docker membership or broad PostgreSQL credentials just to use the ledger. B032 should receive a dedicated, least-privilege data-plane identity scoped to the specific ledger functions it needs.

## Backup and rollback

The tables are covered by the existing PostgreSQL backup/restore contract. Before a schema rollback, preserve `jobs`, attempts, gate decisions, effects, and events as audit evidence.

Because the ledger is not yet consumed by a production worker in B031, source rollback is straightforward. Once B032 depends on it, rollback must first stop new worker acquisition and preserve all outstanding leases/quarantine states.

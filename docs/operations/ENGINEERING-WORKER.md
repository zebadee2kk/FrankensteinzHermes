# B032 — Engineering implementation worker

B032 is intentionally a **single implementation stage**, not a general autonomous shell. Its output is a local candidate commit plus evidence for B033/B034/B035. It cannot approve, merge or deploy its own work.

## Authority boundary

A B032 execution is one already-leased B031 `engineering.implement` job. The worker requests B030 `workspace_branch_write` admission before it creates a worktree. A deny, approval-required response, malformed response, or unavailable Action Gate fails closed.

The only Git write target is a deterministic local branch:

```text
fzh/job-<first-12-hex-of-job-uuid>
```

`main`/`master` cannot be selected as an output branch. The canonical checkout must be a clean, non-detached repository whose HEAD exactly equals the requested 40-character base commit. Successful work leaves the canonical checkout unchanged, removes the temporary worktree, and retains only the local candidate branch. Failed work removes the temporary worktree and job branch.

GitHub push/PR/merge and deployment are **not B032 capabilities**. Under the GitHub Free governance model, a later promotion stage uses the separate fork/owner-mediated path and credentials; B032 must never receive repository admin, secret admin or upstream `main` write authority.

## Task envelope

B032 accepts a B031 lease envelope containing:

- `job_id` and `lease_token` UUIDs;
- `job_kind: engineering.implement`;
- data classification;
- objective;
- exact base commit SHA;
- `allowed_paths`;
- optional `context_paths` inside those allowed paths;
- `test_ids` that reference the trusted worker configuration;
- optional output branch, which if present must equal the deterministic job branch.

The job cannot supply arbitrary commands. Tests are referenced by ID and resolved through the trusted `test_allowlist` in the worker configuration.

## Model boundary

The production adapter calls the loopback LiteLLM gateway with the stable free-first alias `fzh-free-auto` by default. It receives a LiteLLM client credential only; it does not receive OpenRouter/provider credentials.

Model output is treated as hostile data. It must be JSON containing one unified Git diff. B032 rejects:

- absolute and parent-traversal paths;
- `.git` metadata paths;
- rename/copy diffs in v1;
- symlink and Git-submodule modes;
- changes outside declared allowed paths;
- constitutional/security paths denied by worker configuration;
- excessive file count or changed bytes.

Selected source context is explicit and bounded by bytes. The configuration also imposes model-call count, prompt bytes, model output-token ceiling, response bytes and per-call timeout. CI uses a deterministic fake adapter and makes no provider request.

## Tool boundary

The worker code itself uses only Git plumbing plus trusted allowlisted test commands. Test commands cannot come directly from model output or the job payload. All tool calls and tests run with timeouts and a reduced environment; model-provider credentials are passed only to the LiteLLM adapter.

There is no sudo, Docker socket, SSH, package-manager mutation, Git push, PR merge or deployment operation in B032.

## Evidence

On success the protected local evidence directory contains:

- `model.patch`;
- one log per allowlisted test;
- `implementation-evidence.json`;
- `manifest.json` with hashes/sizes.

Implementation evidence includes job/attempt identity, hash of the lease token rather than the raw token, branch/base/head SHA, changed paths/bytes, context hashes (not duplicate source contents), B030 request/policy hashes and audit event ID, model/budget usage, test/log hashes, and elapsed time.

Every success records:

```json
{"promotion_authorized": false, "next_stage": "B033 independent reviewer"}
```

B033 therefore receives an evidence bundle without inheriting the implementation model's hidden context.

## B031 lifecycle integration

The execution core deliberately does not obtain broad PostgreSQL or Docker privileges. The production launcher for B032 must bind the core to B031 through a least-privilege ledger adapter: record the B030 decision for the current lease, transition the job to running, track the candidate-commit effect, and complete/fail the job only after evidence is durable. That adapter is part of the B032 production integration and must use B031's constrained functions rather than permitting raw table updates.

Until that ledger adapter is present and tested, the core is a source candidate only and must not be treated as an autonomous production worker.

## Rollback

B032 never changes `main`. To abandon a successful candidate, delete the local `fzh/job-*` branch after preserving any required evidence. Failure cleanup already removes its worktree and branch. Evidence deletion is separate and deliberate.

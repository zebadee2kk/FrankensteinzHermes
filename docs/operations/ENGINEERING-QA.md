# B034 adversarial engineering QA

B034 runs only after B033 has independently approved a B032 candidate. It is an execution stage, not another code-review stage: candidate code and tests are treated as hostile input and may execute only through named, preapproved sandbox profiles.

## Authority boundary

The QA worker may:
- verify externally anchored B033 evidence and manifest hashes;
- verify the approved base/head/branch binding from Git objects;
- obtain B030 `sandboxed_qa_execute` admission and persist the gate evidence in B031;
- create one disposable local clone at the approved head;
- ask a bounded QA planner model for additional **profile IDs and hypotheses only**;
- invoke the sandbox adapter with exactly `operation`, `job_id`, `workspace`, and `profile_id`;
- write hashed QA evidence and complete its own `engineering.qa` job.

It may not mutate the canonical source checkout or candidate ref, push, create/merge a PR, deploy, select arbitrary shell commands, choose an image/runtime, enable network access, or authorize promotion. Every result records `promotion_authorized=false`.

## Admission

Before candidate execution B034 verifies:
1. `review-evidence.json` and `manifest.json` match SHA-256 anchors supplied by the QA job;
2. every manifest-listed artefact matches its recorded digest/size and the evidence file set has not changed;
3. B033 verdict is exactly `approve`, `integrity_verified=true`, `promotion_authorized=false`, and `next_stage` is `B034 adversarial QA`;
4. review job/base/head/branch bindings match the QA job;
5. the candidate branch still resolves to the approved head;
6. the candidate is still one linear commit directly on the approved base.

Admission mismatch is a terminal `reject` and candidate code is not executed.

## Sandbox boundary

Production profile execution uses `scripts/engineering_qa/bwrap_sandbox_adapter.py` behind a user-systemd transient unit. The adapter reads commands/resource policy only from `/etc/frankensteinzhermes/qa-profiles.json`, which must be root-owned and not group/world writable.

The worker supplies only a profile ID. The adapter enforces:
- Bubblewrap `--unshare-all` (including a new network namespace with no configured network);
- `--clearenv` and a minimal fixed environment;
- candidate clone mounted read-only at `/candidate`;
- an in-sandbox tmpfs `/workspace`, populated from the read-only candidate;
- no host HOME mount and no Docker/Podman socket;
- user-systemd `NoNewPrivileges=yes` and `RestrictSUIDSGID=yes`;
- per-profile memory, tasks, CPU, runtime, and output ceilings;
- bounded stdout/stderr collection;
- disposable execution state.

The unprivileged QA service account must not belong to `docker`, `podman`, `lxd`, `sudo`, or equivalent privileged groups.

## Install sandbox policy

Install Bubblewrap and systemd tooling using the host's package manager, then run as root:

```bash
scripts/engineering_qa/install_sandbox.sh <qa-service-user>
```

This installs the example profile catalogue as `/etc/frankensteinzhermes/qa-profiles.json` with root ownership and creates the fixed QA workspace/output roots owned by the unprivileged service account. Review the profile catalogue before production use; profile commands are security policy, not user/model input.

The service account needs a working `systemd --user` manager. Provision that through the host/service-manager design rather than granting root or Docker access to the worker.

## Database identity

Runtime database login is provisioned out-of-band and granted membership only in NOLOGIN role `fzh_b034_qa`. Migration `0008_b034_qa_api.sql` gives that role no table/sequence privileges, no generic B031 functions, no B032 candidate-effect functions, and no B033 review functions. Stage wrappers additionally assert that the target job kind is exactly `engineering.qa`.

## Outcome policy

- evidence/admission mismatch: `reject`, no candidate execution;
- sandbox infrastructure/adapter failure: retryable job failure;
- any selected profile `fail`, `timeout`, or `output_exhausted`: terminal `qa_failed`;
- all selected profiles pass: `qa_passed`, next stage `B035 security review`;
- B034 never authorizes promotion.

## CI contract

`.github/workflows/engineering-qa-contract.yml` uses deterministic fake gate/model/ledger/sandbox adapters for coordinator tests. It never gives the test job provider credentials or GitHub write permission. A separate ephemeral PostgreSQL contract proves the B034 role cannot read tables or call generic/B032/B033 functions, while it can complete only an `engineering.qa` job and leaves neighboring implementation/review jobs untouched.

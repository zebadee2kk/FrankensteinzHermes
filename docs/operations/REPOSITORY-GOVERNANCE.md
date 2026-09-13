# Repository Governance

GitHub is the engineering control plane for FrankensteinzHermes. Runtime agents may propose changes, but they must not be able to weaken the controls that decide whether those changes reach `main`.

## Current plan constraint

The repository is on GitHub Free and the required repository ruleset capability is not available for this project. That limitation is accepted by the owner and is **not** a blocker to the seed build.

We therefore do not pretend that `main` is technically protected when it is not. Instead, the seed phase uses compensating controls based on credential boundaries, CI evidence and owner-controlled promotion.

The canonical machine-readable policy is `policy/repository-protection.yaml`.

## Seed-phase promotion model

All normal changes still use:

1. branch or separate work repository/fork;
2. pull request;
3. `governance`, `secret-scan` and `dependency-review` CI;
4. evidence/rollback information where applicable;
5. merge by the owner or an explicitly owner-authorized session.

Autonomous agents must not self-merge to upstream `main` during this phase.

## Hard security boundary without rulesets

Because GitHub cannot enforce the branch boundary for us, the **credential boundary becomes the enforcement point**.

The future autonomous engineering identity must not receive upstream repository-content write, repository-admin, ruleset-admin, workflow-admin or Actions-secret administration authority. The preferred model is:

- autonomous worker writes to a dedicated fork/work repository;
- worker opens a pull request against `zebadee2kk/FrankensteinzHermes`;
- upstream CI evaluates the candidate;
- owner or explicitly owner-authorized release process promotes the candidate.

This prevents a compromised engineering agent from bypassing CI simply by pushing directly to `main`.

## Credential split

### Owner / break-glass identity

Held by the repository owner only. This identity can administer repository settings, secrets and upstream contents. It must never be made available to Hermes, coding workers, browser workers, CI jobs or the deployed runtime.

### Engineering identity

Used by autonomous development. It may read upstream, work in its own fork/work repository, create candidate commits and open pull requests. It must not have upstream content-write or administrative permissions.

### Runtime identity

The deployed companion receives read-only GitHub access by default. Runtime write capabilities require a separately approved capability and must not imply repository administration.

## Constitutional files

Architecture, autonomy policy, threat model, repository governance, CODEOWNERS and action-gate policy remain owner-controlled. The lack of a GitHub ruleset does not transfer constitutional authority to the autonomous system.

## Break-glass process

Break-glass is for recovery from a control-plane failure, not for bypassing inconvenient checks.

1. Record why normal promotion cannot be used.
2. Use the owner identity to make the minimum required change.
3. Restore the normal contribution path as soon as practical.
4. Record the exact action and resulting state in an issue or incident record.
5. Re-run governance/security checks before autonomous work resumes.

## Future hardening

If a future GitHub plan or alternative hosting platform provides enforceable protected branches/rulesets, enable them as an additional layer. Until then, the system must remain secure even without that feature by keeping upstream write authority out of autonomous credentials.

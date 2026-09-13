# Repository Governance

GitHub is the engineering control plane for FrankensteinzHermes. Runtime agents may propose changes, but they must not be able to weaken the controls that decide whether those changes reach `main`.

## Desired production rules

The canonical desired state is `policy/repository-protection.yaml`.

`main` must require pull requests and the status checks `governance`, `secret-scan`, and `dependency-review`. Force-push and branch deletion must be disabled. The normal engineering identity must not have repository administration, ruleset administration, or secrets-administration privileges.

Constitutional files are owner-controlled. Changes to architecture, autonomy policy, threat model, repository governance, CODEOWNERS or action-gate policy require owner review even when all automated checks pass.

## Credential split

### Owner / break-glass identity

Held by the repository owner only. This identity may change repository rules, secrets and governance. It must not be made available to Hermes, coding workers, CI jobs, browser workers or the deployed runtime.

### Engineering identity

Used by autonomous development. It may read the repository, create branches, commit to non-protected branches, create pull requests and read CI results. It must not be able to change rulesets, repository settings, Actions secrets, owner-review requirements or branch protection.

### Runtime identity

The deployed companion should normally need less GitHub authority than the engineering identity. Runtime access should be read-only unless a specific capability requires otherwise.

## Break-glass process

Break-glass is for recovery from a control-plane failure, not for bypassing inconvenient checks.

1. Record why normal promotion cannot be used.
2. Use the owner identity to make the minimum required administrative change.
3. Restore the expected governance configuration immediately after recovery.
4. Record the exact action, time and resulting state in a repository issue or incident record.
5. Run the governance and security checks before normal autonomous work resumes.

## Bypass test

Before autonomous engineering is enabled, perform and record a negative test using the engineering credential:

- direct push to `main` is rejected;
- ruleset/protection modification is rejected;
- Actions secret administration is rejected;
- ordinary branch creation and pull-request creation succeed.

## Current connector limitation

The ChatGPT GitHub connector used during bootstrap can read repository rulesets but does not expose repository-administration mutation endpoints. The desired state is therefore committed here, while activation of the GitHub ruleset remains an explicit owner/admin action until a suitably scoped administration mechanism is available. B001 must remain open until GitHub reports the desired protection as active and the bypass test has been recorded.

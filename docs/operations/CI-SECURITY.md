# CI Security Fail Policy

The CI pipeline is a promotion gate, not merely advisory output.

## Blocking findings

The following fail a pull request:

- repository governance invariant failure;
- malformed required YAML or JSON governance files;
- unpinned third-party GitHub Action references;
- detected private key material;
- TruffleHog verified or unknown secret findings in the candidate range;
- dependency review findings at **high** or **critical** severity;
- syntax/compile failure in repository validation scripts.

## Non-blocking but tracked findings

Medium/low dependency findings, maintainability warnings, performance observations and optional hardening opportunities may create follow-up work rather than block a candidate unless the affected component is security-sensitive or the risk assessment raises the change class.

## Runtime artefacts

Once buildable runtime artefacts exist, B002 extends rather than changes this policy: build jobs must produce an SBOM and vulnerability result bound to the candidate artefact/digest. Runtime promotion policy may set stricter thresholds than source-only CI.

## Least privilege

Workflow-level GitHub permissions default to `contents: read`. A job may request additional permissions only when its purpose requires them, and the reason must be visible in the workflow. Autonomous workers must not be able to modify repository settings or Actions secrets through CI credentials.

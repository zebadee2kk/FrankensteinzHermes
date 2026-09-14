# B035 independent engineering security review

B035 runs only after B034 produces independently anchored `qa_passed` evidence. It is a read-only security-analysis stage before B036 release; it does not execute candidate code and cannot mutate, merge, deploy, release or authorize promotion.

## Admission

Before any deterministic analyzer or model call, B035 verifies:
1. externally supplied SHA-256 anchors for B034 `qa-evidence.json` and `manifest.json`;
2. exact evidence file set plus every manifest-listed digest and byte count;
3. `qa_status=qa_passed`, `admission_verified=true`, `promotion_authorized=false`, and `next_stage=B035 security review`;
4. QA job ID plus base/head/branch binding;
5. all selected B034 profile records exist in order and have `status=pass` plus valid log digests;
6. candidate branch still resolves to the approved head;
7. candidate remains one linear commit directly on the declared base.

Failure of any admission check produces terminal `reject` evidence before security analysis.

## Deterministic analyzers

B035 reconstructs the candidate diff from Git objects and inspects changed blobs with finite file/byte/Git-command budgets. No candidate command is executed. The initial deterministic checks include:
- changes outside the declared implementation path scope;
- security-sensitive/protected path changes;
- dependency/lockfile changes;
- Git submodule and symlink modes;
- newly executable files;
- private-key markers and apparent hard-coded credentials;
- download-to-shell patterns;
- shell-enabled subprocess use and `os.system`;
- GitHub Actions `permissions: write-all`;
- `pull_request_target` use.

Critical/high deterministic findings block release. Medium findings block once the configured threshold is reached.

## Optional model

A separate bounded security model may receive the objective, acceptance criteria, independently reconstructed diff, changed paths and deterministic findings. It returns findings only with the exact fields:
`severity`, `category`, `path`, `line`, `rationale`, `evidence`, `remediation`.

It has no command/tool interface and no verdict field. Malformed output fails closed. Final `security_passed` versus `security_failed` is derived by deterministic code from all validated findings.

## Database identity

The runtime login is provisioned out of band and is a member only of NOLOGIN role `fzh_b035_security`. Migration `0009_b035_security_reviewer_api.sql` gives that role:
- no table or sequence privileges;
- no generic B031 mutation/effect functions;
- no B032 implementation capabilities;
- no B033 reviewer capabilities;
- no B034 QA/effect capabilities;
- only job-kind-constrained start/heartbeat/complete/fail operations for `engineering.security_review`.

B035 jobs must be submitted with `side_effecting=false`; the scoped functions reject a side-effecting security-review job. No B031 effect record is created because candidate code is never executed and B035 has no external side effect.

## Outcomes

- evidence/binding mismatch: `reject` before analyzers/model;
- analyzer/model infrastructure failure: retryable job failure;
- any critical/high finding: `security_failed`;
- configured medium threshold reached: `security_failed`;
- otherwise: `security_passed`, next stage `B036 release`;
- every result records `promotion_authorized=false`.

## CI

`.github/workflows/engineering-security-reviewer-contract.yml` runs deterministic fake model/ledger tests with no provider credential or GitHub write permission, then uses ephemeral PostgreSQL to prove the B035 role cannot access direct/generic/B032/B033/B034 surfaces and can complete only a non-side-effecting `engineering.security_review` job.

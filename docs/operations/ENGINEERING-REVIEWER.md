# B033 Independent Engineering Reviewer

B033 is a read-only review stage between B032 implementation and B034 adversarial QA. It does not trust B032 model reasoning and does not trust the B032 evidence directory until externally anchored hashes and every manifest entry have been verified.

## Authority boundary

The reviewer may read Git objects with a fixed command set (`rev-parse`, `merge-base`, `rev-list`, `diff`, `show`, `cat-file`), read the B032 evidence directory, call a reviewer model through LiteLLM, write its own review evidence directory, and update only its own `engineering.review` ledger job through the `fzh_b033_reviewer` capability API.

It cannot check out/switch branches, create or move refs, stage/commit code, push, open or merge PRs, deploy, call B032 candidate-effect functions, read/write ledger tables directly, or call generic B031 mutators. Review evidence always records `promotion_authorized=false`.

## Evidence integrity

A review job carries SHA-256 anchors for both B032 `implementation-evidence.json` and `manifest.json`. The reviewer then verifies every manifest-listed file, exact file-set membership, implementation job/base/head/branch bindings, and the no-promotion handoff marker. A mismatch produces a terminal review verdict of `reject` without calling a model.

The reviewer independently resolves base/head and candidate branch from Git, requires a single linear candidate commit whose parent is exactly base, reconstructs the binary diff, recomputes changed paths/bytes/diff SHA-256, and compares B032 claims that can be independently reproduced. Forbidden or undeclared changed paths are an integrity rejection even if B032 evidence claims otherwise.

## Model boundary

The reviewer receives only bounded task criteria, the independently reconstructed diff, and explicitly selected read-only source context. It uses a separate invocation from B032. The model has no tools and may emit only structured findings; it cannot supply commands or the final verdict.

Deterministic policy derives the verdict:
- integrity failure: `reject` without model call;
- any `critical` or `high` finding: `changes_required`;
- configured number of `medium` findings: `changes_required`;
- otherwise: `approve`.

Malformed model output fails closed as a retryable review-job failure rather than approving the candidate.

## Handoff

An approved review writes `review-evidence.json`, `review-model.json`, `reconstructed.diff`, and a SHA-256 manifest. The ledger result records the verdict and evidence/manifest hashes. Only an `approve` verdict names `B034 adversarial QA` as the next stage; B033 itself performs no promotion.

## Recovery

If a reviewer process dies, B031 lease expiry/retry semantics apply. Because B033 does not mutate candidate code or external systems, retry does not duplicate candidate effects. Review output directories are job-ID scoped and existing output is refused rather than silently overwritten.

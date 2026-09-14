# B026 — model evaluation registry

The model registry is the policy boundary between **discovery/evaluation** and **production routing**.

A fast, cheap or high-scoring model is not automatically eligible. Routing eligibility requires three independent conditions:

1. the provider/privacy policy allows the requested data classification;
2. the candidate has been explicitly promoted to `approved` with approval evidence;
3. a current, non-synthetic evaluation exists for the requested task role and, for local models, the current hardware fingerprint.

The ranking function applies those filters before it calculates a score.

## Tables

### `fzh.model_providers`

Stores the provider/security decision. Approval flags are separate for PUBLIC, INTERNAL and CONFIDENTIAL. Unknown retention/training properties remain nullable so CONFIDENTIAL routing can fail closed instead of converting unknown into permission.

### `fzh.model_candidates`

Stores a routable identity or alias and its promotion state. `status='approved'` requires both `approved_at` and `approval_evidence`.

Dynamic routes such as `fzh-free-auto -> openrouter/openrouter/free` are represented with `identity_kind='dynamic_router'`; this does not pretend the actual model behind the router is deterministic.

### `fzh.model_candidate_evidence`

Non-promoting evidence inbox. B025 llmfit recommendations are imported here first. Importing a row does **not** create a `model_candidates` row and cannot change provider approval or candidate status.

### `fzh.model_evaluations`

Stores measured/evaluated evidence for a known candidate and task role. Each row has an explicit validity window. Synthetic evaluations can be retained for development but the router-facing query always excludes them.

For local candidates, throughput/quality evidence is hardware-scoped through `hardware_fingerprint`; the ranking function will not use local evidence from a different machine.

## Data classification

`fzh.rank_model_candidates(task_role, classification, hardware_fingerprint)` implements the seed policy:

- **PUBLIC** — explicitly PUBLIC-approved providers only;
- **INTERNAL** — explicitly INTERNAL-approved providers only;
- **CONFIDENTIAL** — explicitly CONFIDENTIAL-approved local providers, or remote providers where `zero_retention=true` and `training_allowed=false` are both known;
- **SECRET** — no LLM candidate is returned;
- unknown classification — no candidate is returned.

Provider cost never overrides policy. Paid candidates are excluded in B026 because paid fallback remains disabled by owner policy.

## Ranking

Only the latest valid evaluation per candidate/task role participates. The initial seed score combines quality, task success, tool reliability, structured-output adherence, availability and error rate. Latency is a deterministic tie-break after score.

This score is intentionally version-one policy, not an eternal formula. Future changes to weights should be treated as a policy/config change with regression evidence.

## Importing B025 evidence

With PostgreSQL running and migrations applied:

```bash
bash scripts/models/import-llmfit-evidence.sh /path/to/evidence/llmfit/<timestamp>
```

The importer:

- requires `promotion_authorized: false` in the B025 report;
- verifies each raw recommendation file against the B025 manifest SHA-256 and byte count;
- derives a hardware fingerprint from canonical system JSON;
- imports raw recommendation evidence only into `model_candidate_evidence`;
- is idempotent for the same source hash/use-case/candidate;
- never writes provider approvals or candidate promotion state.

A human/Action Gate controlled reconciliation step can later link evidence to a canonical candidate or create a candidate record.

## Querying eligible candidates

```bash
# Remote candidates only because no hardware fingerprint is supplied.
bash scripts/models/rank-models.sh planner PUBLIC

# Include local evaluations measured on this exact hardware fingerprint.
bash scripts/models/rank-models.sh planner CONFIDENTIAL <64-char-hardware-sha256>
```

The command returns JSON. An empty JSON array is a valid fail-closed result and must not cause a caller to bypass policy by querying a provider directly.

## Promotion

Promotion is explicit. A candidate is not routable through this registry until an authorized change sets:

- `status='approved'`;
- `approved_at`;
- `approval_evidence`.

Provider approvals are a separate authorized change. Evaluation/import code must not modify either boundary.

## Rollback

B026 does not yet make LiteLLM depend on the registry, so source rollback is a database/schema rollback concern rather than a live inference cutover. Do not delete evidence to roll back routing policy; retain provenance and disable/retire candidates or revert the consuming router change when that layer is introduced.

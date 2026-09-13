# Release Evidence and Rollback Contract

Every candidate produced by an autonomous engineering worker must leave behind enough evidence for another process—or a human—to answer four questions:

1. What exact candidate was evaluated?
2. What proved that it met its acceptance criteria and did not introduce unacceptable risk?
3. What decision promoted, held, rejected or rolled it back?
4. How can the previous known-good state be restored?

The canonical machine-readable contract is `schemas/release-evidence.schema.json`. `examples/release-evidence.example.yaml` shows the expected shape.

## Promotion policy

A candidate cannot promote when any mandatory test or mandatory review failed, when a required rollback method is absent, or when a required human approval reference is absent.

For deployable L1-L3 changes, rollback information is mandatory. Staging evidence becomes mandatory once the project has a staging environment. L3 changes always require a human approval reference regardless of automated results.

## Review independence

The implementation worker must not be the sole reviewer of its own candidate. The independent review, security review and QA result fields exist so the release worker can distinguish implementation confidence from independent evidence. A review may be `not_required` only when the governing policy for that risk class explicitly permits it.

## Baselines

Changes that affect runtime behavior, model routing, prompts, memory quality, latency, cost, resource usage or task success should capture a before/after baseline. A candidate that materially regresses an agreed acceptance metric should not promote merely because its unit tests pass.

## Automatic failure handling

Failed candidates are normal. The autonomous builder should:

1. mark the evidence bundle `hold`, `reject` or `rollback`;
2. preserve the failed candidate and diagnostics;
3. create or update a follow-up issue with the failure evidence;
4. restore the last known good state if the candidate reached staging/canary/production;
5. continue with unrelated unblocked work where policy allows.

Failed experiments must not silently disappear, and they must not cause an infinite retry loop.

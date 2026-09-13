# ADR-0002: Git is the engineering control plane

- Status: Accepted
- Date: 2026-09-13

## Decision

Self-development occurs through version-controlled candidates, not direct mutation of production.

The normal path is issue -> branch -> implementation -> tests/reviews -> staging -> verification -> promotion/rollback.

## Consequences

- Changes are attributable and reversible.
- Autonomous agents can do substantial engineering without receiving repository-administration authority.
- Production state should converge from Git-managed definitions wherever practical.
- Emergency manual fixes must be reconciled back into Git.

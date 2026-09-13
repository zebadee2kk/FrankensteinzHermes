# ADR-0001: Hermes is companion/planner, not authorization authority

- Status: Accepted
- Date: 2026-09-13

## Context

The system needs one coherent user-facing intelligence while also safely operating tools and progressively modifying itself.

## Decision

Hermes is the primary companion, planner and programme manager. It may propose actions and delegate work, but consequential authorization is enforced by a deterministic boundary outside Hermes.

Hermes must not possess the normal runtime authority required to weaken that boundary.

## Consequences

- The user experiences one coherent assistant.
- Reasoning remains flexible.
- Prompt/model compromise does not by itself grant critical side-effect authority.
- An Action Gate interface becomes a seed requirement.

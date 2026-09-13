# ADR-007: GitHub Free governance fallback

- **Status:** Accepted
- **Date:** 2026-09-13

## Context

The project currently uses GitHub Free. The repository cannot rely on the desired branch-ruleset capability as a hard enforcement mechanism. Blocking the entire seed build on a paid hosting feature would add cost and human friction without improving the autonomous system itself.

The system still requires a trustworthy promotion boundary before autonomous development credentials are introduced.

## Decision

Proceed without GitHub rulesets during the seed phase and move the hard security boundary to credentials and repository topology.

1. Upstream `zebadee2kk/FrankensteinzHermes` remains owner-controlled.
2. Autonomous engineering identities must not receive upstream repository-content write, workflow administration, secrets administration or repository administration authority.
3. Autonomous engineering work should use a dedicated fork/work repository and open pull requests to upstream, or use an owner-mediated branch during bootstrap.
4. Upstream promotion continues to require the `governance`, `secret-scan` and `dependency-review` checks to pass as project policy.
5. Autonomous agents may not self-merge upstream while this fallback is active.
6. Constitutional changes remain human-owned regardless of CI result.
7. If enforceable branch protection becomes available later, add it as defense in depth without changing the credential boundary.

## Consequences

This does not make GitHub itself enforce every policy during the seed phase. An owner credential can still bypass process controls. That risk is accepted because the owner is the break-glass authority.

The autonomous system remains bounded because it will not possess that owner authority. A compromised worker therefore cannot directly mutate upstream `main` if the credential model is implemented as specified.

## Revisit trigger

Revisit when either:

- the GitHub plan changes;
- the repository moves to a platform offering suitable protected-branch controls;
- the autonomous engineering identity is being provisioned;
- or a simpler enforceable control becomes available.

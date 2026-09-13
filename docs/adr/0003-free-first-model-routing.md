# ADR-0003: Free-first, policy-aware model routing

- Status: Accepted
- Date: 2026-09-13

## Context

The seed should build itself primarily with OpenRouter/free and other free model capacity, while remaining able to work locally when remote capacity is unavailable.

## Decision

LiteLLM provides a common internal model interface. Routing prefers benchmarked free models/providers, then a free-router fallback, then local Ollama, then queue/retry. Paid fallback is disabled by default.

Provider eligibility is constrained by data classification and privacy policy; price never overrides those constraints.

Model identities are registry data, not architectural constants, because free availability changes over time.

## Consequences

- The seed can operate at very low marginal inference cost.
- Free-provider churn is handled by discovery/evaluation rather than architectural change.
- Local inference provides degraded operation.
- Continuous benchmarking is required to maintain useful routing quality.

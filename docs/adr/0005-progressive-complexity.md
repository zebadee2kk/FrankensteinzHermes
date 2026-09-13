# ADR-0005: Progressive complexity over platform-first architecture

- Status: Accepted
- Date: 2026-09-13

## Decision

The seed will use the simplest mechanism that safely satisfies current requirements and preserve interfaces for later replacement.

Not seed prerequisites: Kubernetes, service mesh, NATS, Temporal, clustered object storage, multi-node database HA or multiple overlapping policy/identity systems.

Introduce such components only when a measured operational requirement justifies the additional failure modes and maintenance burden.

## Consequences

- Faster first deployment on the Dell XPS.
- Lower operator burden.
- Easier debugging and recovery.
- Future migrations require clean internal contracts, but not speculative infrastructure today.

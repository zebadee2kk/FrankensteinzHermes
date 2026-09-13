# ADR-0004: Narrow canonical memory ownership

- Status: Accepted
- Date: 2026-09-13

## Decision

Seed memory responsibilities are intentionally narrow:

- GBrain owns provenance-aware personal factual memory.
- COG owns human-readable plans, synthesis and accumulated understanding.
- LightRAG provides rebuildable retrieval over source documents/external knowledge.

Additional memory technologies may be introduced later for caching or specialist-agent state but may not become competing canonical stores without a new ADR.

Bidirectional authoritative synchronization between memory systems is prohibited.

## Consequences

- Fewer conflicting truths.
- Easier backup/restore and migration.
- Retrieval systems can be replaced/rebuilt without corrupting canonical memory.
- Every durable memory write requires clear provenance semantics.

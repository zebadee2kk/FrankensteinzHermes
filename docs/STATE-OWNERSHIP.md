# Canonical State Ownership

The system must avoid circular synchronization and competing sources of truth.

## Ownership table

| State | Canonical authority | Notes |
|---|---|---|
| Architecture / desired project state | Git | Human- and agent-reviewed changes only |
| Autonomy/security constitution | Git + owner approval | Autonomous system cannot weaken it |
| Application configuration | Git | Environment-specific secrets excluded |
| Secrets | Host secret mechanism initially; later dedicated secret service if justified | Never canonical in Git or LLM memory |
| Jobs / approvals / action audit | PostgreSQL | Durable operational state |
| Raw documents / artefacts | Filesystem/object storage | Hash/provenance referenced from indexes |
| Personal factual memory | GBrain | Provenance and correction retained |
| Plans / journals / synthesized understanding | COG | Human-readable long-term knowledge |
| Document retrieval index | LightRAG | Rebuildable from canonical source material |
| Model registry / routing policy | Git + benchmark results in PostgreSQL | Runtime projection is rebuildable |
| Capability/tool registry | Git | Runtime cache may be derived |
| n8n workflows | Git-exported definitions where feasible | Runtime DB remains operational state |
| Infrastructure definitions | Git | Host changes should converge back to declared state |
| CI/CD evidence | GitHub + retained build artefacts | Do not rely on conversational memory |
| Backups | Independent backup destination | Must include restore verification |

## Allowed data flows

Examples:

```text
source evidence -> GBrain
GBrain -> retrieval/cache projection
COG -> retrieval projection
raw documents -> LightRAG index
Git capability registry -> runtime registry cache
```

## Forbidden patterns

Avoid bidirectional authoritative synchronization such as:

```text
GBrain <-> Mem0
COG <-> arbitrary agent memory
Git config <-> runtime-mutated config with no reconciliation
```

A cache/index may be rebuilt from its authority. Its contents must never silently overwrite the authority merely because retrieval produced a different value.

## Memory-write rules

A durable factual-memory write should carry:

- fact/value;
- source/provenance;
- timestamp;
- confidence/trust class;
- actor that proposed it;
- whether it is observation, inference or owner-confirmed;
- correction/withdrawal relationship where applicable.

Security credentials and raw secrets are not memory facts.

## World/action history

During the seed phase, consequential system events and actions may be recorded in append-oriented PostgreSQL tables rather than introducing a dedicated event platform.

If replay/fan-out requirements later justify NATS/event sourcing infrastructure, PostgreSQL remains the migration source and schemas must preserve correlation/causation IDs.

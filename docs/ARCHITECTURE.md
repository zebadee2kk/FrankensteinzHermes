# Architecture

## Objective

FrankensteinzHermes is a self-developing AI companion that begins as a small, reliable seed on one Ubuntu laptop and progressively builds the approved target system using its own engineering capabilities.

The initial implementation optimises for **functionality, recoverability and low operator burden** rather than enterprise-scale complexity.

## Seed node

Initial hardware:

- Dell XPS laptop
- Intel i7 CPU
- 32 GB RAM
- Ubuntu LTS
- no dedicated GPU assumed

The seed node is the original controller and may later enrol additional compute nodes.

## Seed architecture

```text
                         HUMAN
                           |
                           v
                        HERMES
                companion / planner / PM
                           |
          +----------------+----------------+
          |                |                |
          v                v                v
       KNOWLEDGE          MODELS           ACTIONS
       GBrain             LiteLLM          MCP/tools
       COG                   |              n8n
       LightRAG        +------+-----+       browser workers
                       |            |        engineering workers
                    Ollama       remote      |
                                providers    v
                                        ACTION GATE
                                   policy / audit / approval

                         PostgreSQL
             state / events / jobs / approvals

                    GitHub is the engineering
                        control plane
```

## Core responsibilities

### Hermes

Hermes is the primary user-facing identity and programme manager. It may reason, research, plan, select backlog work, delegate to workers and propose actions.

Hermes is **not** the ultimate authorization authority and may not weaken the controls governing its own authority.

### GitHub

Git is the source of truth for desired project state, architecture, policy, code, infrastructure definitions and backlog.

Production-changing work follows:

```text
issue -> branch -> implementation -> tests -> independent review -> staging -> verification -> promote/rollback
```

The autonomous system must not be able to disable protected controls that govern its own promotion path.

### PostgreSQL

The seed uses PostgreSQL for durable operational state where practical: jobs, approvals, audit events, capability metadata and service state. Specialised data systems are introduced only where measurements justify them.

### n8n

n8n provides deterministic integration and automation during the seed phase. Long-running durable workflow infrastructure such as Temporal is introduced later when autonomous workloads justify its operational cost.

### LiteLLM

LiteLLM provides the common model interface. It remains private to the system, runs with minimal privilege and must not be exposed directly to the public Internet.

### Ollama

Ollama provides local/degraded inference. Model selection is benchmark-driven and must respect the laptop's resource budget.

### Memory

Initial canonical memory roles are deliberately narrow:

- **GBrain:** personal facts, relationships and provenance-aware factual memory.
- **COG:** human-readable plans, conclusions, journals and accumulated understanding.
- **LightRAG:** retrieval over documents and external knowledge.

Other memory systems may be introduced as caches or specialist-agent state, but must not become circular competing sources of truth.

### Action Gate

The seed Action Gate is intentionally small. It classifies requested side effects, applies policy, records the decision and either allows, denies or requests human approval.

The gate contains no autonomous LLM that can reinterpret its own policy.

## Action classes

| Class | Meaning | Examples | Default |
|---|---|---|---|
| L0 | Read-only | search, inspect repository, query logs | automatic |
| L1 | Reversible low-risk write | create branch, draft file, open issue | automatic + audit |
| L2 | Consequential | merge PR, deploy, send external message | policy + verification; approval as configured |
| L3 | Critical | money movement, security-policy weakening, destructive backup/storage action, root credential change | explicit human approval |

## Self-development loop

```text
OBSERVE
  -> SELECT unblocked roadmap item
  -> PLAN
  -> IMPLEMENT on branch
  -> TEST
  -> INDEPENDENT REVIEW
  -> SECURITY REVIEW
  -> STAGING
  -> EVALUATE against baseline
  -> CANARY where appropriate
  -> PROMOTE or ROLLBACK
  -> OBSERVE
  -> LEARN
```

Every deployed change must have explicit acceptance criteria and a rollback method.

## Dogfooding

New capabilities should improve subsequent build work where safe. Examples:

- code-intelligence tools index FrankensteinzHermes itself;
- new security scanners scan subsequent changes;
- GBrain records architectural decisions and provenance;
- evaluation tooling evaluates future model and prompt candidates;
- model-routing improvements are shadow-tested before promotion.

Dogfooding never bypasses independent gates.

## Progressive growth

The seed should be able to enrol additional machines rather than requiring architectural replacement.

Likely future roles include:

- Apple Silicon inference worker;
- Proxmox-hosted isolated browser/code workers;
- NAS backup/archive target;
- additional local inference nodes;
- dedicated services introduced only once their acceptance criteria are met.

## Deferred components

The following are intentionally not seed prerequisites:

- Kubernetes/K3s;
- service mesh;
- SPIFFE/SPIRE;
- NATS;
- Temporal;
- clustered object storage;
- multi-node PostgreSQL HA;
- multiple overlapping memory stores.

The repository must preserve interfaces that allow these to be added later without placing them in the boot-critical path now.

## Failure philosophy

The companion must degrade gracefully.

- Remote model outage -> alternate approved provider -> local Ollama -> queue/retry.
- Worker failure -> mark job failed/retry within limit; do not loop indefinitely.
- Bad candidate release -> automatic rollback to last known-good version.
- Internet outage -> retain local chat, state, local inference and queued work where possible.
- Resource pressure -> stop new heavy work before starving core services.

The core should remain intentionally boring and recoverable while experimental capabilities operate around it.

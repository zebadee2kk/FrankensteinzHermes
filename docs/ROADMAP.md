# Roadmap

The roadmap is a dependency graph for the system to progressively build itself. Work should be selected from the earliest unblocked item whose autonomy and risk constraints are satisfied.

## Phase 0 — Repository and governance

- **B001 Repository governance** — branch protections/rulesets, PR workflow, CODEOWNERS where appropriate.
- **B002 CI baseline** — linting, tests, secret scanning and dependency checks.
- **B003 Architecture constitution** — architecture, autonomy, threat model, state ownership and model-routing policy committed.
- **B004 Release evidence format** — standard evidence bundle for every promoted change.

Exit: protected engineering path exists and can reject unsafe changes.

## Phase 1 — Seed host

Depends on Phase 0.

- **B010 Ubuntu host bootstrap** — idempotent host setup for Dell XPS i7/32 GB.
- **B011 Docker/Compose runtime** — pinned images, least-privilege defaults, health checks.
- **B012 PostgreSQL** — durable state, migrations, backup and restore smoke test.
- **B013 Basic observability** — host/service metrics, structured logs and actionable alerts.
- **B014 Resource governor** — memory/concurrency/thermal-pressure limits for the laptop.

Exit: host can be rebuilt from repository definitions and restored from backup.

## Phase 2 — Intelligence seed

Depends on B010-B014.

- **B020 Hermes deployment** — primary companion/planner.
- **B021 LiteLLM gateway** — private model gateway with minimal enabled features.
- **B022 OpenRouter free-first provider** — explicit free-model routing and provider-policy filters.
- **B023 Additional free providers** — approved endpoints such as Gemini/NVIDIA where policy permits.
- **B024 Ollama degraded mode** — local fallback model selected by benchmark.
- **B025 llmfit integration** — detect hardware and benchmark feasible local models.
- **B026 Model evaluation registry** — record quality, latency, reliability and policy eligibility.

Exit: the system can complete reasoning/coding tasks without a paid-model dependency and can degrade locally when remote providers fail.

## Phase 3 — Safe action and engineering factory

Depends on Phase 2.

- **B030 Action Gate v1** — deterministic allow/deny/approval service with audit.
- **B031 Job ledger** — durable autonomous job state in PostgreSQL.
- **B032 Engineering worker** — branch-based coding worker.
- **B033 Independent reviewer** — separate reviewer context/provider where practical.
- **B034 QA/adversarial worker** — tests implementations against acceptance criteria.
- **B035 Security reviewer** — secret, dependency, permission and exposure checks.
- **B036 Release worker** — staging, verification, promotion and rollback hooks.
- **B037 Autonomous backlog selector** — selects only dependency-unblocked, policy-eligible work.

Exit: the seed can autonomously implement an approved low-risk issue through staging and produce an auditable result.

## Phase 4 — Memory and knowledge

Depends on Phase 3.

- **B040 GBrain** — provenance-aware factual memory.
- **B041 COG second brain** — plans, conclusions and human-readable long-term understanding.
- **B042 LightRAG** — document/external-knowledge retrieval.
- **B043 Memory write policy** — candidate -> validate -> canonical write; correction/withdrawal support.
- **B044 Retrieval evaluation** — golden queries and poisoning/regression tests.

Exit: memory systems have explicit ownership and no circular synchronization.

## Phase 5 — Tool plane and web capability

Depends on B030 and B040-B044.

- **B050 MCP/tool registry** — typed capability metadata, permission/risk level and availability.
- **B051 n8n** — deterministic integrations.
- **B052 GitHub tool integration** — least-privilege engineering operations.
- **B053 Isolated browser worker** — untrusted web content separated from core services.
- **B054 Ingress quarantine** — validate downloaded/attached content before trusted processing.
- **B055 Egress/SSRF guardrails** — block management/private targets unless explicitly allowed.

Exit: Hermes can discover and use approved tools without inheriting unrestricted credentials.

## Phase 6 — Dogfood engineering improvements

Depends on Phase 3.

Candidate integrations from the starred catalogue should now improve the factory itself, including:

- GitNexus;
- grepai;
- Understand-Anything;
- code-review-graph;
- Repomix/OpenWiki;
- Promptfoo/evaluation tooling;
- SkillSpector/agent-scan/AgentShield;
- skill management/recording tooling.

Each integration must prove a measurable benefit before becoming production-critical.

Exit: later roadmap work benefits from code intelligence, evaluation and supply-chain/security tooling built by earlier work.

## Phase 7 — Durable autonomy

Introduce only after there is evidence the simpler seed has outgrown its mechanisms.

- **B070 Temporal** — durable multi-hour/day engineering workflows and human waits.
- **B071 NATS JetStream** — event fan-out/replay when direct API/n8n integration becomes limiting.
- **B072 Event contracts** — AsyncAPI/JSON schema for mature event flows.
- **B073 Shadow/canary framework** — compare candidate models/prompts/agents against production without side effects.

Exit: long-running work survives reboots/failures and can resume deterministically.

## Phase 8 — Additional compute

- **B080 Node discovery/enrolment** — explicit owner-authorized enrolment only.
- **B081 Apple Silicon worker** — benchmark and assign suitable inference tasks.
- **B082 Proxmox worker/VM integration** — disposable browser/code/security sandboxes.
- **B083 NAS backup/archive** — not a latency-critical database path.
- **B084 Mesh-LLM evaluation** — private distributed inference; experimental modes staging-only until proven.
- **B085 SIE specialist inference** — embeddings/rerank/OCR/extraction/safety workloads.

Exit: workload placement can use multiple resources without changing Hermes' user-facing identity.

## Phase 9 — Companion experience

- CopilotKit rich UI;
- Hermes Desktop where useful;
- voice input/output;
- proactive but rate-limited notifications;
- daily owner brief and approval queue;
- offline/degraded user experience.

## Phase 10 — Specialist pods

Business, research, personal productivity, finance/paper-trading and security/research pods are introduced individually with domain-specific permissions and tests.

Critical finance/security side effects remain governed by the autonomy constitution.

## Phase 11 — Continuous improvement

The mature loop may automatically:

1. inspect telemetry, failures and repeated human intervention;
2. discover upstream releases and useful new capabilities;
3. create improvement hypotheses/issues;
4. implement candidates in isolation;
5. benchmark against baselines/golden datasets;
6. promote eligible improvements;
7. roll back regressions;
8. summarize only material decisions/exceptions to the owner.

The roadmap itself may be extended autonomously, but constitutional/security changes remain human-owned.

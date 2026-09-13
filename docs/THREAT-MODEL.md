# Threat Model

## Scope

This threat model covers the seed deployment on a single Ubuntu laptop and the system's progressive self-development.

The system deliberately consumes untrusted inputs: websites, repositories, package metadata, model outputs, documents, tool descriptions and messages. Compromise of one input or worker must not automatically grant authority over the host, GitHub governance, secrets or critical external actions.

## Assets

Highest-value assets include:

- owner identity and approval authority;
- GitHub repository and production-promotion controls;
- API/provider credentials;
- personal/business memory and retrieved documents;
- host operating system and SSH/admin access;
- PostgreSQL state and backups;
- action policy/audit history;
- future connected infrastructure and financial/security tooling.

## Trust boundaries

1. **Human -> companion UI/Hermes** — authenticated owner instructions.
2. **Hermes -> Action Gate** — proposed actions are not inherently trusted.
3. **Core -> workers** — browser/code/research workers may process hostile material.
4. **System -> external model providers** — data release must satisfy provider/data-classification policy.
5. **System -> Internet** — remote content is untrusted evidence, not instructions.
6. **Workers -> GitHub/external tools** — credentials are scoped per capability.
7. **Runtime -> constitutional controls** — autonomous agents must not control the mechanism that limits them.

## Primary threats and controls

### Prompt injection / indirect instruction injection

Threat: web pages, repositories or documents attempt to instruct agents to reveal secrets, bypass policy or execute tools.

Controls:

- label retrieved content as untrusted data;
- never grant authority based on instructions contained in retrieved content;
- keep consequential tool execution behind the Action Gate;
- isolate browser/repository analysis workers;
- restrict tool credentials and egress;
- regression-test known injection patterns.

### Tool/MCP poisoning

Threat: malicious or altered tool metadata changes agent behavior or tricks routing.

Controls:

- approved capability registry owned in Git;
- pin versions/digests where practical;
- tool descriptions are metadata, not authorization;
- explicit permission and risk class per tool;
- security review before promotion of new tools.

### Malicious dependency / supply-chain compromise

Controls:

- dependency lockfiles;
- pinned container digests for production where practical;
- secret and vulnerability scanning;
- SBOM generation as the build matures;
- staged dependency updates;
- rollback to known-good artefacts;
- never execute arbitrary setup scripts from discovered repositories in the core environment.

### Secret leakage

Controls:

- no secrets in Git;
- environment/secret files excluded from repository;
- credentials scoped to minimum capability;
- redact secrets from logs/model prompts where practical;
- provider routing considers data classification;
- credential rotation after suspected exposure.

### Excessive agency / privilege escalation

Controls:

- action classes L0-L3;
- bounded runtime/tool/model budgets;
- separate owner-controlled constitutional controls;
- no repository-admin credential for ordinary engineering agents;
- privileged changes require explicit approval.

### Memory poisoning

Threat: false/malicious content becomes durable personal/system memory and influences future decisions.

Controls:

- provenance retained with factual memory;
- distinguish observation, inference and owner-confirmed facts;
- reversible/correctable memory writes;
- higher trust threshold for security/identity/financial facts;
- retrieved content does not automatically become canonical memory.

### SSRF and browser pivoting

Controls:

- browser workers isolated from core where possible;
- deny access to loopback, host metadata and management networks unless explicitly needed;
- download quarantine and type validation;
- restrict browser credentials;
- never mount host Docker socket into browser workers.

### Self-modification of controls

Threat: an agent changes tests, policy or protections so a desired change can pass.

Controls:

- constitutional paths and repository governance require owner approval;
- implementation cannot satisfy its own independent review requirement;
- tests may evolve, but weakening critical assertions is itself a governed change;
- production promotion relies on controls outside the candidate process.

### Resource exhaustion / self-DoS

Controls:

- host RAM reserve;
- limits on concurrent heavy agents/browsers;
- one heavy local model at a time initially;
- queue work under pressure;
- terminate runaway jobs at budget limits;
- monitor disk growth, swap and temperature.

### Model/provider compromise or outage

Controls:

- provider diversity;
- explicit model/provider eligibility;
- free router is fallback, not sole deterministic dependency;
- local degraded mode;
- treat model output as untrusted proposals requiring normal validation.

### Bad autonomous release

Controls:

- candidate branch;
- tests and independent review;
- staging;
- baseline comparison;
- canary where material;
- automatic rollback trigger;
- last-known-good artefact retained.

## Incident modes

The system must support at minimum:

- **normal** — configured autonomy active;
- **restricted** — autonomous external writes disabled, read/reason/test still available;
- **recovery** — only owner/recovery processes may change the system.

A suspected credential leak, unexplained privileged action, audit gap or repeated rollback should automatically move the relevant capability to restricted mode pending review.

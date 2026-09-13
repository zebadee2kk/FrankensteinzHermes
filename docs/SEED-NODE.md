# Seed Node: Dell XPS Ubuntu

## Role

The Dell XPS is the first home and original controller for FrankensteinzHermes.

Target baseline:

- Ubuntu LTS;
- Intel i7 CPU;
- 32 GB RAM;
- no dedicated GPU required;
- stable wired networking preferred for unattended operation;
- encrypted storage where practical;
- remote administration restricted to an approved secure path.

## Host responsibilities

The seed node initially hosts:

- Hermes;
- PostgreSQL;
- LiteLLM;
- Ollama;
- n8n;
- Action Gate / companion-core;
- GBrain;
- COG;
- LightRAG when Phase 4 is reached;
- lightweight observability;
- engineering coordinator and limited isolated workers.

Heavy browser/code workloads may later move to disposable Proxmox VMs or other enrolled workers.

## Host installation philosophy

The host should contain only the minimum packages required to run and recover the containerized/services layer.

Bootstrap must be idempotent and stored in Git.

Prefer:

- Docker Engine + Compose;
- systemd for host-level lifecycle;
- host firewall default-deny for unsolicited inbound access;
- automatic security updates with reboot policy that does not interrupt active critical jobs without checkpointing;
- SSH keys, no password login when remote SSH is enabled;
- dedicated service accounts / rootless execution where practical;
- no Docker socket mounted into untrusted workers.

## Resource governor baseline

Initial values are conservative starting points and must be tuned by measurement.

```yaml
host:
  ram_gb: 32
  reserve_ram_gb: 6

limits:
  concurrent_heavy_agents: 2
  concurrent_browsers: 2
  heavy_local_models: 1
  heavy_index_jobs: 1

pressure:
  stop_new_heavy_work_on_swap_pressure: true
  pause_background_indexing_when_interactive: true
  disk_free_min_percent: 15
```

The governor should use measured load rather than assuming all i7/XPS generations behave identically.

## Local inference

Local inference is a resilience feature first and a performance feature second.

The seed should:

1. inventory CPU, RAM and available acceleration;
2. use `llmfit` or equivalent to shortlist candidates;
3. benchmark actual Ollama throughput and memory pressure;
4. register successful models by task role;
5. unload heavy models when pressure threatens core services.

## Storage layout

Suggested logical layout:

```text
/opt/frankensteinzhermes/      deployment definitions
/var/lib/frankensteinzhermes/  durable runtime data
/var/log/frankensteinzhermes/  local logs where not centralized
/srv/frankensteinzhermes/      artefacts/quarantine/workspaces
```

Exact paths may change during implementation, but data, configuration and disposable workspaces must remain clearly separated.

## Backups

At minimum protect:

- PostgreSQL logical/physical backup as selected by implementation;
- GBrain durable data;
- COG vault/data;
- raw source documents not reproducible elsewhere;
- deployment metadata needed to restore the seed;
- encrypted copies of recovery configuration/keys through an owner-controlled process.

Git itself is not a backup for runtime data.

A backup is not considered working until an automated restore test has successfully reconstructed an isolated test instance.

## Power and thermals

Because the first node is a laptop:

- disable sleep/suspend while acting as an always-on server unless explicitly scheduled;
- preserve thermal safety;
- exploit the laptop battery as short transient power protection but do not treat it as a complete UPS strategy;
- expose battery health, thermal and disk-health signals to monitoring where available;
- gracefully stop heavy autonomous work before forced shutdown.

## Expansion

The XPS remains the controller until a deliberate migration occurs. Additional nodes are workers, not automatically trusted peers.

Enrolment must require owner authorization and record:

- node identity;
- hardware inventory;
- permitted workloads;
- network reachability;
- data classifications allowed;
- current health/capacity.

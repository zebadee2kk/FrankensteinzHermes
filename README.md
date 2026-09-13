# FrankensteinzHermes

FrankensteinzHermes is a secure, self-developing AI companion and engineering system designed to bootstrap on a single Ubuntu laptop, then autonomously build, test, deploy, operate and improve itself toward an approved target architecture.

The project starts deliberately small. Its first home is a Dell XPS with an Intel i7 CPU, 32 GB RAM and Ubuntu. Heavy reasoning and coding should use OpenRouter free models and other approved free providers where practical, while local Ollama inference provides offline/degraded capability. The system should progressively dogfood every capability it adds.

## Core principles

- **Hermes is the companion and planner, not the security authority.**
- **Free-first inference, not free-at-all-costs.** Paid models are disabled by default and may only be introduced through explicit budget policy.
- **Git is the engineering control plane.** Changes are proposed on branches, tested, reviewed, staged and promoted rather than modifying production directly.
- **Autonomy is earned.** Low-risk, reversible work can become fully autonomous; critical actions remain human-gated.
- **The system improves itself through candidates, tests and rollback.** It never weakens its own constitutional controls.
- **The core stays boring.** Prefer a small number of durable services over unnecessary distributed infrastructure.
- **Security controls protect side effects without crippling reasoning, research or experimentation.**
- **Every consequential action is auditable and attributable.**
- **Every deployed change must have a rollback path.**
- **No secrets belong in this repository.**

The detailed architecture, roadmap, autonomy constitution, threat model and implementation backlog are maintained in this repository and are intended to become executable inputs to the system itself.

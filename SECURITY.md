# Security Policy

## Reporting security issues

Do not open a public issue containing credentials, exploit details that expose a live deployment, private personal data or other sensitive material.

For the initial private-owner build, security findings should be recorded through an owner-controlled channel and converted into a sanitized repository issue where useful.

## Repository rules

- Never commit API keys, passwords, tokens, private keys or recovery secrets.
- Never commit personal/private memory exports to this public repository.
- Use example configuration with placeholders only.
- Pin dependencies and container versions for promoted environments where practical.
- Treat code, skills, prompts and tool definitions imported from external repositories as untrusted until reviewed.
- Security-sensitive paths and autonomy-policy changes require explicit owner review.

## Runtime principles

- Least privilege for every worker/tool credential.
- No public exposure of internal model gateways/databases.
- Consequential actions pass through the Action Gate.
- Untrusted browser/code workloads execute away from host-critical authority.
- Provider routing must respect data classification.
- Audit decisions and production side effects.
- Preserve a tested rollback path and last-known-good release.

## Incident triggers

Move the affected capability into restricted mode if any of the following occurs:

- suspected credential disclosure;
- unexplained privileged action;
- audit trail gap;
- repeated automatic rollback;
- unexpected network exposure;
- integrity mismatch in a promoted artefact;
- evidence that an untrusted input influenced authorization rather than merely reasoning.

## Public-repository warning

This repository is public. Architecture and example configuration may be stored here, but deployment secrets, private memory, customer/client data, host-specific sensitive inventory and live credentials must remain outside the repository.

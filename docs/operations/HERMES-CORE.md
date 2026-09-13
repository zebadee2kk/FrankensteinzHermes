# Hermes Core Deployment

Hermes is the companion/planner for FrankensteinzHermes. It is deliberately **not** the authorization authority and must not inherit unrestricted host, Docker, canonical-repository, or security-policy privileges.

## Reviewed upstream pin

The reviewed upstream source is recorded in `config/hermes-upstream.yaml`. Production installation always uses the exact 40-character commit recorded there. A moving branch or `curl .../main/... | bash` is not an acceptable production deployment.

Upgrades are candidate changes: update the pin on a branch, review upstream changes, run CI/evals, test the candidate, and then promote or roll back.

## Install

On the Ubuntu seed node:

```bash
sudo bash scripts/hermes/install-pinned.sh
```

The wrapper:

1. validates Ubuntu/Linux and the pinned commit;
2. creates the dedicated `fzh-hermes` identity if needed;
3. refuses to continue if that user belongs to `sudo` or `docker`;
4. downloads the upstream installer from the exact pinned GitHub commit;
5. confirms the expected installer controls still exist;
6. installs Hermes under the dedicated user's state tree with:
   - `--commit <pin>`
   - `--force-commit`
   - `--skip-setup`
   - `--skip-browser`
   - `--skip-computer-use`
   - `--non-interactive`
7. leaves provider setup and gateway activation as explicit later steps.

Browser/computer-use is excluded from the core because hostile web content belongs behind the later isolated tool plane.

## State layout

Default layout:

```text
/var/lib/frankensteinzhermes/hermes-user/
├── hermes-agent/        pinned upstream code + virtual environment
└── .hermes/             Hermes mutable state/config/sessions/skills
```

The mutable state directory must be backed up before code upgrades that may migrate data.

## Credentials

Do not commit credentials or embed them in installation commands.

Provider credentials should be provisioned locally after install with file permissions that allow only the dedicated Hermes identity (and root) to read them. Where Hermes' own setup/configuration command stores credentials, run it as `fzh-hermes` and ensure its home remains mode-restricted.

Never give the Hermes runtime identity:

- passwordless sudo;
- membership in the `docker` group;
- `/var/run/docker.sock`;
- GitHub repository administration;
- canonical-repository content-write credentials under the GitHub Free fallback;
- permission to modify the autonomy constitution/action gate/security policy.

## Verify

```bash
sudo bash scripts/hermes/verify.sh
```

Verification checks:

- dedicated identity exists;
- forbidden `sudo`/`docker` membership is absent;
- installed Git checkout exactly matches the approved commit;
- Hermes executable resolves for the dedicated identity;
- `hermes doctor` succeeds.

## Configure models

B020 only establishes the companion runtime. B021/B022 introduce the private LiteLLM gateway and free-first OpenRouter routing. Until then, do not improvise a production provider configuration in source.

## Gateway activation

After local configuration exists and verification passes, install the upstream gateway service explicitly as the Hermes identity. Upstream currently supports `hermes gateway install` for systemd/launchd background service management.

Do not start messaging platforms or expose ingress until their authentication/allowlist policy is separately reviewed.

## Upgrade

Never run an unattended `hermes update` against production `main` as the self-improvement mechanism.

Upgrade flow:

1. discover a candidate upstream commit;
2. create an issue/branch updating `config/hermes-upstream.yaml`;
3. inspect the upstream diff/security advisories;
4. run FrankensteinzHermes CI and later Hermes-specific evals;
5. back up `$HERMES_HOME`;
6. install candidate to staging/shadow context when available;
7. promote only if evidence improves or preserves baseline;
8. retain previous pin for rollback.

## Rollback

Code rollback is pin-based. Restore the previous approved commit and rerun `install-pinned.sh` with the previous configuration. Preserve `$HERMES_HOME` first. If an upstream migration makes the state incompatible, restore the pre-upgrade state backup rather than forcing a code rollback against newer data.

The core deployment stores no provider secrets in Git, so reverting repository code does not rotate or reveal credentials.

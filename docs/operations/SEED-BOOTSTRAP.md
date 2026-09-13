# Ubuntu Seed-Host Bootstrap

B010 prepares the first FrankensteinzHermes home: a Dell XPS i7/32 GB laptop running Ubuntu LTS. The bootstrap is intentionally small. It prepares the operating system for the later Docker Compose runtime but does not deploy Hermes, models, databases or provider credentials.

## Supported baseline

The current primary target is Ubuntu 26.04 LTS on amd64. The bootstrap also recognizes 24.04 and 22.04 LTS so an existing supported Ubuntu LTS installation can be evaluated without a forced reinstall.

## Read-only preflight

```bash
./bootstrap/seed-host.sh --check
```

The preflight refuses non-Ubuntu hosts and checks the seed hardware floor before making any changes. For the 32 GB design, at least 24 GiB detected RAM and 50 GiB free root-filesystem space are required before bootstrap.

## Apply

Only after B001 repository governance is active and the B010 candidate is approved:

```bash
sudo ./bootstrap/seed-host.sh --apply
```

The script installs the minimal host tooling, Docker/Compose from Ubuntu packages, UFW, unattended upgrades, SMART/lm-sensors tooling and creates the FrankensteinzHermes runtime directories. It does **not** add the operator or autonomous agents to the `docker` group; membership in that group is effectively privileged and must not become an implicit agent capability.

If TCP/22 is already listening, the script creates an SSH firewall allowance before enabling UFW so an existing SSH service is not silently locked out. This is a bootstrap safeguard, not the final network policy; later work should restrict management access to the actual trusted management path.

## Verify

```bash
sudo ./bootstrap/verify-seed-host.sh
```

A successful verification checks Ubuntu, memory/disk reserve, required commands, Docker/Compose, Docker boot enablement, UFW, SSH/firewall consistency, automatic update policy, runtime directories and failed systemd units. Lack of swap is currently a warning rather than a failure because swap strategy should be chosen after measuring the actual XPS storage and local-model workload.

## State capture

Before mutating the host, every `--apply` run records state under:

```text
/var/lib/frankensteinzhermes/bootstrap/<UTC timestamp>/
```

The capture includes the package inventory, previous Docker enablement state and UFW state. This makes later recovery evidence-based instead of relying on memory.

## Reboot acceptance test

Before B010 closes:

1. run the verification script successfully;
2. reboot the XPS;
3. confirm the host returns normally;
4. rerun `sudo ./bootstrap/verify-seed-host.sh`;
5. confirm Docker is active and enabled, UFW remains active, automatic updates remain configured and no new failed systemd units exist;
6. attach the results to the B010 release evidence.

## Rollback and recovery

B010 deliberately avoids pretending that uninstalling packages is a safe generic rollback. Host baselines are stateful, so rollback is scoped by what changed:

- **Firewall problem:** use local console access, inspect the captured `ufw.before.txt`, then adjust/disable UFW before attempting remote access again.
- **Docker problem:** stop/disable `docker.service` and `containerd.service`; compare the captured package inventory before removing any package that may have existed previously.
- **Automatic-update problem:** restore or remove `/etc/apt/apt.conf.d/20auto-upgrades` according to the pre-bootstrap state and restart the relevant service/timer.
- **Runtime directories:** these contain no production state during B010. They may be removed only after verifying no later phase has placed data inside them.
- **Catastrophic bootstrap failure:** recover from local console, retain the timestamped state capture, file the incident evidence, and do not continue to B011 until the host passes verification.

Once B012 introduces durable application data, host rollback must never delete `/var/lib/frankensteinzhermes` without a tested backup/restore procedure.

## Secrets

No API key, GitHub credential, model-provider key, SSH private key or other runtime secret belongs in this repository or in `config/seed-host.env.example`. B010 prepares only non-secret host configuration.

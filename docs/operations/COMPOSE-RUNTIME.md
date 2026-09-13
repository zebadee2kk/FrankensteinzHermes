# Docker Compose Runtime Contract

The seed node uses Docker Compose as the first runtime orchestrator. The goal is deliberately simple: predictable service lifecycle, explicit healthchecks, bounded resources and secure defaults without introducing Kubernetes or another scheduler before there is a demonstrated need.

## Canonical layout

- Compose file: `deploy/compose/compose.yaml`
- Verification: `scripts/compose/verify.sh`
- Runtime state: `/var/lib/frankensteinzhermes`
- Operator configuration: `/etc/frankensteinzhermes`
- Secrets: never committed to Git; later runtime secrets will be mounted or injected from the approved secret mechanism.

## Seed canary

B011 contains a tiny `seed-canary` service. Its purpose is to verify that the Docker/Compose runtime, restart policy, healthchecks, internal networking and resource constraints work correctly before durable services are added.

The canary:

- publishes no ports;
- uses an internal-only Docker network;
- runs read-only with a small tmpfs;
- drops all Linux capabilities;
- uses `no-new-privileges`;
- has strict CPU, memory and PID limits;
- never mounts the Docker socket;
- uses an explicit image version rather than `latest`.

The canary may be removed once the production services themselves provide equivalent runtime verification.

## Operator flow

From the repository root:

```bash
./scripts/compose/verify.sh
sudo docker compose -f deploy/compose/compose.yaml pull
sudo docker compose -f deploy/compose/compose.yaml up -d
sudo docker compose -f deploy/compose/compose.yaml ps
```

Wait for `seed-canary` to report healthy.

To stop without deleting local state:

```bash
sudo docker compose -f deploy/compose/compose.yaml stop
```

To remove the current runtime containers/network:

```bash
sudo docker compose -f deploy/compose/compose.yaml down
```

## Rollback

B011 itself contains no persistent application data. Rollback is therefore:

1. checkout/redeploy the previous known-good repository commit;
2. run `docker compose down` using the current candidate if required;
3. run `docker compose up -d` using the previous known-good Compose definition;
4. verify service health.

Later stateful services must add their own backup/restore requirements before they can be promoted.

## Rules for future services

Every service added to the seed Compose stack must justify exceptions to these defaults:

- explicit image version; immutable digest preferred once the release process supports automated digest updates;
- healthcheck where the software supports one;
- no `privileged: true`;
- no Docker socket mount for normal services or untrusted workers;
- no host networking unless explicitly approved;
- no public port exposure by default;
- `cap_drop: [ALL]` unless a documented capability is required;
- `no-new-privileges` where compatible;
- CPU/RAM/PID bounds appropriate to the 32 GB laptop;
- persistent data placed in an explicit named volume or documented host path;
- credentials injected at runtime rather than stored in Compose or Git.

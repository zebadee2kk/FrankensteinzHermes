# Seed Observability

B013 gives FrankensteinzHermes enough visibility to operate and later improve itself without turning the Dell XPS into a monitoring cluster.

The seed stack is deliberately limited to:

- **Prometheus 3.14.0** for metrics and alert evaluation;
- **node_exporter 1.12.1** for CPU, memory, filesystem and host metrics;
- a one-shot host health collector every minute for Docker Compose service state and backup freshness;
- bounded Docker JSON logs available through `docker compose logs`.

Grafana, Loki, Tempo, Alertmanager and full OpenTelemetry pipelines are deferred until their value is demonstrated by the running system.

## Network model

Prometheus and node_exporter have no published host ports. They communicate only on the internal Compose network. Operators and Hermes query Prometheus through the local Docker control plane using `scripts/health/status.sh` rather than exposing a dashboard to the LAN.

A later authenticated UI can be added through the normal ingress/security design.

## Host metrics

node_exporter mounts host `/proc`, `/sys` and `/` read-only and remaps those paths using its supported path flags. It does **not** receive the Docker socket, elevated capabilities or a host port.

The textfile collector reads custom FrankensteinzHermes metrics from:

```text
/var/lib/frankensteinzhermes/metrics/textfile
```

## Compose service health

`scripts/health/collect.sh` runs on the host rather than inside a container. This is intentional: reading Docker state requires access to the Docker control plane, and we do not want to place `/var/run/docker.sock` inside a long-running monitoring container.

The collector emits, for every non-profile Compose service:

- container presence;
- running state;
- whether a Docker healthcheck exists;
- current healthcheck state;
- collection timestamp;
- timestamp of the newest local PostgreSQL backup if one exists.

The write is atomic: metrics are generated into a temporary file and renamed into place only when complete.

## Install the timer on the XPS

Run from the checked-out repository location that will remain stable:

```bash
sudo bash scripts/health/install.sh
```

The installer records the absolute Compose path in `/etc/frankensteinzhermes/health.env`, installs the collector under `/usr/local/lib/frankensteinzhermes`, and enables `frankensteinz-health-collector.timer`.

Verify:

```bash
systemctl status frankensteinz-health-collector.timer
sudo systemctl start frankensteinz-health-collector.service
cat /var/lib/frankensteinzhermes/metrics/textfile/frankensteinz.prom
```

If the repository is moved later, rerun the installer from the new stable checkout path.

## Start observability

```bash
sudo docker compose -f deploy/compose/compose.yaml up -d node-exporter prometheus
```

Then:

```bash
sudo bash scripts/health/status.sh
```

The status script shows Compose state, current Prometheus `up` metrics and firing alerts without publishing Prometheus externally.

## Alert rules

The seed rules intentionally focus on failure modes that can stop autonomous operation:

- node_exporter unavailable;
- root filesystem below 10% free;
- memory below 10% available for a sustained period;
- health collector missing/stale;
- core Compose service not running;
- service Docker healthcheck unhealthy;
- PostgreSQL backup older than 25 hours once backups exist.

Notification delivery is separate from alert evaluation. Later work can route firing alerts to the owner/companion without changing the alert definitions.

## Logs

Seed services use Docker `json-file` logging with rotation:

- maximum file size: 10 MB;
- maximum files: 3 per container.

This prevents an unattended noisy service from exhausting the XPS disk. `docker compose logs` remains the seed log-query mechanism. Application services should emit structured JSON where possible; central log aggregation is deferred.

## Retention and resource budget

Prometheus is bounded to:

- 7 days retention;
- 1 GB TSDB retention size;
- 512 MB container memory;
- 0.75 CPU.

node_exporter is bounded to 128 MB / 0.25 CPU.

These limits may be adjusted from measured evidence, not by guesswork.

## CI contract

CI must:

1. validate Prometheus configuration and alert rules with `promtool`;
2. start Prometheus and node_exporter with an ephemeral textfile directory;
3. confirm both Prometheus scrape targets become healthy;
4. retain existing governance, secret, dependency, Compose and PostgreSQL recovery tests.

## Physical acceptance

B013 is not physically complete until the XPS demonstrates:

- timer runs after boot;
- textfile metrics remain fresh;
- Prometheus scrapes node_exporter;
- a deliberately injected test metric/service failure reaches the expected alert state;
- monitoring survives reboot without exposing network ports.

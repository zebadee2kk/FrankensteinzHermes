# Seed Resource Governor

The seed governor protects the first Dell XPS from new autonomous workloads exhausting the resources needed by the OS and core FrankensteinzHermes services.

It is intentionally **not** a scheduler or daemon. It is a small admission wrapper future workers call before starting resource-intensive work.

## Policy

The default policy is `config/resource-policy.yaml` and is tuned initially for an i7/32 GB Ubuntu laptop:

- heavy work requires at least 6 GiB MemAvailable;
- light work requires at least 2 GiB MemAvailable;
- root filesystem must retain at least 15 GiB free and remain below 90% used;
- heavy work is refused above the configured load-per-logical-CPU threshold;
- heavy work is refused above the configured swap-used threshold;
- thermal pressure is checked when Linux thermal-zone telemetry exists;
- at most two heavy guarded jobs may execute concurrently.

These thresholds are configuration, not code, so real-XPS evidence can tune them later.

## Commands

Inspect admission state:

```bash
python3 scripts/governor/governor.py status --class heavy
```

Prometheus representation:

```bash
python3 scripts/governor/governor.py status --class heavy --format prometheus
```

Write an atomic textfile metric snapshot to the configured node_exporter textfile directory:

```bash
sudo python3 scripts/governor/governor.py status --class heavy --write-metrics
```

Run a heavy command through admission and concurrency control:

```bash
python3 scripts/governor/governor.py run --class heavy -- <command> [args...]
```

Exit code `75` means the job was not admitted and should be deferred/requeued rather than treated as an application failure. Child command exit codes otherwise pass through unchanged.

## Concurrency

Heavy slots use non-blocking advisory file locks beneath `/run/frankensteinzhermes/governor`. A crashed process releases its lock automatically when its file descriptor closes, avoiding a persistent slot ledger that can become stale.

## Safety behaviour

The governor only decides whether **new** work starts. It does not automatically kill PostgreSQL, Hermes, monitoring, or an already-running job. Emergency termination belongs to a later explicit workload-supervision policy.

No autonomous worker should bypass the governor for work classified as heavy once the engineering factory is enabled.

## Metrics

The governor can export:

- `fzh_governor_admit{class=...}`
- `fzh_governor_memory_available_bytes`
- `fzh_governor_root_disk_free_bytes`
- `fzh_governor_root_disk_used_percent`
- `fzh_governor_load1`
- `fzh_governor_swap_used_percent`
- `fzh_governor_max_temperature_celsius` when available
- `fzh_governor_snapshot_timestamp_seconds`

These are derived operational signals and are not canonical state.

## CI test hooks

`FZH_TEST_*` environment variables exist solely to make admission decisions deterministic in tests. They must not be set in normal production service environments.

## Rollback

The governor has no persistent application data. Rollback consists of reverting its code/policy and ceasing to invoke the wrapper. Lock files are disposable. Removing governor metrics does not affect canonical state.

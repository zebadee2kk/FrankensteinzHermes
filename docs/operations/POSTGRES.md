# PostgreSQL Durable State

PostgreSQL is the first canonical durable state service in FrankensteinzHermes. B012 intentionally defines migration, backup and restore behavior before higher-level services are allowed to depend on it.

## Version and container layout

The seed candidate uses PostgreSQL `18.6`.

PostgreSQL 18+ changed the Docker Official Image data layout. The persistent volume is mounted at `/var/lib/postgresql`, while PostgreSQL 18 uses a version-specific `PGDATA` below that path. Do not change the mount back to the pre-18 `/var/lib/postgresql/data` convention.

The service publishes no host port and is reachable only on the internal Compose network.

## Secret provisioning

The database password is never stored in Git or in the Compose file.

Before first start on the XPS:

```bash
sudo install -d -m 0700 /etc/frankensteinzhermes/secrets
openssl rand -base64 36 | sudo tee /etc/frankensteinzhermes/secrets/postgres_password >/dev/null
sudo chmod 0400 /etc/frankensteinzhermes/secrets/postgres_password
sudo chown root:root /etc/frankensteinzhermes/secrets/postgres_password
```

The default path can be overridden with `FZH_POSTGRES_PASSWORD_FILE` for CI/test environments.

## Start and verify

```bash
sudo docker compose -f deploy/compose/compose.yaml up -d postgres
sudo docker compose -f deploy/compose/compose.yaml ps postgres
```

Wait until the service is healthy before migrations or backup.

## Migrations

Migrations live under `db/migrations` and use a four-digit ordered prefix, for example `0001_core.sql`.

Run:

```bash
sudo -E bash scripts/postgres/migrate.sh
```

The runner maintains `fzh_meta.schema_migrations` with the migration version and SHA-256 checksum. Re-running a migration with the same checksum is a no-op. Changing an already-applied migration causes the runner to fail; create a new migration instead.

Each new migration and its ledger insertion execute in one PostgreSQL transaction.

## Backup

Default backup location:

`/var/lib/frankensteinzhermes/backups/postgres`

Run:

```bash
sudo -E bash scripts/postgres/backup.sh
```

The script:

1. verifies PostgreSQL readiness;
2. creates a custom-format `pg_dump` archive with restrictive permissions;
3. asks `pg_restore` to parse the archive before accepting it;
4. atomically promotes the partial file;
5. writes a SHA-256 sidecar.

A backup is not considered proven until the restore test succeeds.

## Isolated restore test

```bash
sudo -E bash scripts/postgres/restore-test.sh /var/lib/frankensteinzhermes/backups/postgres/<backup>.dump
```

The restore test starts the `postgres-restore-test` Compose profile using disposable tmpfs database state. It restores the archive, verifies the migration ledger and the core schema marker, then removes the disposable container. It never issues `docker compose down` and therefore does not stop or modify the production PostgreSQL service.

## Seed recovery objectives

Initial seed targets:

- **RPO:** 24 hours once scheduled backups are enabled.
- **RTO:** 2 hours for a single-node recovery using a known-good backup and repository checkout.

These are bootstrap objectives, not final architecture targets. B013+ monitoring and later off-host backup work should tighten and validate them.

## Storage placement

Live PostgreSQL data belongs on the XPS local SSD. Do not place PostgreSQL's live data directory on the QNAP/NFS/SMB filesystem. The NAS can later receive encrypted backup copies.

## Backup retention and off-host copies

B012 proves local backup/restore mechanics. Before the machine becomes materially autonomous, later work must add:

- automatic backup schedule;
- retention policy;
- encrypted off-host copy;
- restore drills from the off-host copy;
- alerting on missed/failed backups.

## Upgrade policy

Stay on a supported PostgreSQL major release and keep its minor release current after staging tests. Major-version upgrades require a separately planned migration/rollback procedure; they are not routine unattended dependency bumps.

## Recovery outline

For host loss or volume corruption:

1. stop dependent writers;
2. preserve the damaged volume for forensic/recovery purposes if possible;
3. provision a fresh PostgreSQL service using the known-good Compose definition;
4. restore the latest verified backup;
5. run migrations;
6. execute application/database verification;
7. reopen writers only after evidence is recorded.

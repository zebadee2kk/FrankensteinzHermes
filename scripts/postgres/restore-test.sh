#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"
DB_USER="${FZH_POSTGRES_USER:-frankensteinz}"
DB_NAME="${FZH_POSTGRES_DB:-frankensteinz}"
BACKUP="${1:-}"

compose() {
  docker compose -f "$COMPOSE_FILE" --profile restore-test "$@"
}

if [[ -z "$BACKUP" || ! -f "$BACKUP" ]]; then
  echo "usage: $0 /path/to/backup.dump" >&2
  exit 2
fi

if [[ -f "${BACKUP}.sha256" ]]; then
  (
    cd "$(dirname "$BACKUP")"
    sha256sum --check "$(basename "$BACKUP").sha256"
  )
fi

cleanup() {
  compose rm -sf postgres-restore-test >/dev/null 2>&1 || true
}
trap cleanup EXIT

compose rm -sf postgres-restore-test >/dev/null 2>&1 || true
compose up -d postgres-restore-test

container_id=""
for _ in $(seq 1 60); do
  container_id="$(compose ps -q postgres-restore-test)"
  if [[ -n "$container_id" ]]; then
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
    if [[ "$status" == "healthy" ]]; then
      break
    fi
    if [[ "$status" == "unhealthy" ]]; then
      echo "restore-test PostgreSQL became unhealthy" >&2
      compose logs postgres-restore-test >&2 || true
      exit 1
    fi
  fi
  sleep 1
done

if [[ -z "$container_id" ]] || [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")" != "healthy" ]]; then
  echo "restore-test PostgreSQL did not become healthy" >&2
  compose logs postgres-restore-test >&2 || true
  exit 1
fi

echo "restoring backup into disposable PostgreSQL instance"
compose exec -T postgres-restore-test sh -c \
  'exec pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  < "$BACKUP"

migration_count="$(compose exec -T postgres-restore-test psql --quiet --tuples-only --no-align \
  --username="$DB_USER" --dbname="$DB_NAME" \
  --command='SELECT count(*) FROM fzh_meta.schema_migrations;' | tr -d '[:space:]')"

metadata_count="$(compose exec -T postgres-restore-test psql --quiet --tuples-only --no-align \
  --username="$DB_USER" --dbname="$DB_NAME" \
  --command="SELECT count(*) FROM fzh.system_metadata WHERE key = 'schema';" | tr -d '[:space:]')"

if [[ ! "$migration_count" =~ ^[1-9][0-9]*$ ]]; then
  echo "restored migration ledger is missing or empty" >&2
  exit 1
fi

if [[ "$metadata_count" != "1" ]]; then
  echo "restored core metadata verification failed" >&2
  exit 1
fi

echo "isolated PostgreSQL restore test passed (migrations=$migration_count)"

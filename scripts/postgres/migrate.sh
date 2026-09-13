#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"
MIGRATIONS_DIR="$ROOT_DIR/db/migrations"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "docker compose v2 is required" >&2
  exit 1
fi

if [[ ! -d "$MIGRATIONS_DIR" ]]; then
  echo "migration directory missing: $MIGRATIONS_DIR" >&2
  exit 1
fi

DB_USER="${FZH_POSTGRES_USER:-frankensteinz}"
DB_NAME="${FZH_POSTGRES_DB:-frankensteinz}"

if ! compose exec -T postgres pg_isready --username="$DB_USER" --dbname="$DB_NAME" >/dev/null 2>&1; then
  echo "postgres service is not ready" >&2
  exit 1
fi

cat <<'SQL' | compose exec -T postgres psql --set=ON_ERROR_STOP=1 --username="$DB_USER" --dbname="$DB_NAME"
CREATE SCHEMA IF NOT EXISTS fzh_meta;
CREATE TABLE IF NOT EXISTS fzh_meta.schema_migrations (
    version text PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

shopt -s nullglob
migrations=("$MIGRATIONS_DIR"/*.sql)
if (( ${#migrations[@]} == 0 )); then
  echo "no migrations found" >&2
  exit 1
fi

for migration in "${migrations[@]}"; do
  filename="$(basename "$migration")"
  if [[ ! "$filename" =~ ^[0-9]{4}_[a-z0-9_]+\.sql$ ]]; then
    echo "invalid migration filename: $filename" >&2
    exit 1
  fi

  version="${filename%.sql}"
  checksum="$(sha256sum "$migration" | awk '{print $1}')"
  if [[ ! "$checksum" =~ ^[0-9a-f]{64}$ ]]; then
    echo "invalid SHA-256 checksum generated for $filename" >&2
    exit 1
  fi

  # version and checksum are safe to embed because both are strictly validated above.
  existing="$(compose exec -T postgres psql --quiet --tuples-only --no-align \
    --username="$DB_USER" --dbname="$DB_NAME" \
    --command="SELECT checksum FROM fzh_meta.schema_migrations WHERE version = '$version';" | tr -d '[:space:]')"

  if [[ -n "$existing" ]]; then
    if [[ "$existing" != "$checksum" ]]; then
      echo "migration checksum mismatch for $version; applied=$existing current=$checksum" >&2
      exit 1
    fi
    echo "already applied: $version"
    continue
  fi

  echo "applying: $version"
  {
    printf '%s\n' '\set ON_ERROR_STOP on' 'BEGIN;'
    cat "$migration"
    printf "\nINSERT INTO fzh_meta.schema_migrations(version, checksum) VALUES ('%s', '%s');\n" "$version" "$checksum"
    printf '%s\n' 'COMMIT;'
  } | compose exec -T postgres psql --username="$DB_USER" --dbname="$DB_NAME"
done

echo "database migrations are current"

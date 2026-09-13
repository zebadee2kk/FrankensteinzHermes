#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"
BACKUP_DIR="${FZH_BACKUP_DIR:-/var/lib/frankensteinzhermes/backups/postgres}"
DB_USER="${FZH_POSTGRES_USER:-frankensteinz}"
DB_NAME="${FZH_POSTGRES_DB:-frankensteinz}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "docker compose v2 is required" >&2
  exit 1
fi

if ! compose exec -T postgres pg_isready --username="$DB_USER" --dbname="$DB_NAME" >/dev/null 2>&1; then
  echo "postgres service is not ready" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$BACKUP_DIR/frankensteinzhermes-${timestamp}.dump"
tmp="${final}.partial"
trap 'rm -f "$tmp"' EXIT

echo "creating PostgreSQL custom-format backup" >&2
compose exec -T postgres sh -c \
  'exec pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  > "$tmp"

if [[ ! -s "$tmp" ]]; then
  echo "backup is empty" >&2
  exit 1
fi

# Validate that PostgreSQL can parse the archive before it is promoted as a backup.
compose exec -T postgres pg_restore --list < "$tmp" >/dev/null

mv "$tmp" "$final"
trap - EXIT
(
  cd "$BACKUP_DIR"
  sha256sum "$(basename "$final")" > "$(basename "$final").sha256"
)

echo "backup created and archive-validated: $final" >&2
printf '%s\n' "$final"

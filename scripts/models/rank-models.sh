#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"
TASK_ROLE="${1:-}"
CLASSIFICATION="${2:-}"
HARDWARE_FINGERPRINT="${3:-}"

[[ "$TASK_ROLE" =~ ^[a-z][a-z0-9_-]{0,63}$ ]] || {
  echo "task role must match ^[a-z][a-z0-9_-]{0,63}$" >&2
  exit 2
}

case "$CLASSIFICATION" in
  PUBLIC|INTERNAL|CONFIDENTIAL|SECRET) ;;
  *) echo "classification must be PUBLIC, INTERNAL, CONFIDENTIAL or SECRET" >&2; exit 2 ;;
esac

if [[ -n "$HARDWARE_FINGERPRINT" && ! "$HARDWARE_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]]; then
  echo "hardware fingerprint must be an empty value or a lowercase SHA-256" >&2
  exit 2
fi

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 is required" >&2; exit 1; }

DB_USER="${FZH_POSTGRES_USER:-frankensteinz}"
DB_NAME="${FZH_POSTGRES_DB:-frankensteinz}"

# psql's :'var' form SQL-quotes values, avoiding string interpolation into SQL.
docker compose -f "$COMPOSE_FILE" exec -T postgres psql \
  --quiet --tuples-only --no-align \
  --username="$DB_USER" --dbname="$DB_NAME" \
  --set=task_role="$TASK_ROLE" \
  --set=classification="$CLASSIFICATION" \
  --set=hardware="$HARDWARE_FINGERPRINT" <<'SQL'
SELECT COALESCE(json_agg(row_to_json(r)), '[]'::json)::text
FROM fzh.rank_model_candidates(
    :'task_role',
    :'classification',
    NULLIF(:'hardware', '')
) AS r;
SQL

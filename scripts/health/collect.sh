#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${FZH_COMPOSE_FILE:-}"
TEXTFILE_DIR="${FZH_TEXTFILE_DIR:-/var/lib/frankensteinzhermes/metrics/textfile}"
BACKUP_DIR="${FZH_BACKUP_DIR:-/var/lib/frankensteinzhermes/backups/postgres}"

if [[ -z "$COMPOSE_FILE" ]]; then
  ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"
fi

for command in docker jq; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 1; }
done

docker compose version >/dev/null 2>&1 || { echo "docker compose v2 is required" >&2; exit 1; }
[[ -r "$COMPOSE_FILE" ]] || { echo "Compose file is not readable: $COMPOSE_FILE" >&2; exit 1; }

install -d -m 0755 "$TEXTFILE_DIR"
tmp="$TEXTFILE_DIR/frankensteinz.prom.tmp.$$"
final="$TEXTFILE_DIR/frankensteinz.prom"
trap 'rm -f "$tmp"' EXIT

config_json="$(docker compose -f "$COMPOSE_FILE" config --format json)"
mapfile -t services < <(
  jq -r '.services | to_entries[] | select(((.value.profiles // []) | length) == 0) | .key' <<<"$config_json"
)

{
  echo '# HELP fzh_health_snapshot_timestamp_seconds Unix timestamp of the latest FrankensteinzHermes host health snapshot.'
  echo '# TYPE fzh_health_snapshot_timestamp_seconds gauge'
  printf 'fzh_health_snapshot_timestamp_seconds %s\n' "$(date +%s)"

  echo '# HELP fzh_compose_service_present Whether a non-profile Compose service has a container.'
  echo '# TYPE fzh_compose_service_present gauge'
  echo '# HELP fzh_compose_service_running Whether a non-profile Compose service container is running.'
  echo '# TYPE fzh_compose_service_running gauge'
  echo '# HELP fzh_compose_service_healthcheck_present Whether the service container defines a Docker healthcheck.'
  echo '# TYPE fzh_compose_service_healthcheck_present gauge'
  echo '# HELP fzh_compose_service_healthy Whether the Docker healthcheck is healthy. Services without a healthcheck mirror running state.'
  echo '# TYPE fzh_compose_service_healthy gauge'

  for service in "${services[@]}"; do
    cid="$(docker compose -f "$COMPOSE_FILE" ps -q "$service" 2>/dev/null || true)"
    present=0
    running=0
    healthcheck_present=0
    healthy=0

    if [[ -n "$cid" ]]; then
      present=1
      if [[ "$(docker inspect --format '{{.State.Running}}' "$cid" 2>/dev/null || true)" == "true" ]]; then
        running=1
      fi
      health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo none)"
      if [[ "$health_status" != "none" && -n "$health_status" ]]; then
        healthcheck_present=1
        [[ "$health_status" == "healthy" ]] && healthy=1
      else
        healthy="$running"
      fi
    fi

    printf 'fzh_compose_service_present{service="%s"} %d\n' "$service" "$present"
    printf 'fzh_compose_service_running{service="%s"} %d\n' "$service" "$running"
    printf 'fzh_compose_service_healthcheck_present{service="%s"} %d\n' "$service" "$healthcheck_present"
    printf 'fzh_compose_service_healthy{service="%s"} %d\n' "$service" "$healthy"
  done

  echo '# HELP fzh_postgres_backup_last_success_timestamp_seconds Unix modification time of the newest local PostgreSQL dump, or 0 when none exists.'
  echo '# TYPE fzh_postgres_backup_last_success_timestamp_seconds gauge'
  newest_backup=0
  if [[ -d "$BACKUP_DIR" ]]; then
    newest_backup="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.dump' -printf '%T@\n' 2>/dev/null | sort -nr | head -n1 | cut -d. -f1 || true)"
    [[ "$newest_backup" =~ ^[0-9]+$ ]] || newest_backup=0
  fi
  printf 'fzh_postgres_backup_last_success_timestamp_seconds %s\n' "$newest_backup"
} > "$tmp"

chmod 0644 "$tmp"
mv -f "$tmp" "$final"
trap - EXIT

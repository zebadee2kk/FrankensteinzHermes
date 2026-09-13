#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${FZH_COMPOSE_FILE:-$ROOT_DIR/deploy/compose/compose.yaml}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

compose ps

echo
echo "Prometheus targets:"
compose exec -T prometheus promtool query instant http://localhost:9090 'up' || {
  echo "Prometheus query failed" >&2
  exit 1
}

echo
echo "Firing alerts:"
compose exec -T prometheus promtool query instant http://localhost:9090 'ALERTS{alertstate="firing"}' || {
  echo "Prometheus alert query failed" >&2
  exit 1
}

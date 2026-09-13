#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose v2 is required" >&2
  exit 1
fi

echo "Validating Compose configuration..."
docker compose -f "$COMPOSE_FILE" config --quiet

if grep -R --line-number -E '/var/run/docker\.sock|privileged:[[:space:]]*true' "$ROOT_DIR/deploy/compose"; then
  echo "Unsafe Docker socket mount or privileged container detected" >&2
  exit 1
fi

if grep -R --line-number -E 'image:[[:space:]]*[^#]*:latest([[:space:]]|$)' "$ROOT_DIR/deploy/compose"; then
  echo "Unpinned latest image tag detected" >&2
  exit 1
fi

echo "Compose static verification passed"

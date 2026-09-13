#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${FZH_COMPOSE_FILE:-deploy/compose/compose.yaml}"

if ! docker compose -f "$COMPOSE_FILE" --profile local-ai ps --status running ollama | grep -q ollama; then
  echo "Ollama service is not running" >&2
  exit 1
fi

version_output="$(docker compose -f "$COMPOSE_FILE" --profile local-ai exec -T ollama ollama --version 2>&1)"
printf '%s\n' "$version_output"
grep -q '0.34.0' <<<"$version_output" || {
  echo "Unexpected Ollama version; expected 0.34.0" >&2
  exit 1
}

docker compose -f "$COMPOSE_FILE" --profile local-ai exec -T ollama ollama list >/dev/null

published="$(docker compose -f "$COMPOSE_FILE" --profile local-ai port ollama 11434 2>/dev/null || true)"
if [[ -n "$published" ]]; then
  echo "Ollama must not publish a host port; found: $published" >&2
  exit 1
fi

echo "Ollama runtime verification passed"

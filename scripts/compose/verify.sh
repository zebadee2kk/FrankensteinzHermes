#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"
DOC_FILE="$ROOT_DIR/docs/operations/COMPOSE-RUNTIME.md"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose v2 is required" >&2
  exit 1
fi
if [[ ! -s "$DOC_FILE" ]]; then
  echo "Compose runtime documentation is required" >&2
  exit 1
fi

# `docker compose config` resolves env_file paths. Static CI does not have the
# production LiteLLM secret, so provide an ephemeral non-secret placeholder only
# for schema rendering. Runtime startup still requires the real local file.
tmp_env=""
if [[ -z "${FZH_LITELLM_ENV_FILE:-}" ]]; then
  tmp_env="$(mktemp)"
  printf 'LITELLM_MASTER_KEY=static-verification-placeholder\n' > "$tmp_env"
  export FZH_LITELLM_ENV_FILE="$tmp_env"
fi
trap '[[ -n "$tmp_env" ]] && rm -f "$tmp_env"' EXIT

echo "Validating Compose configuration..."
rendered="$(mktemp)"
trap 'rm -f "$rendered"; [[ -n "$tmp_env" ]] && rm -f "$tmp_env"' EXIT
docker compose -f "$COMPOSE_FILE" config --format json > "$rendered"

if grep -R --line-number -E '/var/run/docker\.sock|privileged:[[:space:]]*true' "$ROOT_DIR/deploy/compose"; then
  echo "Unsafe Docker socket mount or privileged container detected" >&2
  exit 1
fi
if grep -R --line-number -E 'image:[[:space:]]*[^#]*:latest([[:space:]]|$)' "$ROOT_DIR/deploy/compose"; then
  echo "Unpinned latest image tag detected" >&2
  exit 1
fi

python3 - "$rendered" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding='utf-8'))
for service, data in cfg.get('services', {}).items():
    for port in data.get('ports') or []:
        allowed = (
            service == 'litellm'
            and str(port.get('host_ip')) == '127.0.0.1'
            and int(port.get('target')) == 4000
        )
        if not allowed:
            raise SystemExit(f"Published port outside approved loopback LiteLLM boundary: {service}: {port}")
PY

if ! grep -q 'healthcheck:' "$COMPOSE_FILE"; then
  echo "At least one healthcheck is required in the seed runtime" >&2
  exit 1
fi
if grep -q '/var/lib/postgresql/data' "$COMPOSE_FILE"; then
  echo "PostgreSQL 18+ must persist /var/lib/postgresql, not the pre-18 /var/lib/postgresql/data path" >&2
  exit 1
fi
if grep -Eq '^[[:space:]]+POSTGRES_PASSWORD:[[:space:]]' "$COMPOSE_FILE"; then
  echo "Inline POSTGRES_PASSWORD is forbidden; use POSTGRES_PASSWORD_FILE" >&2
  exit 1
fi
if grep -q 'image: postgres:' "$COMPOSE_FILE" && ! grep -q 'postgres_data:/var/lib/postgresql' "$COMPOSE_FILE"; then
  echo "PostgreSQL service must mount its durable volume at /var/lib/postgresql" >&2
  exit 1
fi

echo "Compose static verification passed"

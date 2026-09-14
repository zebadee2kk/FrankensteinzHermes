#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"
BUNDLE="${1:-}"

[[ -n "$BUNDLE" ]] || { echo "usage: $0 <b025-evidence-bundle>" >&2; exit 2; }
[[ -d "$BUNDLE" ]] || { echo "B025 evidence bundle not found: $BUNDLE" >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 is required" >&2; exit 1; }

DB_USER="${FZH_POSTGRES_USER:-frankensteinz}"
DB_NAME="${FZH_POSTGRES_DB:-frankensteinz}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

if ! compose exec -T postgres pg_isready --username="$DB_USER" --dbname="$DB_NAME" >/dev/null 2>&1; then
  echo "postgres service is not ready" >&2
  exit 1
fi

# Generate the CSV before opening the DB write path. The preparer validates the
# B025 schema and promotion_authorized=false contract.
tmp_csv="$(mktemp)"
trap 'rm -f "$tmp_csv"' EXIT
python3 "$ROOT_DIR/scripts/models/prepare-llmfit-evidence-import.py" "$BUNDLE" > "$tmp_csv"

# COPY into a temporary staging table, then insert idempotently into the
# evidence inbox. No statement here can write model_candidates or provider
# approval/promotion state.
{
  cat <<'SQL'
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE fzh_llmfit_import (
    source_system text,
    source_version text,
    use_case text,
    source_candidate_name text,
    hardware_fingerprint text,
    synthetic boolean,
    linked_candidate_key text,
    evidence_ref text,
    evidence_sha256 text,
    source_payload jsonb,
    observed_at timestamptz
) ON COMMIT DROP;
\copy fzh_llmfit_import FROM STDIN WITH (FORMAT csv, HEADER true)
SQL
  cat "$tmp_csv"
  cat <<'SQL'
\.
INSERT INTO fzh.model_candidate_evidence (
    source_system, source_version, use_case, source_candidate_name,
    hardware_fingerprint, synthetic, linked_candidate_key,
    evidence_ref, evidence_sha256, source_payload, observed_at
)
SELECT
    source_system, source_version, use_case, source_candidate_name,
    NULLIF(hardware_fingerprint, ''), synthetic, NULLIF(linked_candidate_key, ''),
    evidence_ref, evidence_sha256, source_payload, observed_at
FROM fzh_llmfit_import
ON CONFLICT (source_system, evidence_sha256, use_case, source_candidate_name)
DO NOTHING;
COMMIT;
SQL
} | compose exec -T postgres psql --username="$DB_USER" --dbname="$DB_NAME"

printf 'B025 llmfit evidence imported into non-promoting inbox: %s\n' "$BUNDLE"

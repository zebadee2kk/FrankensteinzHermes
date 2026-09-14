#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose/compose.yaml"
DB_USER="${FZH_POSTGRES_USER:-frankensteinz}"
DB_NAME="${FZH_POSTGRES_DB:-frankensteinz}"

usage() {
  cat <<'EOF'
Usage:
  ledger.sh submit KIND IDEMPOTENCY_KEY PAYLOAD_JSON_FILE SIDE_EFFECTING RISK ACTION CLASSIFICATION [MAX_ATTEMPTS] [PRIORITY]
  ledger.sh lease WORKER_ID [LEASE_SECONDS] [JOB_KIND]
  ledger.sh show JOB_ID
  ledger.sh events JOB_ID
  ledger.sh reap

This is an operator/CI wrapper around the canonical PostgreSQL functions.
It is not the future Hermes runtime credential path and does not grant Docker
or database authority to fzh-hermes.
EOF
}

need_docker() {
  command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 2; }
  docker compose version >/dev/null 2>&1 || { echo "docker compose v2 is required" >&2; exit 2; }
}

psql_exec() {
  docker compose -f "$COMPOSE_FILE" exec -T postgres \
    psql --set=ON_ERROR_STOP=1 --quiet --no-align --tuples-only \
    --username="$DB_USER" --dbname="$DB_NAME" "$@"
}

need_docker
cmd="${1:-}"
shift || true

case "$cmd" in
  submit)
    [[ $# -ge 7 && $# -le 9 ]] || { usage >&2; exit 2; }
    kind="$1"; idem="$2"; payload_file="$3"; side_effecting="$4"; risk="$5"; action="$6"; classification="$7"
    max_attempts="${8:-3}"; priority="${9:-100}"
    [[ -f "$payload_file" ]] || { echo "payload file not found: $payload_file" >&2; exit 2; }
    python3 -m json.tool "$payload_file" >/dev/null
    payload="$(cat "$payload_file")"
    psql_exec \
      --set=kind="$kind" --set=idem="$idem" --set=payload="$payload" \
      --set=side_effecting="$side_effecting" --set=risk="$risk" --set=action="$action" \
      --set=classification="$classification" --set=max_attempts="$max_attempts" --set=priority="$priority" <<'SQL'
SELECT row_to_json(j)
FROM fzh.submit_job(
    :'kind', :'idem', :'payload'::jsonb, :'side_effecting'::boolean,
    :'risk', :'action', :'classification', :'max_attempts'::integer,
    :'priority'::integer, now(), 'operator-cli'
) AS j;
SQL
    ;;

  lease)
    [[ $# -ge 1 && $# -le 3 ]] || { usage >&2; exit 2; }
    worker="$1"; seconds="${2:-300}"; kind="${3:-}"
    psql_exec --set=worker="$worker" --set=seconds="$seconds" --set=kind="$kind" <<'SQL'
SELECT row_to_json(j)
FROM fzh.lease_next_job(:'worker', :'seconds'::integer, NULLIF(:'kind','')) AS j;
SQL
    ;;

  show)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    psql_exec --set=job_id="$1" <<'SQL'
SELECT row_to_json(j) FROM fzh.jobs j WHERE j.job_id = :'job_id'::uuid;
SQL
    ;;

  events)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    psql_exec --set=job_id="$1" <<'SQL'
SELECT row_to_json(e)
FROM fzh.job_events e
WHERE e.job_id = :'job_id'::uuid
ORDER BY e.event_id;
SQL
    ;;

  reap)
    [[ $# -eq 0 ]] || { usage >&2; exit 2; }
    psql_exec <<'SQL'
SELECT row_to_json(r) FROM fzh.reap_expired_jobs('operator-cli') AS r;
SQL
    ;;

  *)
    usage >&2
    exit 2
    ;;
esac

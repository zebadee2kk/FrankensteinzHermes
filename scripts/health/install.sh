#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${FZH_COMPOSE_FILE:-$ROOT_DIR/deploy/compose/compose.yaml}"

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

if [[ "$COMPOSE_FILE" == *$'\n'* || "$COMPOSE_FILE" == *' '* ]]; then
  echo "Compose path containing whitespace is not supported by the seed systemd environment file" >&2
  exit 1
fi

[[ -r "$COMPOSE_FILE" ]] || { echo "Compose file not readable: $COMPOSE_FILE" >&2; exit 1; }

install -d -m 0755 /usr/local/lib/frankensteinzhermes
install -d -m 0750 /etc/frankensteinzhermes
install -d -m 0755 /var/lib/frankensteinzhermes/metrics/textfile

install -m 0755 "$ROOT_DIR/scripts/health/collect.sh" /usr/local/lib/frankensteinzhermes/collect-health.sh
install -m 0644 "$ROOT_DIR/deploy/systemd/frankensteinz-health-collector.service" /etc/systemd/system/frankensteinz-health-collector.service
install -m 0644 "$ROOT_DIR/deploy/systemd/frankensteinz-health-collector.timer" /etc/systemd/system/frankensteinz-health-collector.timer

cat > /etc/frankensteinzhermes/health.env <<EOF
FZH_COMPOSE_FILE=$COMPOSE_FILE
FZH_TEXTFILE_DIR=/var/lib/frankensteinzhermes/metrics/textfile
FZH_BACKUP_DIR=/var/lib/frankensteinzhermes/backups/postgres
EOF
chmod 0640 /etc/frankensteinzhermes/health.env

systemctl daemon-reload
systemctl enable --now frankensteinz-health-collector.timer
systemctl start frankensteinz-health-collector.service

if [[ ! -s /var/lib/frankensteinzhermes/metrics/textfile/frankensteinz.prom ]]; then
  echo "health collector did not produce metrics" >&2
  exit 1
fi

systemctl --no-pager status frankensteinz-health-collector.timer || true
echo "FrankensteinzHermes health collector installed"

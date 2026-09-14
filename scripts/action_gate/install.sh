#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERMES_USER="${FZH_HERMES_USER:-fzh-hermes}"
GATE_USER="fzh-gate"
CLIENT_GROUP="fzh-gate-clients"
LIBEXEC_DIR="/usr/local/libexec/frankensteinzhermes"
ETC_DIR="/etc/frankensteinzhermes"
UNIT_PATH="/etc/systemd/system/fzh-action-gate.service"

[[ "$EUID" -eq 0 ]] || { echo "run as root/sudo" >&2; exit 2; }
[[ "$(uname -s)" == "Linux" ]] || { echo "Linux required" >&2; exit 2; }
id "$HERMES_USER" >/dev/null 2>&1 || { echo "Hermes user missing: $HERMES_USER" >&2; exit 2; }

if ! getent group "$CLIENT_GROUP" >/dev/null 2>&1; then
  groupadd --system "$CLIENT_GROUP"
fi
if ! id "$GATE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$GATE_USER"
fi

usermod -a -G "$CLIENT_GROUP" "$HERMES_USER"
usermod -a -G "$CLIENT_GROUP" "$GATE_USER"

install -d -o root -g root -m 0755 "$LIBEXEC_DIR" "$ETC_DIR"
install -o root -g root -m 0755 "$ROOT_DIR/scripts/action_gate/action_gate.py" "$LIBEXEC_DIR/action_gate.py"
install -o root -g root -m 0755 "$ROOT_DIR/scripts/action_gate/server.py" "$LIBEXEC_DIR/action-gate-server.py"
install -o root -g root -m 0755 "$ROOT_DIR/scripts/action_gate/client.py" /usr/local/bin/fzh-action-gate
install -o root -g root -m 0644 "$ROOT_DIR/policy/action-gate.json" "$ETC_DIR/action-gate.json"
install -o root -g root -m 0644 "$ROOT_DIR/deploy/systemd/fzh-action-gate.service" "$UNIT_PATH"

# Never remove or replace an active kill switch during install/upgrade.
# Operators control it only with root privileges:
#   touch /etc/frankensteinzhermes/action-gate.kill
#   rm    /etc/frankensteinzhermes/action-gate.kill

systemctl daemon-reload
systemctl enable --now fzh-action-gate.service

printf '%s\n' \
  "Action Gate installed and started." \
  "Restart long-lived $HERMES_USER processes before relying on new $CLIENT_GROUP membership." \
  "Run: bash scripts/action_gate/verify.sh"

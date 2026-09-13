#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIN_FILE="${FZH_HERMES_PIN_FILE:-$ROOT_DIR/config/hermes-upstream.yaml}"

[[ "$(uname -s)" == "Linux" ]] || { echo "Hermes seed install supports Linux only" >&2; exit 2; }
[[ -r /etc/os-release ]] || { echo "Cannot identify OS" >&2; exit 2; }
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || { echo "Expected Ubuntu; found ${ID:-unknown}" >&2; exit 2; }
[[ "$EUID" -eq 0 ]] || { echo "Run with sudo/root; installer creates a dedicated unprivileged identity" >&2; exit 2; }

field() {
  local key="$1"
  awk -F': ' -v key="$key" '$1 == key {print $2; exit}' "$PIN_FILE"
}

COMMIT="$(field commit)"
RAW_BASE="$(field raw_base)"
HERMES_USER="${FZH_HERMES_USER:-fzh-hermes}"
HERMES_USER_HOME="${FZH_HERMES_USER_HOME:-/var/lib/frankensteinzhermes/hermes-user}"
HERMES_HOME="${FZH_HERMES_HOME:-$HERMES_USER_HOME/.hermes}"
INSTALL_DIR="${FZH_HERMES_INSTALL_DIR:-$HERMES_USER_HOME/hermes-agent}"

[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid pinned Hermes commit: $COMMIT" >&2; exit 2; }
[[ "$RAW_BASE" == "https://raw.githubusercontent.com/NousResearch/hermes-agent" ]] || {
  echo "Unexpected Hermes raw source: $RAW_BASE" >&2; exit 2;
}

if ! id "$HERMES_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$HERMES_USER_HOME" --shell /bin/bash "$HERMES_USER"
fi

install -d -o "$HERMES_USER" -g "$HERMES_USER" -m 0750 "$HERMES_USER_HOME" "$HERMES_HOME"

# Refuse accidental privilege inheritance.
if id -nG "$HERMES_USER" | tr ' ' '\n' | grep -Eq '^(sudo|docker)$'; then
  echo "$HERMES_USER must not belong to sudo or docker groups" >&2
  exit 3
fi

installer="$(mktemp)"
trap 'rm -f "$installer"' EXIT
curl --fail --silent --show-error --location \
  "$RAW_BASE/$COMMIT/scripts/install.sh" \
  --output "$installer"
chmod 0755 "$installer"

# Sanity-check that the immutable upstream installer still exposes the controls
# on which this deployment contract relies.
for expected in '--commit' '--skip-setup' '--skip-browser' '--skip-computer-use' '--dir' '--hermes-home'; do
  grep -q -- "$expected" "$installer" || { echo "Pinned installer lacks required option $expected" >&2; exit 4; }
done

sudo -H -u "$HERMES_USER" env \
  HOME="$HERMES_USER_HOME" \
  HERMES_HOME="$HERMES_HOME" \
  HERMES_INSTALL_DIR="$INSTALL_DIR" \
  bash "$installer" \
    --commit "$COMMIT" \
    --force-commit \
    --skip-setup \
    --skip-browser \
    --skip-computer-use \
    --non-interactive \
    --dir "$INSTALL_DIR" \
    --hermes-home "$HERMES_HOME"

chown -R "$HERMES_USER:$HERMES_USER" "$HERMES_USER_HOME"

echo "Hermes pinned install complete: $COMMIT"
echo "Next: provision provider configuration out-of-band, run verification, then explicitly install/start the gateway service."

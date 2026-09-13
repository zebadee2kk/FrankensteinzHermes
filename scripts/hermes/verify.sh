#!/usr/bin/env bash
set -euo pipefail

HERMES_USER="${FZH_HERMES_USER:-fzh-hermes}"
HERMES_USER_HOME="${FZH_HERMES_USER_HOME:-/var/lib/frankensteinzhermes/hermes-user}"
HERMES_HOME="${FZH_HERMES_HOME:-$HERMES_USER_HOME/.hermes}"
INSTALL_DIR="${FZH_HERMES_INSTALL_DIR:-$HERMES_USER_HOME/hermes-agent}"

id "$HERMES_USER" >/dev/null 2>&1 || { echo "Missing Hermes user: $HERMES_USER" >&2; exit 1; }
[[ -d "$INSTALL_DIR/.git" ]] || { echo "Hermes install checkout missing: $INSTALL_DIR" >&2; exit 1; }
[[ -d "$HERMES_HOME" ]] || { echo "Hermes state home missing: $HERMES_HOME" >&2; exit 1; }

if id -nG "$HERMES_USER" | tr ' ' '\n' | grep -Eq '^(sudo|docker)$'; then
  echo "FAIL: $HERMES_USER has forbidden sudo/docker group membership" >&2
  exit 1
fi

actual_commit="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
expected_commit="$(awk -F': ' '$1 == "commit" {print $2; exit}' "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/config/hermes-upstream.yaml")"
[[ "$actual_commit" == "$expected_commit" ]] || {
  echo "FAIL: installed Hermes commit $actual_commit != pin $expected_commit" >&2
  exit 1
}

sudo -H -u "$HERMES_USER" env \
  HOME="$HERMES_USER_HOME" HERMES_HOME="$HERMES_HOME" \
  PATH="$HERMES_USER_HOME/.local/bin:$HERMES_HOME/bin:/usr/local/bin:/usr/bin:/bin" \
  bash -lc 'command -v hermes >/dev/null && hermes doctor'

echo "Hermes verification passed at $actual_commit"

#!/usr/bin/env bash
set -euo pipefail

# Install the B034 QA sandbox policy boundary. Run as root and pass the
# unprivileged service account that will execute the QA worker/adapter.
if [[ "${EUID}" -ne 0 ]]; then
  echo "run as root" >&2
  exit 2
fi

qa_user="${1:-}"
if [[ -z "$qa_user" ]] || ! id "$qa_user" >/dev/null 2>&1; then
  echo "usage: $0 <qa-service-user>" >&2
  exit 2
fi
qa_group="$(id -gn "$qa_user")"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

for binary in bwrap systemd-run systemctl; do
  command -v "$binary" >/dev/null 2>&1 || {
    echo "required runtime missing: $binary" >&2
    exit 3
  }
done

install -d -o root -g root -m 0755 /etc/frankensteinzhermes
install -o root -g root -m 0644 \
  "$repo_root/config/qa-profiles.example.json" \
  /etc/frankensteinzhermes/qa-profiles.json

install -d -o root -g root -m 0755 /var/lib/frankensteinzhermes
install -d -o "$qa_user" -g "$qa_group" -m 0700 \
  /var/lib/frankensteinzhermes/qa-workspaces
install -d -o "$qa_user" -g "$qa_group" -m 0700 \
  /var/lib/frankensteinzhermes/qa-output

# Refuse insecure policy ownership/mode even if install semantics change.
owner="$(stat -c '%u' /etc/frankensteinzhermes/qa-profiles.json)"
mode="$(stat -c '%a' /etc/frankensteinzhermes/qa-profiles.json)"
[[ "$owner" == "0" ]]
case "$mode" in
  644|640|600) ;;
  *) echo "unexpected QA profile mode: $mode" >&2; exit 4 ;;
esac

echo "B034 sandbox policy installed for unprivileged user: $qa_user"
echo "Ensure that user's systemd --user manager is available before starting the QA worker."

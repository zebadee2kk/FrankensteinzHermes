#!/usr/bin/env bash
set -euo pipefail

HERMES_USER="${FZH_HERMES_USER:-fzh-hermes}"
CLIENT_GROUP="fzh-gate-clients"
POLICY="/etc/frankensteinzhermes/action-gate.json"
KILL_SWITCH="/etc/frankensteinzhermes/action-gate.kill"
SOCKET="/run/frankensteinzhermes-gate/action-gate.sock"
AUDIT="/var/log/frankensteinzhermes-gate/action-gate.jsonl"
EXERCISE_KILL_SWITCH=false
[[ "${1:-}" == "--exercise-kill-switch" ]] && EXERCISE_KILL_SWITCH=true

[[ "$EUID" -eq 0 ]] || { echo "run verification as root/sudo" >&2; exit 2; }

fail() { echo "action gate verify: $*" >&2; exit 1; }

for path in \
  /usr/local/libexec/frankensteinzhermes/action_gate.py \
  /usr/local/libexec/frankensteinzhermes/action-gate-server.py \
  /usr/local/bin/fzh-action-gate \
  "$POLICY" \
  /etc/systemd/system/fzh-action-gate.service; do
  [[ -e "$path" ]] || fail "missing $path"
  owner="$(stat -c '%U' "$path")"
  mode="$(stat -c '%a' "$path")"
  [[ "$owner" == root ]] || fail "$path is not root-owned"
  # Reject group/world writable installed authority files.
  perms=$((8#$mode))
  (( (perms & 0022) == 0 )) || fail "$path is group/world writable ($mode)"
done

id -nG "$HERMES_USER" | tr ' ' '\n' | grep -qx "$CLIENT_GROUP" || fail "$HERMES_USER is not in $CLIENT_GROUP"
id -nG "$HERMES_USER" | tr ' ' '\n' | grep -Eq '^(sudo|docker)$' && fail "$HERMES_USER has forbidden sudo/docker membership"

systemctl is-enabled --quiet fzh-action-gate.service || fail "service not enabled"
systemctl is-active --quiet fzh-action-gate.service || fail "service not active"
[[ -S "$SOCKET" ]] || fail "gate socket missing"

request() {
  local payload="$1"
  local expected_rc="$2"
  local output rc
  set +e
  output="$(printf '%s\n' "$payload" | runuser -u "$HERMES_USER" -- /usr/local/bin/fzh-action-gate)"
  rc=$?
  set -e
  [[ "$rc" -eq "$expected_rc" ]] || fail "unexpected client exit $rc (expected $expected_rc): $output"
  printf '%s\n' "$output"
}

if [[ -e "$KILL_SWITCH" ]]; then
  fail "kill switch is already active; baseline allow test intentionally refuses to proceed"
fi

before=0
[[ -f "$AUDIT" ]] && before="$(wc -l < "$AUDIT")"

allow_output="$(request '{"request_id":"verify-l0","actor":"verify","action_type":"read_status","risk_level":"L0","environment":"local","data_classification":"PUBLIC","target":"health","side_effecting":false,"reversible":true,"external_side_effect":false,"uses_llm":false,"metadata":{}}' 0)"
python3 - "$allow_output" <<'PY'
import json, sys
assert json.loads(sys.argv[1])["decision"] == "allow"
PY

deny_output="$(request '{"request_id":"verify-deny","actor":"verify","action_type":"credential_export","risk_level":"L0","environment":"local","data_classification":"PUBLIC","target":"credentials","side_effecting":false,"reversible":true,"external_side_effect":false,"uses_llm":false,"metadata":{}}' 30)"
python3 - "$deny_output" <<'PY'
import json, sys
assert json.loads(sys.argv[1])["decision"] == "deny"
PY

after="$(wc -l < "$AUDIT")"
(( after >= before + 2 )) || fail "audit log did not record both verification decisions"

if [[ "$EXERCISE_KILL_SWITCH" == true ]]; then
  touch "$KILL_SWITCH"
  chmod 0644 "$KILL_SWITCH"
  chown root:root "$KILL_SWITCH"
  trap 'rm -f "$KILL_SWITCH"' EXIT
  kill_output="$(request '{"request_id":"verify-kill","actor":"verify","action_type":"read_status","risk_level":"L0","environment":"local","data_classification":"PUBLIC","target":"health","side_effecting":false,"reversible":true,"external_side_effect":false,"uses_llm":false,"metadata":{}}' 30)"
  python3 - "$kill_output" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
assert value["decision"] == "deny"
assert value["kill_switch_active"] is True
assert value["reason"] == "kill_switch_active"
PY
  rm -f "$KILL_SWITCH"
  trap - EXIT
fi

printf 'Action Gate verification passed%s\n' "$([[ "$EXERCISE_KILL_SWITCH" == true ]] && printf ' including kill switch' || true)"

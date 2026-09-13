#!/usr/bin/env bash
set -Eeuo pipefail

failures=0
warnings=0

pass() { printf '[verify] PASS: %s\n' "$*"; }
warn() { printf '[verify] WARN: %s\n' "$*"; warnings=$((warnings + 1)); }
fail() { printf '[verify] FAIL: %s\n' "$*" >&2; failures=$((failures + 1)); }

[[ -r /etc/os-release ]] || { fail "/etc/os-release missing"; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] && pass "Ubuntu detected (${VERSION_ID:-unknown})" || fail "host is not Ubuntu"

mem_kib="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
mem_gib=$(( mem_kib / 1024 / 1024 ))
(( mem_gib >= 24 )) && pass "RAM ${mem_gib} GiB meets seed floor" || fail "RAM ${mem_gib} GiB below seed floor"

root_free_kib="$(df -Pk / | awk 'NR==2 {print $4}')"
root_free_gib=$(( root_free_kib / 1024 / 1024 ))
(( root_free_gib >= 20 )) && pass "free disk ${root_free_gib} GiB" || fail "free disk ${root_free_gib} GiB below 20 GiB reserve"

for cmd in git curl jq python3 ufw docker; do
  command -v "$cmd" >/dev/null 2>&1 && pass "$cmd installed" || fail "$cmd missing"
done

docker compose version >/dev/null 2>&1 && pass "Docker Compose v2 available" || fail "Docker Compose v2 unavailable"
systemctl is-active --quiet docker.service && pass "Docker service active" || fail "Docker service not active"
systemctl is-enabled --quiet docker.service && pass "Docker service enabled at boot" || fail "Docker service not enabled"

if ufw status | head -n1 | grep -q 'Status: active'; then
  pass "UFW active"
else
  fail "UFW is not active"
fi

if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq '(^|:|\])22$'; then
  if ufw status | grep -Eq '(^|[[:space:]])22/tcp([[:space:]]|$)'; then
    pass "active SSH listener has UFW allowance"
  else
    fail "SSH listens on TCP/22 but no matching UFW allowance was found"
  fi
else
  pass "no TCP/22 SSH listener detected"
fi

[[ -f /etc/apt/apt.conf.d/20auto-upgrades ]] && pass "automatic update policy present" || fail "automatic update policy missing"

for path in /opt/frankensteinzhermes /etc/frankensteinzhermes /var/lib/frankensteinzhermes /var/log/frankensteinzhermes; do
  [[ -d "$path" ]] && pass "$path present" || fail "$path missing"
done

if systemctl --failed --no-legend --plain | grep -q .; then
  warn "systemd reports failed units; inspect with systemctl --failed"
else
  pass "no failed systemd units"
fi

if swapon --show --noheadings 2>/dev/null | grep -q .; then
  pass "swap is configured"
else
  warn "no swap configured; heavy local-model workloads may fail abruptly under memory pressure"
fi

if command -v sensors >/dev/null 2>&1; then
  pass "lm-sensors available"
else
  warn "lm-sensors unavailable"
fi

printf '[verify] SUMMARY: %d failure(s), %d warning(s)\n' "$failures" "$warnings"
(( failures == 0 )) || exit 1

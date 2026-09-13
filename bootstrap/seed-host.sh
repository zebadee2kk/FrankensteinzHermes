#!/usr/bin/env bash
set -Eeuo pipefail

MODE="check"

usage() {
  cat <<'EOF'
Usage: seed-host.sh [--check|--apply]

  --check  Read-only preflight (default)
  --apply  Apply the seed-host baseline; requires root/sudo
EOF
}

log() { printf '[seed-host] %s\n' "$*"; }
warn() { printf '[seed-host] WARNING: %s\n' "$*" >&2; }
die() { printf '[seed-host] ERROR: %s\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --apply) MODE="apply" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $arg" ;;
  esac
done

[[ -r /etc/os-release ]] || die "/etc/os-release not found"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "Ubuntu is required; found ID=${ID:-unknown}"

case "${VERSION_ID:-}" in
  22.04|24.04|26.04) ;;
  *) warn "Ubuntu ${VERSION_ID:-unknown} is not a validated LTS baseline; continue only after review" ;;
esac

arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
mem_kib="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
mem_gib=$(( mem_kib / 1024 / 1024 ))
root_free_kib="$(df -Pk / | awk 'NR==2 {print $4}')"
root_free_gib=$(( root_free_kib / 1024 / 1024 ))
cpu_count="$(nproc)"

log "OS: ${PRETTY_NAME:-Ubuntu}"
log "Architecture: ${arch}"
log "CPU threads: ${cpu_count}"
log "RAM detected: ${mem_gib} GiB"
log "Root filesystem free: ${root_free_gib} GiB"

(( mem_gib >= 24 )) || die "at least 24 GiB RAM is required for the 32 GB seed profile"
(( root_free_gib >= 50 )) || die "at least 50 GiB free space is required before bootstrap"

if [[ "$MODE" == "check" ]]; then
  log "Read-only preflight passed. Re-run with --apply to configure the host."
  exit 0
fi

[[ $EUID -eq 0 ]] || die "--apply must be run as root, e.g. sudo ./bootstrap/seed-host.sh --apply"

export DEBIAN_FRONTEND=noninteractive
state_root="/var/lib/frankensteinzhermes/bootstrap"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
state_dir="${state_root}/${stamp}"
install -d -m 0750 "$state_dir"

dpkg-query -W -f='${binary:Package}\t${Version}\n' > "${state_dir}/packages.before.tsv" 2>/dev/null || true
systemctl is-enabled docker > "${state_dir}/docker.enabled.before.txt" 2>&1 || true
ufw status verbose > "${state_dir}/ufw.before.txt" 2>&1 || true

log "Refreshing Ubuntu package metadata"
apt-get update

packages=(
  ca-certificates
  curl
  git
  jq
  python3
  python3-pip
  python3-venv
  ufw
  unattended-upgrades
  smartmontools
  lm-sensors
  docker.io
  docker-compose-v2
)

log "Installing seed packages"
apt-get install -y --no-install-recommends "${packages[@]}"

log "Configuring unattended security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades.service >/dev/null 2>&1 || true

log "Configuring host firewall"
ufw default deny incoming
ufw default allow outgoing
if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq '(^|:|\])22$'; then
  log "Active SSH listener detected; preserving TCP/22 before enabling UFW"
  ufw allow 22/tcp comment 'FrankensteinzHermes bootstrap SSH safeguard'
fi
ufw --force enable

log "Enabling container runtime"
systemctl enable --now docker.service
systemctl enable --now containerd.service >/dev/null 2>&1 || true

log "Enabling SSD trim and hardware monitoring where available"
systemctl enable --now fstrim.timer >/dev/null 2>&1 || true
systemctl enable --now smartmontools.service >/dev/null 2>&1 || true

log "Creating runtime directories"
install -d -m 0755 /opt/frankensteinzhermes
install -d -m 0750 /etc/frankensteinzhermes
install -d -m 0750 /var/lib/frankensteinzhermes
install -d -m 0750 /var/log/frankensteinzhermes
install -d -m 0750 "$state_root"

cat > /etc/frankensteinzhermes/seed-host.env <<'EOF'
# Non-secret host resource envelope. Runtime secrets belong outside Git.
FHZ_SYSTEM_RESERVE_MB=6144
FHZ_MIN_FREE_DISK_GB=20
FHZ_MAX_HEAVY_AGENTS=2
FHZ_MAX_BROWSER_WORKERS=2
FHZ_MAX_LOCAL_MODELS=1
FHZ_MEMORY_PRESSURE_PERCENT=85
FHZ_THERMAL_THROTTLE_C=90
EOF
chmod 0640 /etc/frankensteinzhermes/seed-host.env

dpkg-query -W -f='${binary:Package}\t${Version}\n' > "${state_dir}/packages.after.tsv" 2>/dev/null || true
ufw status verbose > "${state_dir}/ufw.after.txt" 2>&1 || true

log "Seed baseline applied. State capture: ${state_dir}"
log "Run: sudo ./bootstrap/verify-seed-host.sh"
log "A reboot is recommended before runtime deployment so recovery behavior can be verified."

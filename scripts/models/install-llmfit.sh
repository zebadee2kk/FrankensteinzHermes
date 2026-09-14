#!/usr/bin/env bash
set -euo pipefail

readonly LLMFIT_VERSION="1.1.15"
readonly LLMFIT_TAG="v${LLMFIT_VERSION}"
readonly LLMFIT_REPO="AlexsJones/llmfit"
readonly LLMFIT_PLATFORM="x86_64-unknown-linux-gnu"
readonly ASSET="llmfit-${LLMFIT_TAG}-${LLMFIT_PLATFORM}.tar.gz"
readonly BASE_URL="https://github.com/${LLMFIT_REPO}/releases/download/${LLMFIT_TAG}"

INSTALL_DIR="${1:-${FZH_LLMFIT_INSTALL_DIR:-$HOME/.local/lib/frankensteinzhermes/llmfit/${LLMFIT_VERSION}}}"

fail() {
  printf 'llmfit install: %s\n' "$*" >&2
  exit 1
}

for command_name in curl tar sha256sum find install uname; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command missing: ${command_name}"
done

case "$(uname -s)" in
  Linux) ;;
  *) fail "B025 seed installer supports Linux only" ;;
esac

case "$(uname -m)" in
  x86_64|amd64) ;;
  *) fail "B025 seed installer supports x86_64 only" ;;
esac

umask 077
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

archive="${tmp_dir}/${ASSET}"
checksum="${archive}.sha256"

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "${BASE_URL}/${ASSET}" -o "$archive"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "${BASE_URL}/${ASSET}.sha256" -o "$checksum"

# FrankensteinzHermes requires the upstream checksum sidecar. Missing or invalid
# integrity metadata is a hard failure; we never silently skip verification.
(
  cd "$tmp_dir"
  sha256sum --check --strict "${ASSET}.sha256"
)

tar -xzf "$archive" -C "$tmp_dir"
binary="$(find "$tmp_dir" -type f -name llmfit -perm -u+x -print -quit)"
[[ -n "$binary" ]] || fail "llmfit binary not found in verified release archive"

install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$binary" "${INSTALL_DIR}/llmfit"

version_output="$("${INSTALL_DIR}/llmfit" --version 2>&1)"
[[ "$version_output" == *"${LLMFIT_VERSION}"* ]] || fail "installed binary does not report expected version ${LLMFIT_VERSION}: ${version_output}"

printf '%s\n' "${INSTALL_DIR}/llmfit"

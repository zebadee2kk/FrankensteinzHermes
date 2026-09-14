#!/usr/bin/env bash
set -euo pipefail

readonly LLMFIT_VERSION="1.1.15"
readonly LLMFIT_TAG="v${LLMFIT_VERSION}"
readonly LLMFIT_REPO="AlexsJones/llmfit"
readonly LLMFIT_PLATFORM="x86_64-unknown-linux-gnu"
readonly ASSET="llmfit-${LLMFIT_TAG}-${LLMFIT_PLATFORM}.tar.gz"
readonly BASE_URL="https://github.com/${LLMFIT_REPO}/releases/download/${LLMFIT_TAG}"
# GitHub release asset digest for release 386039909 / asset 554393199.
readonly EXPECTED_ARCHIVE_SHA256="fe0d4987376fae21cc1461f72a348a93c88cfacd2aec4356c15ba30603dcc731"

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

# Require both the publisher-provided sidecar and our reviewed immutable asset
# digest. This makes an upstream-sidecar change visible instead of trusting two
# files fetched from the same release location as the entire integrity model.
(
  cd "$tmp_dir"
  sha256sum --check --strict "${ASSET}.sha256"
  printf '%s  %s\n' "$EXPECTED_ARCHIVE_SHA256" "$ASSET" | sha256sum --check --strict -
)

tar -xzf "$archive" -C "$tmp_dir"
binary="$(find "$tmp_dir" -type f -name llmfit -perm -u+x -print -quit)"
[[ -n "$binary" ]] || fail "llmfit binary not found in verified release archive"

install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$binary" "${INSTALL_DIR}/llmfit"

version_output="$("${INSTALL_DIR}/llmfit" --version 2>&1)"
[[ "$version_output" == *"${LLMFIT_VERSION}"* ]] || fail "installed binary does not report expected version ${LLMFIT_VERSION}: ${version_output}"

printf '%s\n' "${INSTALL_DIR}/llmfit"

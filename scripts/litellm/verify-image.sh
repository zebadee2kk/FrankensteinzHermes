#!/usr/bin/env bash
set -euo pipefail

IMAGE="${FZH_LITELLM_IMAGE:-ghcr.io/berriai/litellm:v1.100.1}"
KEY_COMMIT="0112e53046018d726492c814b3644b7d376029d0"
KEY_URL="https://raw.githubusercontent.com/BerriAI/litellm/${KEY_COMMIT}/cosign.pub"

command -v cosign >/dev/null 2>&1 || {
  echo "cosign is required to verify the LiteLLM release image" >&2
  exit 2
}

[[ "$IMAGE" == "ghcr.io/berriai/litellm:v1.100.1" ]] || {
  echo "Unexpected LiteLLM image: $IMAGE" >&2
  exit 2
}

cosign verify --key "$KEY_URL" "$IMAGE"
echo "LiteLLM signature verification passed for $IMAGE"

# LiteLLM Private Gateway

LiteLLM is the single model-provider boundary for the seed system. Hermes and later workers should call the local OpenAI-compatible gateway rather than receiving upstream provider credentials directly.

## Seed design

- image: `ghcr.io/berriai/litellm:v1.100.1`;
- host binding: `127.0.0.1:4000` only;
- provider-facing egress network belongs only to the LiteLLM container;
- no LiteLLM database/UI/MCP/custom-code integration in B021;
- no provider models configured until B022/B023;
- master key supplied from a local root-managed environment file, never Git;
- container receives bounded CPU/RAM/PID/logging limits and no Linux capabilities.

The internal Compose network used by PostgreSQL/Prometheus remains `internal: true`; LiteLLM is intentionally attached to a separate egress-capable bridge because model providers require outbound Internet access.

## Secret file

Create the local file before starting LiteLLM:

```bash
sudo install -d -m 0700 /etc/frankensteinzhermes/secrets
sudo sh -c 'umask 077; printf "LITELLM_MASTER_KEY=%s\n" "$(openssl rand -hex 32)" > /etc/frankensteinzhermes/secrets/litellm.env'
```

B022/B023 may add provider keys to this local file or move them to a stronger secrets backend later. Do not commit the file or print its contents in evidence.

## Supply-chain verification

Upstream LiteLLM states its release images are Cosign-signed. Before first physical promotion and before an image upgrade:

```bash
bash scripts/litellm/verify-image.sh
```

The verification script trusts the signing key from immutable upstream commit `0112e53046018d726492c814b3644b7d376029d0`, rather than a moving branch/tag.

## Start and verify

```bash
export FZH_LITELLM_ENV_FILE=/etc/frankensteinzhermes/secrets/litellm.env
docker compose -f deploy/compose/compose.yaml up -d litellm
curl --fail --silent http://127.0.0.1:4000/health/liveliness
```

The gateway must not bind `0.0.0.0` on the host. Remote access, if ever needed, must be added through a separately authenticated ingress design rather than changing this bind casually.

## Hermes boundary

Hermes receives:

- local gateway URL;
- a gateway client credential appropriate to its role.

Hermes does **not** receive OpenRouter/Gemini/NVIDIA provider keys directly once the gateway is in service.

B022 introduces free-first OpenRouter model aliases/routing. B023 adds other approved free providers with provider-specific privacy classifications.

## Failure behaviour

If LiteLLM is unavailable:

1. Hermes should fail/defer remote inference cleanly rather than spin in an unbounded retry loop;
2. later B024 local Ollama degraded mode provides a separate fallback;
3. the gateway must not silently expose provider credentials or bypass policy to recover.

## Rollback

Stop the LiteLLM service and return to the previous approved image/config. B021 has no canonical data store. Provider credentials remain local and do not need to be copied into a rollback artefact.

Do not use `latest` tags for production promotion.

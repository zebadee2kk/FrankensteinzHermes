# Ollama Local Degraded Runtime

Ollama provides a local inference runtime for the seed laptop so FrankensteinzHermes retains useful AI capability when Internet/free providers are unavailable.

## Seed runtime

- image: `ollama/ollama:0.34.0`;
- CPU-only first deployment;
- profile: `local-ai` so normal core-service commands do not pull/start the multi-GB image;
- durable model volume: `frankensteinzhermes-ollama-models`;
- no host port publication;
- initial limits: 16 GiB RAM, 6 CPUs, 512 PIDs;
- bounded Docker logs and `no-new-privileges`;
- production model intentionally **not selected in B024**.

The official Docker image supports CPU-only operation. B025 uses `llmfit` and real XPS measurements to select the local model rather than guessing from nominal parameter counts.

## Start runtime

```bash
docker compose -f deploy/compose/compose.yaml --profile local-ai up -d ollama
bash scripts/ollama/verify.sh
```

Ollama is intentionally not published on host TCP/11434. Future LiteLLM local routing will access it through Docker networking after a model passes B025/B026.

## Pulling models

Do not populate production models until B025 has recorded the XPS hardware inventory and benchmark recommendation.

After approval of a model candidate:

```bash
docker compose -f deploy/compose/compose.yaml --profile local-ai exec ollama ollama pull <approved-model>
```

Record the exact model/tag and, where available, model digest in the model registry/evidence. Do not use an unrecorded floating choice as the production fallback.

## Offline acceptance

After the selected model is present locally:

1. start PostgreSQL, monitoring, LiteLLM and Ollama;
2. confirm normal seed health;
3. disconnect/deny Internet access;
4. generate a fixed evaluation prompt locally;
5. confirm the response succeeds without network access;
6. confirm core services remain responsive while the model is loaded;
7. record RAM, swap, load, latency and temperature observations;
8. stop the test and reconnect network access.

This evidence belongs to B024/B025 physical acceptance.

## Resource governor integration

A model generation is a **heavy** workload. Future autonomous workers must acquire a governor heavy slot before initiating local model-intensive work. B024 itself does not bypass or replace the governor.

The Ollama container limit is a ceiling, not a target. B025 may reduce it after measurement. A model that causes swap storms, unacceptable thermal pressure, or loss of core-service responsiveness is not an acceptable fallback even if it technically runs.

## Network boundary

The seed Ollama service uses the model network so it can initially pull approved models. It has no host-published port. A later hardening step may separate model-download and runtime networks once operating experience justifies the extra mechanism.

Local inference does not automatically mean all local data is safe to send to every model. Existing data classification and tool/action policies still apply.

## Upgrade

Ollama upgrades are explicit candidate changes. Update the pinned image on a branch, run CI/static checks, retain the old image reference, and verify existing approved models before promotion.

Do not track `latest` in production.

## Rollback

Runtime rollback does **not** delete the model volume:

```bash
docker compose -f deploy/compose/compose.yaml --profile local-ai stop ollama
# revert runtime image/config to previous approved revision
docker compose -f deploy/compose/compose.yaml --profile local-ai up -d ollama
```

The named model volume is persistent and must not be removed by ordinary rollback. Model deletion is a separate deliberate storage action.

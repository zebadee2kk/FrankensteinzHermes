# B025 — llmfit hardware/model benchmark

B025 measures the seed node before any Ollama model is promoted as an automatic fallback. `llmfit` is advisory evidence only; B026 owns quality/policy promotion.

## Supply-chain pin

FrankensteinzHermes pins `AlexsJones/llmfit` **v1.1.15** for the B025 seed workflow. The installer downloads the exact `x86_64-unknown-linux-gnu` release asset and its upstream `.sha256` sidecar. A missing or invalid checksum is a hard failure.

Install without replacing a system package:

```bash
install_dir="$HOME/.local/lib/frankensteinzhermes/llmfit/1.1.15"
llmfit_bin="$(bash scripts/models/install-llmfit.sh "$install_dir")"
"$llmfit_bin" --version
```

Do not substitute `latest`, an unverified convenience installer, or a different release inside B025 evidence.

## Capture the real XPS

Run this on the physical Dell XPS with no synthetic override variables set:

```bash
export FZH_LLMFIT_BIN="$HOME/.local/lib/frankensteinzhermes/llmfit/1.1.15/llmfit"
bundle="$(bash scripts/models/capture-llmfit-evidence.sh)"
printf 'evidence: %s\n' "$bundle"
```

The bundle contains:

- `llmfit-version.txt`;
- `system.json`;
- `doctor.txt` and `doctor.exit-code`;
- `recommend-general.json`;
- `recommend-coding.json`;
- `recommend-reasoning.json`;
- `candidate-report.json` — stable B026 handoff wrapper;
- `manifest.json` — hashes and sizes for raw evidence inputs.

`evidence/` is gitignored. Store the real-machine bundle in the protected operational evidence location used for the seed deployment, not in the public repository.

## CI synthetic contract

CI is allowed to use `FZH_LLMFIT_RAM` and `FZH_LLMFIT_CPU_CORES` to prove the non-interactive pipeline on a GitHub runner. Such a report records `synthetic_hardware_overrides: true` and **must never be interpreted as XPS model-selection evidence**.

## Credential boundary

The capture wrapper removes GitHub and common model-provider credentials before executing `llmfit`, disables its interactive GitHub device flow, and scans the resulting bundle for credential-like material. B025 never invokes `bench --share`.

## Selecting candidates for B026

Use the real-XPS general/coding/reasoning recommendations to identify a small candidate set. Do not promote a model merely because `llmfit` says it fits. For each candidate B026 must additionally record:

1. exact Ollama model/tag and digest where available;
2. measured tokens/s and latency on the XPS;
3. peak RAM/swap/load and thermal behaviour;
4. response quality on fixed reasoning/coding/general tasks;
5. whether PostgreSQL, LiteLLM and monitoring remain responsive while loaded;
6. classification/policy eligibility;
7. rollback/removal behaviour.

The generated report deliberately contains `promotion_authorized: false` until B026 completes those gates.

## Optional local benchmarking

After a candidate has been deliberately pulled into Ollama, `llmfit bench` may be used to measure it. Keep benchmark results local. Do **not** use `--share` from the autonomous seed workflow and do not provide llmfit a GitHub token.

## Rollback

Removing B025 tooling does not touch Ollama models or runtime state:

```bash
rm -rf "$HOME/.local/lib/frankensteinzhermes/llmfit/1.1.15"
```

Evidence deletion is a separate deliberate action. Never combine rollback with deleting the B024 Ollama model volume.

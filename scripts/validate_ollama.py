#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
compose = (ROOT / "deploy/compose/compose.yaml").read_text(encoding="utf-8")
doc = ROOT / "docs/operations/OLLAMA-DEGRADED.md"
verify = ROOT / "scripts/ollama/verify.sh"

required = [
    "image: ollama/ollama:0.34.0",
    'profiles: ["local-ai"]',
    "ollama_models:/root/.ollama",
    "mem_limit: ${FZH_OLLAMA_MEMORY_LIMIT:-16g}",
    "cpus: ${FZH_OLLAMA_CPU_LIMIT:-6.0}",
    "frankensteinzhermes-ollama-models",
]
for fragment in required:
    if fragment not in compose:
        raise SystemExit(f"Ollama runtime missing required invariant: {fragment}")

# Ollama must not publish its API to the host. Only LiteLLM is the model gateway.
ollama_block = compose.split("  ollama:\n", 1)[1].split("\n  prometheus:\n", 1)[0]
if "ports:" in ollama_block:
    raise SystemExit("Ollama must not publish a host port")
if ":latest" in ollama_block:
    raise SystemExit("Ollama must not use latest")
if not doc.exists() or not verify.exists():
    raise SystemExit("Ollama runbook and verifier are required")

print("Ollama degraded-runtime validation passed")

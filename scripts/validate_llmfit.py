#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
installer = (ROOT / "scripts/models/install-llmfit.sh").read_text(encoding="utf-8")
capture = (ROOT / "scripts/models/capture-llmfit-evidence.sh").read_text(encoding="utf-8")
builder = ROOT / "scripts/models/build-llmfit-report.py"
runbook = ROOT / "docs/operations/LLMFIT-BENCHMARK.md"
gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

required_installer = [
    'LLMFIT_VERSION="1.1.15"',
    'LLMFIT_PLATFORM="x86_64-unknown-linux-gnu"',
    'EXPECTED_ARCHIVE_SHA256="fe0d4987376fae21cc1461f72a348a93c88cfacd2aec4356c15ba30603dcc731"',
    '${ASSET}.sha256',
    'sha256sum --check --strict',
]
for fragment in required_installer:
    if fragment not in installer:
        raise SystemExit(f"llmfit installer missing required invariant: {fragment}")
if "releases/latest" in installer or ":latest" in installer:
    raise SystemExit("llmfit installer must not follow a floating latest release")

for fragment in (
    "recommend --json --use-case",
    "general coding reasoning",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "LLMFIT_GH_CLIENT_ID=",
):
    if fragment not in capture:
        raise SystemExit(f"llmfit capture missing required invariant: {fragment}")

if "bench --share" in capture:
    raise SystemExit("autonomous B025 capture must never share llmfit benchmarks upstream")
if not builder.exists() or not runbook.exists():
    raise SystemExit("llmfit report builder and runbook are required")
if "evidence/" not in gitignore:
    raise SystemExit("local llmfit evidence must be gitignored")

builder_text = builder.read_text(encoding="utf-8")
if '"promotion_authorized": False' not in builder_text:
    raise SystemExit("B025 report must explicitly deny automatic model promotion")

print("llmfit evidence-pipeline validation passed")

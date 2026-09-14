#!/usr/bin/env python3
"""Build a stable FrankensteinzHermes wrapper around raw llmfit evidence.

The raw upstream JSON is always retained. This report deliberately extracts
only a small compatibility surface so B026 can consume evidence without
coupling itself to every upstream field name.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

USE_CASES = ("general", "coding", "reasoning")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid JSON evidence {path}: {exc}") from exc


def extract_candidates(payload: Any, source: Path) -> list[dict[str, Any]]:
    rows: Any = payload
    if isinstance(payload, dict):
        for key in ("models", "recommendations", "results", "candidates"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break

    if not isinstance(rows, list):
        raise SystemExit(f"{source} does not contain a recognizable candidate list")
    if not rows:
        raise SystemExit(f"{source} contains no model candidates")
    if not all(isinstance(row, dict) for row in rows):
        raise SystemExit(f"{source} candidate list contains non-object rows")
    return rows


def first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": first(row, "name", "model", "model_name", "id"),
        "provider": first(row, "provider", "source", "runtime_provider"),
        "fit": first(row, "fit", "fit_level", "memory_fit"),
        "score": first(row, "score", "composite_score", "total_score"),
        "estimated_tps": first(row, "estimated_tps", "tokens_per_second", "tps"),
        "quantization": first(row, "quantization", "quant", "recommended_quantization"),
        "estimated_memory_gb": first(
            row, "estimated_memory_gb", "memory_gb", "ram_gb", "required_memory_gb"
        ),
        "raw": row,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--synthetic", choices=("true", "false"), default="false")
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    system_path = bundle / "system.json"
    version_path = bundle / "llmfit-version.txt"
    doctor_path = bundle / "doctor.txt"
    doctor_rc_path = bundle / "doctor.exit-code"

    system = load_json(system_path)
    if not isinstance(system, dict):
        raise SystemExit("system.json must contain a JSON object")

    profiles: dict[str, Any] = {}
    source_files = [system_path, version_path, doctor_path, doctor_rc_path]
    for use_case in USE_CASES:
        source = bundle / f"recommend-{use_case}.json"
        payload = load_json(source)
        candidates = extract_candidates(payload, source)
        profiles[use_case] = {
            "source": source.name,
            "candidate_count": len(candidates),
            "candidates": [normalize(row) for row in candidates],
        }
        source_files.append(source)

    version_text = version_path.read_text(encoding="utf-8").strip()
    doctor_rc = int(doctor_rc_path.read_text(encoding="utf-8").strip())

    report = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "synthetic_hardware_overrides": args.synthetic == "true",
        "llmfit_version_output": version_text,
        "system": system,
        "doctor_exit_code": doctor_rc,
        "profiles": profiles,
        "promotion_authorized": False,
        "next_gate": "B026 model evaluation registry",
    }
    (bundle / "candidate-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    manifest = {
        "schema_version": 1,
        "synthetic_hardware_overrides": args.synthetic == "true",
        "files": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in source_files
        },
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

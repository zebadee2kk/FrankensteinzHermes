#!/usr/bin/env python3
"""Convert a B025 evidence bundle into CSV for the B026 evidence inbox.

This script intentionally writes only model_candidate_evidence rows. It does
not create model_candidates and cannot approve or enable a route.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

USE_CASES = ("general", "coding", "reasoning")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid B025 evidence file {path}: {exc}") from exc


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    report_path = bundle / "candidate-report.json"
    manifest_path = bundle / "manifest.json"
    report = load_json(report_path)
    manifest = load_json(manifest_path)

    if report.get("schema_version") != 1:
        raise SystemExit("unsupported B025 candidate-report schema")
    if report.get("promotion_authorized") is not False:
        raise SystemExit("B025 report does not explicitly deny promotion")
    if manifest.get("schema_version") != 1:
        raise SystemExit("unsupported B025 manifest schema")

    system = report.get("system")
    if not isinstance(system, dict):
        raise SystemExit("B025 report missing system object")
    hardware_fingerprint = canonical_digest(system)
    source_version = str(report.get("llmfit_version_output") or "unknown")
    synthetic = bool(report.get("synthetic_hardware_overrides"))
    observed_at = report.get("generated_at_utc")
    if not isinstance(observed_at, str) or not observed_at:
        raise SystemExit("B025 report missing generated_at_utc")

    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(
        [
            "source_system",
            "source_version",
            "use_case",
            "source_candidate_name",
            "hardware_fingerprint",
            "synthetic",
            "linked_candidate_key",
            "evidence_ref",
            "evidence_sha256",
            "source_payload",
            "observed_at",
        ]
    )

    profiles = report.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("B025 report missing profiles")

    row_count = 0
    for use_case in USE_CASES:
        profile = profiles.get(use_case)
        if not isinstance(profile, dict):
            raise SystemExit(f"B025 report missing {use_case} profile")
        source_name = profile.get("source")
        if not isinstance(source_name, str):
            raise SystemExit(f"B025 {use_case} profile missing source")
        source_meta = manifest.get("files", {}).get(source_name)
        if not isinstance(source_meta, dict):
            raise SystemExit(f"manifest missing source file metadata for {source_name}")
        evidence_sha = source_meta.get("sha256")
        if not isinstance(evidence_sha, str) or len(evidence_sha) != 64:
            raise SystemExit(f"manifest has invalid SHA-256 for {source_name}")

        candidates = profile.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise SystemExit(f"B025 {use_case} profile has no candidates")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise SystemExit(f"B025 {use_case} candidate is not an object")
            name = candidate.get("name")
            if not isinstance(name, str) or not name.strip():
                raise SystemExit(f"B025 {use_case} candidate has no stable name")
            raw = candidate.get("raw", candidate)
            writer.writerow(
                [
                    "llmfit",
                    source_version,
                    use_case,
                    name.strip(),
                    hardware_fingerprint,
                    "true" if synthetic else "false",
                    "",
                    f"b025:{source_name}:{evidence_sha}",
                    evidence_sha,
                    json.dumps(raw, sort_keys=True, separators=(",", ":")),
                    observed_at,
                ]
            )
            row_count += 1

    if row_count == 0:
        raise SystemExit("B025 report produced no evidence rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

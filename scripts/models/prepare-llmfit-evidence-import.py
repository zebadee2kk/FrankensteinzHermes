#!/usr/bin/env python3
"""Convert a B025 evidence bundle into CSV for the B026 evidence inbox.

This script intentionally writes only model_candidate_evidence rows. It does
not create model_candidates and cannot approve or enable a route. Raw B025
recommendation files are verified against their manifest hashes before use.
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


def digest_file(path: Path) -> str:
    try:
        return digest_bytes(path.read_bytes())
    except OSError as exc:
        raise SystemExit(f"unable to read B025 evidence file {path}: {exc}") from exc


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest_bytes(payload)


def extract_candidates(payload: Any, source: Path) -> list[dict[str, Any]]:
    rows: Any = payload
    if isinstance(payload, dict):
        for key in ("models", "recommendations", "results", "candidates"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise SystemExit(f"raw B025 recommendation file has no usable candidates: {source}")
    return rows


def candidate_name(row: dict[str, Any]) -> str:
    for key in ("name", "model", "model_name", "id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise SystemExit("raw B025 candidate has no stable name")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    report = load_json(bundle / "candidate-report.json")
    manifest = load_json(bundle / "manifest.json")

    if report.get("schema_version") != 1:
        raise SystemExit("unsupported B025 candidate-report schema")
    if report.get("promotion_authorized") is not False:
        raise SystemExit("B025 report does not explicitly deny promotion")
    if manifest.get("schema_version") != 1:
        raise SystemExit("unsupported B025 manifest schema")
    if bool(report.get("synthetic_hardware_overrides")) != bool(
        manifest.get("synthetic_hardware_overrides")
    ):
        raise SystemExit("B025 report/manifest synthetic-hardware flag mismatch")

    system = report.get("system")
    if not isinstance(system, dict):
        raise SystemExit("B025 report missing system object")
    hardware_fingerprint = canonical_digest(system)
    source_version = str(report.get("llmfit_version_output") or "unknown")
    synthetic = bool(report.get("synthetic_hardware_overrides"))
    observed_at = report.get("generated_at_utc")
    if not isinstance(observed_at, str) or not observed_at:
        raise SystemExit("B025 report missing generated_at_utc")

    profiles = report.get("profiles")
    manifest_files = manifest.get("files")
    if not isinstance(profiles, dict) or not isinstance(manifest_files, dict):
        raise SystemExit("B025 report/manifest missing profile or file metadata")

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

    row_count = 0
    for use_case in USE_CASES:
        profile = profiles.get(use_case)
        if not isinstance(profile, dict):
            raise SystemExit(f"B025 report missing {use_case} profile")
        source_name = profile.get("source")
        expected_source_name = f"recommend-{use_case}.json"
        if source_name != expected_source_name:
            raise SystemExit(
                f"B025 {use_case} source mismatch: expected {expected_source_name}, got {source_name!r}"
            )

        source_meta = manifest_files.get(source_name)
        if not isinstance(source_meta, dict):
            raise SystemExit(f"manifest missing source file metadata for {source_name}")
        evidence_sha = source_meta.get("sha256")
        if not isinstance(evidence_sha, str) or len(evidence_sha) != 64:
            raise SystemExit(f"manifest has invalid SHA-256 for {source_name}")

        source_path = bundle / source_name
        actual_sha = digest_file(source_path)
        if actual_sha != evidence_sha:
            raise SystemExit(
                f"B025 raw evidence checksum mismatch for {source_name}: manifest={evidence_sha} actual={actual_sha}"
            )
        expected_bytes = source_meta.get("bytes")
        if not isinstance(expected_bytes, int) or source_path.stat().st_size != expected_bytes:
            raise SystemExit(f"B025 raw evidence size mismatch for {source_name}")

        candidates = extract_candidates(load_json(source_path), source_path)
        if profile.get("candidate_count") != len(candidates):
            raise SystemExit(f"B025 {use_case} candidate count does not match raw source")

        for raw in candidates:
            writer.writerow(
                [
                    "llmfit",
                    source_version,
                    use_case,
                    candidate_name(raw),
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

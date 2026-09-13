#!/usr/bin/env python3
"""Validate machine-readable FrankensteinzHermes governance files.

This is intentionally dependency-light. CI installs PyYAML before running it.
"""

from __future__ import annotations

from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_yaml(relative: str):
    path = ROOT / relative
    if not path.exists():
        fail(f"required file missing: {relative}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # CI diagnostic
        fail(f"invalid YAML in {relative}: {exc}")


def validate_backlog() -> None:
    data = load_yaml("roadmap/backlog.yaml")
    items = data.get("items", [])
    if not items:
        fail("backlog contains no items")

    ids = [item.get("id") for item in items]
    if None in ids:
        fail("every backlog item must have an id")
    if len(ids) != len(set(ids)):
        fail("backlog item ids must be unique")

    known = set(ids)
    for item in items:
        for dep in item.get("depends_on", []):
            if dep not in known:
                fail(f"{item['id']} depends on unknown item {dep}")
        risk = item.get("risk")
        if risk not in {"L0", "L1", "L2", "L3"}:
            fail(f"{item['id']} has invalid risk class {risk!r}")


def validate_autonomy_policy() -> None:
    data = load_yaml("config/autonomy-policy.example.yaml")
    classes = data.get("classes", {})
    if set(classes) != {"L0", "L1", "L2", "L3"}:
        fail("autonomy policy must define exactly L0-L3")
    if classes["L3"].get("approval") != "human_required":
        fail("L3 must remain human_required")
    if data.get("finance", {}).get("real_money_actions") is not False:
        fail("real money actions must default to false")
    if data.get("models", {}).get("paid_fallback_enabled") is not False:
        fail("paid model fallback must default to false")


def validate_model_policy() -> None:
    data = load_yaml("config/model-routing.example.yaml")
    if data.get("strategy") != "free_first":
        fail("seed model strategy must be free_first")
    if data.get("paid_fallback_enabled") is not False:
        fail("paid fallback must be disabled by default")
    secret = data.get("classifications", {}).get("SECRET", {})
    if secret.get("remote_allowed") is not False:
        fail("SECRET classification must not be remotely routed")


def validate_required_docs() -> None:
    required = [
        "docs/ARCHITECTURE.md",
        "docs/AUTONOMY-CONSTITUTION.md",
        "docs/THREAT-MODEL.md",
        "docs/MODEL-ROUTING.md",
        "docs/STATE-OWNERSHIP.md",
        "docs/ROADMAP.md",
        "docs/SEED-NODE.md",
        "SECURITY.md",
    ]
    for relative in required:
        path = ROOT / relative
        if not path.exists() or path.stat().st_size == 0:
            fail(f"required governance document missing/empty: {relative}")


def main() -> None:
    validate_required_docs()
    validate_backlog()
    validate_autonomy_policy()
    validate_model_policy()
    print("FrankensteinzHermes repository validation passed")


if __name__ == "__main__":
    main()

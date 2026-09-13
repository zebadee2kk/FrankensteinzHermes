#!/usr/bin/env python3
"""Validate machine-readable FrankensteinzHermes governance files.

This is intentionally dependency-light. CI installs PyYAML before running it.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA_PIN = re.compile(r"^[0-9a-f]{40}$")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_yaml(relative: str):
    path = ROOT / relative
    if not path.exists():
        fail(f"required file missing: {relative}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"invalid YAML in {relative}: {exc}")


def load_json(relative: str):
    path = ROOT / relative
    if not path.exists():
        fail(f"required file missing: {relative}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"invalid JSON in {relative}: {exc}")


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
        if item.get("risk") not in {"L0", "L1", "L2", "L3"}:
            fail(f"{item['id']} has invalid risk class {item.get('risk')!r}")


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


def validate_repository_protection() -> None:
    data = load_yaml("policy/repository-protection.yaml")
    enforcement = data.get("enforcement", {})
    if enforcement.get("mode") != "credential_boundary_and_ci":
        fail("free-plan governance must use credential_boundary_and_ci")
    required = data.get("required", {})
    if required.get("pull_request_workflow") is not True:
        fail("pull request workflow must remain required")
    if required.get("automated_ci_before_promotion") is not True:
        fail("CI before promotion must remain required")
    if required.get("direct_push_by_autonomous_identity") is not False:
        fail("autonomous identities must not directly push upstream main")
    checks = set(required.get("required_status_checks", []))
    expected = {"governance", "secret-scan", "dependency-review"}
    if not expected.issubset(checks):
        fail(f"repository governance missing required checks: {sorted(expected - checks)}")
    engineering = data.get("engineering_identity", {})
    for key in ("repository_admin", "ruleset_admin", "secret_admin", "upstream_contents_write", "upstream_workflow_admin"):
        if engineering.get(key) is not False:
            fail(f"engineering identity must not have {key}")
    if engineering.get("contribution_mode") != "fork_or_owner_mediated_branch":
        fail("engineering contribution mode must preserve an upstream authority boundary")
    promotion = data.get("promotion", {}).get("until_hard_upstream_protection_is_available", {})
    if promotion.get("autonomous_agent_may_self_merge") is not False:
        fail("autonomous agents must not self-merge upstream during free-plan governance")


def validate_release_evidence() -> None:
    schema = load_json("schemas/release-evidence.schema.json")
    required = set(schema.get("required", []))
    expected = {"work_item", "candidate", "risk", "summary", "tests", "reviews", "baseline", "rollback", "promotion"}
    if not expected.issubset(required):
        fail(f"release evidence schema missing required fields: {sorted(expected - required)}")
    example = load_yaml("examples/release-evidence.example.yaml")
    missing = expected - set(example)
    if missing:
        fail(f"release evidence example missing fields: {sorted(missing)}")
    if example.get("risk") not in {"L0", "L1", "L2", "L3"}:
        fail("release evidence example has invalid risk")
    rollback = example.get("rollback", {})
    if rollback.get("required") is True and not rollback.get("method"):
        fail("required rollback must have a method")


def validate_action_pins() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    for path in workflow_dir.glob("*.y*ml"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("uses:"):
                continue
            value = stripped.split(":", 1)[1].strip().split()[0]
            if value.startswith("./"):
                continue
            if "@" not in value:
                fail(f"{path.relative_to(ROOT)}:{number} action is not pinned")
            ref = value.rsplit("@", 1)[1]
            if not SHA_PIN.fullmatch(ref):
                fail(f"{path.relative_to(ROOT)}:{number} action must be pinned to a 40-character commit SHA")


def validate_required_docs() -> None:
    required = [
        "docs/ARCHITECTURE.md",
        "docs/AUTONOMY-CONSTITUTION.md",
        "docs/THREAT-MODEL.md",
        "docs/MODEL-ROUTING.md",
        "docs/STATE-OWNERSHIP.md",
        "docs/ROADMAP.md",
        "docs/SEED-NODE.md",
        "docs/operations/REPOSITORY-GOVERNANCE.md",
        "docs/operations/CI-SECURITY.md",
        "docs/operations/RELEASE-EVIDENCE.md",
        "docs/operations/SEED-BOOTSTRAP.md",
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
    validate_repository_protection()
    validate_release_evidence()
    validate_action_pins()
    print("FrankensteinzHermes repository validation passed")


if __name__ == "__main__":
    main()

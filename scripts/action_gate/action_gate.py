#!/usr/bin/env python3
"""Deterministic FrankensteinzHermes Action Gate v1.

No model call, network access or mutable runtime policy is involved in a
decision. The installed daemon loads one root-owned policy and checks one
root-owned kill-switch path. Callers submit only action requests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DECISIONS = {"allow", "deny", "approval_required"}
RISK_LEVELS = {"L0", "L1", "L2", "L3"}
CLASSIFICATIONS = {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"}
ENVIRONMENTS = {"local", "isolated", "staging", "production", "external"}


class GateError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedPolicy:
    raw: dict[str, Any]
    digest: str


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def load_policy(path: Path) -> LoadedPolicy:
    try:
        policy = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"policy_load_failed:{exc.__class__.__name__}") from exc
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise GateError("policy_schema_invalid")
    defaults = policy.get("risk_defaults")
    if not isinstance(defaults, dict) or set(defaults) != RISK_LEVELS:
        raise GateError("policy_risk_defaults_invalid")
    if any(value not in DECISIONS for value in defaults.values()):
        raise GateError("policy_decision_invalid")
    for list_key in ("hard_deny_actions", "approval_required_actions"):
        values = policy.get(list_key)
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            raise GateError(f"policy_{list_key}_invalid")
    return LoadedPolicy(policy, sha256_json(policy))


def validate_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise GateError("request_not_object")

    required_strings = (
        "request_id",
        "actor",
        "action_type",
        "risk_level",
        "environment",
        "data_classification",
        "target",
    )
    for key in required_strings:
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise GateError(f"request_{key}_invalid")

    if request["risk_level"] not in RISK_LEVELS:
        raise GateError("request_risk_level_invalid")
    if request["environment"] not in ENVIRONMENTS:
        raise GateError("request_environment_invalid")
    if request["data_classification"] not in CLASSIFICATIONS:
        raise GateError("request_data_classification_invalid")

    for key in ("side_effecting", "reversible", "external_side_effect", "uses_llm"):
        if not isinstance(request.get(key), bool):
            raise GateError(f"request_{key}_invalid")

    metadata = request.get("metadata", {})
    if not isinstance(metadata, dict):
        raise GateError("request_metadata_invalid")

    normalized = dict(request)
    for key in required_strings:
        normalized[key] = normalized[key].strip()
    normalized["metadata"] = metadata
    return normalized


def _decision(request: dict[str, Any], policy: dict[str, Any], kill_switch_active: bool) -> tuple[str, str]:
    action = request["action_type"]
    risk = request["risk_level"]

    if kill_switch_active:
        return "deny", "kill_switch_active"

    if action in set(policy["hard_deny_actions"]):
        return "deny", f"hard_deny_action:{action}"

    if request["data_classification"] == "SECRET":
        secret_policy = policy.get("secret", {})
        if secret_policy.get("deny_if_uses_llm", True) and request["uses_llm"]:
            return "deny", "secret_llm_use_denied"
        if secret_policy.get("deny_if_external_side_effect", True) and request["external_side_effect"]:
            return "deny", "secret_external_side_effect_denied"

    if action in set(policy["approval_required_actions"]):
        return "approval_required", f"explicit_approval_action:{action}"

    default = policy["risk_defaults"][risk]

    if risk == "L0" and request["side_effecting"]:
        return "approval_required", "l0_side_effect_mismatch"

    if risk == "L1":
        l1 = policy.get("l1", {})
        if l1.get("reversible_must_be", True) and not request["reversible"]:
            return "approval_required", "l1_irreversible"
        if request["environment"] in set(l1.get("forbidden_environments", ["production", "external"])):
            return "approval_required", "l1_environment_escalation"
        if l1.get("external_side_effect_must_be", False) is False and request["external_side_effect"]:
            return "approval_required", "l1_external_side_effect"

    return default, f"risk_default:{risk}:{default}"


def decide(request: Any, loaded_policy: LoadedPolicy, *, kill_switch_active: bool = False) -> dict[str, Any]:
    """Return a deterministic decision; malformed input fails closed."""
    try:
        normalized = validate_request(request)
        decision, reason = _decision(normalized, loaded_policy.raw, kill_switch_active)
        request_hash = sha256_json(normalized)
        risk_level = normalized["risk_level"]
        request_id = normalized["request_id"]
        actor = normalized["actor"]
        action_type = normalized["action_type"]
        environment = normalized["environment"]
        classification = normalized["data_classification"]
        target_sha256 = hashlib.sha256(normalized["target"].encode("utf-8")).hexdigest()
    except GateError as exc:
        try:
            request_hash = sha256_json(request)
        except (TypeError, ValueError):
            request_hash = hashlib.sha256(repr(request).encode("utf-8", errors="replace")).hexdigest()
        decision = "deny"
        reason = f"fail_closed:{exc}"
        risk_level = request.get("risk_level") if isinstance(request, dict) else None
        request_id = request.get("request_id") if isinstance(request, dict) else None
        actor = request.get("actor") if isinstance(request, dict) else None
        action_type = request.get("action_type") if isinstance(request, dict) else None
        environment = request.get("environment") if isinstance(request, dict) else None
        classification = request.get("data_classification") if isinstance(request, dict) else None
        target = request.get("target") if isinstance(request, dict) else None
        target_sha256 = hashlib.sha256(str(target).encode("utf-8")).hexdigest() if target is not None else None

    return {
        "schema_version": 1,
        "request_id": request_id,
        "actor": actor,
        "action_type": action_type,
        "risk_level": risk_level,
        "environment": environment,
        "data_classification": classification,
        "target_sha256": target_sha256,
        "decision": decision,
        "reason": reason,
        "request_sha256": request_hash,
        "policy_sha256": loaded_policy.digest,
        "kill_switch_active": bool(kill_switch_active),
    }

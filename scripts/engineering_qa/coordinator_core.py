#!/usr/bin/env python3
"""B034 adversarial QA coordinator.

Requires an independently approved B033 evidence bundle, obtains B030 admission,
creates a standalone detached clone, lets an optional model propose only
preapproved QA profile IDs, and executes candidate code only through the fixed
sandbox adapter. It never mutates the source repository/candidate ref and has no
promotion authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BRANCH = re.compile(r"^fzh/job-[0-9a-f]{12}$")
SAFE_PROFILE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
DEFAULT_GATE = ["/usr/local/libexec/frankensteinzhermes/action-gate-client"]
READ_ONLY_GIT = {"rev-parse", "merge-base", "rev-list", "status"}
MAX_INPUT = 1024 * 1024


class QAError(RuntimeError):
    pass


class QAIntegrityError(QAError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, limit: int = MAX_INPUT) -> Any:
    data = path.read_bytes()
    if len(data) > limit:
        raise QAError(f"input_too_large:{path.name}")
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise QAError(f"invalid_json:{path.name}:{exc.msg}") from exc


def minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    if extra:
        env.update(extra)
    return env


def ledger_env() -> dict[str, str]:
    env = minimal_env()
    for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGPASSFILE", "PGSSLMODE"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def remaining(started: float, wall: int) -> int:
    left = wall - int(time.monotonic() - started)
    if left <= 0:
        raise QAError("wall_clock_budget_exhausted")
    return left


def run(cmd: list[str], *, cwd: Path, timeout: int, input_bytes: bytes | None = None,
        env: dict[str, str] | None = None, max_output: int = 2_000_000) -> subprocess.CompletedProcess[bytes]:
    try:
        cp = subprocess.run(cmd, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, env=env, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise QAError(f"command_unavailable:{cmd[0]}:{exc.__class__.__name__}") from exc
    if len(cp.stdout) + len(cp.stderr) > max_output:
        raise QAError(f"command_output_too_large:{cmd[0]}")
    return cp


def require_ok(cp: subprocess.CompletedProcess[bytes], label: str, integrity: bool = False) -> bytes:
    if cp.returncode != 0:
        error = f"{label}_failed:{cp.stderr.decode('utf-8', errors='replace')[-1600:]}"
        if integrity:
            raise QAIntegrityError(error)
        raise QAError(error)
    return cp.stdout


def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise QAError("config_schema_invalid")
    ledger, model, sandbox, budgets = value.get("ledger"), value.get("model"), value.get("sandbox"), value.get("budgets")
    for name, section in (("ledger", ledger), ("model", model), ("sandbox", sandbox), ("budgets", budgets)):
        if not isinstance(section, dict):
            raise QAError(f"{name}_config_invalid")
    for key in ("adapter_command",):
        for section in (ledger, sandbox):
            cmd = section.get(key)
            if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
                raise QAError("adapter_command_invalid")
    if model.get("enabled") not in (True, False):
        raise QAError("model_enabled_invalid")
    if model["enabled"]:
        cmd = model.get("adapter_command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise QAError("model_adapter_invalid")
    for key, low, high in (
        ("max_wall_seconds", 30, 3600), ("max_git_commands", 1, 50),
        ("max_review_diff_bytes", 1, 2_000_000), ("max_evidence_bytes", 4096, 4_000_000),
    ):
        item = budgets.get(key)
        if not isinstance(item, int) or not low <= item <= high:
            raise QAError(f"budget_invalid:{key}")
    for key, low, high in (
        ("max_calls", 1, 2), ("max_prompt_bytes", 4096, 1_000_000),
        ("max_output_tokens", 128, 4096), ("max_response_bytes", 1024, 512_000),
        ("timeout_seconds", 5, 300), ("max_profile_suggestions", 0, 32),
    ):
        item = model.get(key)
        if not isinstance(item, int) or not low <= item <= high:
            raise QAError(f"model_budget_invalid:{key}")
    allowed, baseline = sandbox.get("allowed_profile_ids"), sandbox.get("baseline_profile_ids")
    if not isinstance(allowed, list) or not allowed or not isinstance(baseline, list) or not baseline:
        raise QAError("qa_profile_catalog_invalid")
    if len(set(allowed)) != len(allowed) or len(set(baseline)) != len(baseline):
        raise QAError("duplicate_qa_profile_id")
    for item in allowed + baseline:
        if not isinstance(item, str) or not SAFE_PROFILE.fullmatch(item):
            raise QAError("invalid_qa_profile_id")
    if not set(baseline).issubset(set(allowed)):
        raise QAError("baseline_profile_not_allowed")
    max_profiles = sandbox.get("max_profiles")
    max_response = sandbox.get("max_adapter_response_bytes")
    if not isinstance(max_profiles, int) or not 1 <= max_profiles <= 32 or max_profiles < len(baseline):
        raise QAError("max_profiles_invalid")
    if not isinstance(max_response, int) or not 4096 <= max_response <= 4_000_000:
        raise QAError("sandbox_response_budget_invalid")
    heartbeat = ledger.get("heartbeat_seconds", 900)
    retry_delay = ledger.get("retry_delay_seconds", 60)
    if not isinstance(heartbeat, int) or not 15 <= heartbeat <= 3600 or not isinstance(retry_delay, int) or not 0 <= retry_delay <= 3600:
        raise QAError("ledger_budget_invalid")
    return value


def validate_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("job_kind") != "engineering.qa":
        raise QAError("job_kind_invalid")
    for key in ("job_id", "lease_token"):
        try:
            uuid.UUID(str(value.get(key)))
        except ValueError as exc:
            raise QAError(f"invalid_{key}") from exc
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise QAError("payload_invalid")
    for key in ("review_job_id",):
        try:
            uuid.UUID(str(payload.get(key)))
        except ValueError as exc:
            raise QAError(f"{key}_invalid") from exc
    for key in ("base_commit", "head_commit"):
        if not isinstance(payload.get(key), str) or not HEX40.fullmatch(payload[key]):
            raise QAError(f"{key}_invalid")
    if not isinstance(payload.get("branch"), str) or not BRANCH.fullmatch(payload["branch"]):
        raise QAError("branch_invalid")
    for key in ("review_evidence_sha256", "review_manifest_sha256"):
        if not isinstance(payload.get(key), str) or not HEX64.fullmatch(payload[key]):
            raise QAError(f"{key}_invalid")
    if not isinstance(payload.get("objective"), str) or not payload["objective"].strip():
        raise QAError("objective_invalid")
    required = payload.get("required_profile_ids", [])
    if not isinstance(required, list) or not all(isinstance(x, str) and SAFE_PROFILE.fullmatch(x) for x in required):
        raise QAError("required_profile_ids_invalid")
    return value


class GitBudget:
    def __init__(self, maximum: int, started: float, wall: int):
        self.maximum, self.started, self.wall, self.used = maximum, started, wall, 0

    def _charge(self) -> None:
        self.used += 1
        if self.used > self.maximum:
            raise QAError("git_command_budget_exhausted")

    def read(self, repo: Path, args: list[str], *, integrity: bool = False) -> bytes:
        if not args or args[0] not in READ_ONLY_GIT:
            raise QAError("git_command_not_readonly")
        self._charge()
        return require_ok(run(["git", *args], cwd=repo, timeout=min(30, remaining(self.started, self.wall)), env=minimal_env()), "git_" + args[0], integrity=integrity)

    def clone_detached(self, source: Path, workspace: Path, head: str) -> None:
        self._charge()
        require_ok(run(["git", "clone", "--quiet", "--no-checkout", "--local", str(source), str(workspace)], cwd=source.parent,
                       timeout=min(60, remaining(self.started, self.wall)), env=minimal_env()), "qa_clone")
        self._charge()
        require_ok(run(["git", "checkout", "--quiet", "--detach", head], cwd=workspace,
                       timeout=min(30, remaining(self.started, self.wall)), env=minimal_env()), "qa_detached_checkout")


def call_gate(envelope: dict[str, Any], source: Path, command: list[str], started: float, wall: int, model_enabled: bool) -> dict[str, Any]:
    request = {
        "request_id": f"b034:{envelope['job_id']}", "actor": "b034-adversarial-qa",
        "action_type": "sandboxed_qa_execute", "risk_level": "L1", "environment": "isolated",
        "data_classification": envelope.get("data_classification", "INTERNAL"),
        "target": sha256_bytes(str(source.resolve()).encode()), "side_effecting": True,
        "reversible": True, "external_side_effect": False, "uses_llm": model_enabled,
        "metadata": {"job_id": envelope["job_id"], "head_commit": envelope["payload"]["head_commit"]},
    }
    cp = run(command, cwd=source, timeout=min(10, remaining(started, wall)), input_bytes=canonical(request) + b"\n",
             env=minimal_env(), max_output=65536)
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise QAError("gate_response_invalid") from exc
    decision = response.get("decision")
    if decision not in {"allow", "approval_required", "deny"}:
        raise QAError("gate_decision_invalid")
    for key in ("request_sha256", "policy_sha256"):
        if not isinstance(response.get(key), str) or not HEX64.fullmatch(response[key]):
            raise QAError(f"gate_evidence_invalid:{key}")
    try:
        uuid.UUID(str(response.get("event_id")))
    except ValueError as exc:
        raise QAError("gate_evidence_invalid:event_id") from exc
    if cp.returncode != {"allow": 0, "approval_required": 20, "deny": 30}[decision]:
        raise QAError("gate_exit_decision_mismatch")
    return response


def call_ledger(config: dict[str, Any], request: dict[str, Any], cwd: Path, started: float, wall: int) -> dict[str, Any]:
    cp = run(config["ledger"]["adapter_command"], cwd=cwd, timeout=min(10, remaining(started, wall)), input_bytes=canonical(request) + b"\n",
             env=ledger_env(), max_output=131072)
    if cp.returncode != 0:
        raise QAError("ledger_adapter_failed")
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise QAError("ledger_response_invalid") from exc
    if not isinstance(response, dict):
        raise QAError("ledger_response_not_object")
    return response


def ledger_request(operation: str, envelope: dict[str, Any], **extra: Any) -> dict[str, Any]:
    result = {"operation": operation, "job_id": envelope["job_id"], "lease_token": envelope["lease_token"]}
    result.update(extra)
    return result


def verify_review_bundle(directory: Path, payload: dict[str, Any], maximum: int) -> tuple[dict[str, Any], bytes]:
    if not directory.is_dir() or directory.is_symlink():
        raise QAIntegrityError("review_evidence_directory_invalid")
    evidence_path, manifest_path = directory / "review-evidence.json", directory / "manifest.json"
    if not evidence_path.is_file() or evidence_path.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise QAIntegrityError("required_review_evidence_missing")
    if evidence_path.stat().st_size + manifest_path.stat().st_size > maximum:
        raise QAIntegrityError("review_evidence_budget_exhausted")
    if sha256_file(evidence_path) != payload["review_evidence_sha256"]:
        raise QAIntegrityError("review_evidence_anchor_mismatch")
    if sha256_file(manifest_path) != payload["review_manifest_sha256"]:
        raise QAIntegrityError("review_manifest_anchor_mismatch")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QAIntegrityError("review_evidence_json_invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), dict):
        raise QAIntegrityError("review_manifest_schema_invalid")
    expected = set(manifest["files"])
    actual = {p.name for p in directory.iterdir() if p.is_file() and not p.is_symlink()}
    if actual != expected | {"manifest.json"}:
        raise QAIntegrityError("review_evidence_file_set_mismatch")
    for name, metadata in manifest["files"].items():
        if PurePosixPath(name).name != name or not isinstance(metadata, dict):
            raise QAIntegrityError("review_manifest_filename_invalid")
        path = directory / name
        if not path.is_file() or path.is_symlink() or metadata.get("sha256") != sha256_file(path) or metadata.get("bytes") != path.stat().st_size:
            raise QAIntegrityError(f"review_artifact_hash_mismatch:{name}")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise QAIntegrityError("review_evidence_schema_invalid")
    if evidence.get("job_id") != payload["review_job_id"]:
        raise QAIntegrityError("review_job_binding_mismatch")
    if evidence.get("verdict") != "approve" or evidence.get("integrity_verified") is not True:
        raise QAIntegrityError("review_not_approved")
    if evidence.get("promotion_authorized") is not False or evidence.get("next_stage") != "B034 adversarial QA":
        raise QAIntegrityError("review_handoff_boundary_invalid")
    for key in ("base_commit", "head_commit", "branch"):
        if evidence.get(key) != payload[key]:
            raise QAIntegrityError(f"review_{key}_binding_mismatch")
    diff_path = directory / "reconstructed.diff"
    if not diff_path.is_file() or diff_path.is_symlink():
        raise QAIntegrityError("review_reconstructed_diff_missing")
    diff = diff_path.read_bytes()
    if len(diff) > maximum or evidence.get("reconstructed_diff_sha256") != sha256_bytes(diff):
        raise QAIntegrityError("review_reconstructed_diff_mismatch")
    return evidence, diff


def verify_candidate(git: GitBudget, source: Path, payload: dict[str, Any]) -> None:
    base, head, branch = payload["base_commit"], payload["head_commit"], payload["branch"]
    for name, sha in (("base", base), ("head", head)):
        if git.read(source, ["rev-parse", "--verify", f"{sha}^{{commit}}"], integrity=True).decode().strip() != sha:
            raise QAIntegrityError(f"{name}_commit_mismatch")
    if git.read(source, ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"], integrity=True).decode().strip() != head:
        raise QAIntegrityError("candidate_branch_head_mismatch")
    cp = run(["git", "merge-base", "--is-ancestor", base, head], cwd=source, timeout=30, env=minimal_env())
    git._charge()
    if cp.returncode != 0:
        raise QAIntegrityError("candidate_base_not_ancestor")
    parents = git.read(source, ["rev-list", "--parents", "-n", "1", head], integrity=True).decode().strip().split()
    if len(parents) != 2 or parents != [head, base]:
        raise QAIntegrityError("candidate_history_changed")


def validate_model_selection(value: Any, config: dict[str, Any]) -> tuple[list[str], dict[str, str], list[str], str]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise QAError("qa_model_schema_invalid")
    if not isinstance(value.get("summary"), str) or not value["summary"].strip() or len(value["summary"]) > 4000:
        raise QAError("qa_model_summary_invalid")
    profiles = value.get("profiles")
    if not isinstance(profiles, list) or len(profiles) > config["model"]["max_profile_suggestions"]:
        raise QAError("qa_model_profiles_invalid")
    allowed = set(config["sandbox"]["allowed_profile_ids"])
    accepted: list[str] = []
    hypotheses: dict[str, str] = {}
    rejected: list[str] = []
    for item in profiles:
        if not isinstance(item, dict) or set(item) != {"id", "hypothesis"}:
            raise QAError("qa_model_profile_shape_invalid")
        profile_id, hypothesis = item["id"], item["hypothesis"]
        if not isinstance(profile_id, str) or not SAFE_PROFILE.fullmatch(profile_id) or not isinstance(hypothesis, str) or not hypothesis.strip() or len(hypothesis) > 2000:
            raise QAError("qa_model_profile_value_invalid")
        if profile_id in allowed:
            if profile_id not in accepted:
                accepted.append(profile_id)
                hypotheses[profile_id] = hypothesis
        else:
            rejected.append(profile_id)
    return accepted, hypotheses, rejected, value["summary"]


def call_model(config: dict[str, Any], prompt: dict[str, Any], source: Path, started: float, wall: int) -> tuple[list[str], dict[str, str], list[str], dict[str, Any]]:
    model = config["model"]
    prompt_bytes = canonical(prompt) + b"\n"
    if len(prompt_bytes) > model["max_prompt_bytes"]:
        raise QAError("qa_model_prompt_budget_exhausted")
    env = minimal_env({"FZH_MODEL_ALIAS": str(model.get("alias", "fzh-free-auto")), "FZH_MODEL_MAX_OUTPUT_TOKENS": str(model["max_output_tokens"])})
    for key in ("FZH_LITELLM_BASE_URL", "FZH_LITELLM_API_KEY"):
        if key in os.environ:
            env[key] = os.environ[key]
    cp = run(model["adapter_command"], cwd=source, timeout=min(model["timeout_seconds"], remaining(started, wall)),
             input_bytes=prompt_bytes, env=env, max_output=model["max_response_bytes"])
    if cp.returncode != 0:
        raise QAError("qa_model_adapter_failed")
    try:
        envelope = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise QAError("qa_model_invalid_json") from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("selection"), dict):
        raise QAError("qa_model_envelope_invalid")
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    if isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] > model["max_output_tokens"]:
        raise QAError("qa_model_output_token_budget_exceeded")
    accepted, hypotheses, rejected, summary = validate_model_selection(envelope["selection"], config)
    metadata = {"id": str(envelope.get("model", model.get("alias", "unknown"))), "summary": summary,
                "prompt_bytes": len(prompt_bytes), "response_bytes": len(cp.stdout), "reported_usage": usage}
    return accepted, hypotheses, rejected, metadata


def call_sandbox(config: dict[str, Any], job_id: str, workspace: Path, profile_id: str, source: Path, started: float, wall: int) -> dict[str, Any]:
    request = {"operation": "run_profile", "job_id": job_id, "workspace": str(workspace), "profile_id": profile_id}
    cp = run(config["sandbox"]["adapter_command"], cwd=source, timeout=remaining(started, wall), input_bytes=canonical(request) + b"\n",
             env=minimal_env(), max_output=config["sandbox"]["max_adapter_response_bytes"])
    if cp.returncode != 0:
        raise QAError("qa_sandbox_infrastructure_failure")
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise QAError("qa_sandbox_response_invalid") from exc
    if not isinstance(response, dict) or response.get("schema_version") != 1 or response.get("profile_id") != profile_id:
        raise QAError("qa_sandbox_response_schema_invalid")
    if response.get("status") not in {"pass", "fail", "timeout", "output_exhausted"}:
        raise QAError("qa_sandbox_status_invalid")
    if response.get("network") != "unshared" or response.get("workspace_mode") != "tmpfs-copy-from-readonly-candidate":
        raise QAError("qa_sandbox_isolation_evidence_invalid")
    if not isinstance(response.get("stdout"), str) or not isinstance(response.get("stderr"), str):
        raise QAError("qa_sandbox_logs_invalid")
    return response


def write_bundle(output: Path, evidence: dict[str, Any], model_selection: dict[str, Any] | None, logs: dict[str, bytes]) -> dict[str, str]:
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name, data in logs.items():
        (output / name).write_bytes(data)
    if model_selection is not None:
        (output / "qa-model.json").write_text(json.dumps(model_selection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence_path = output / "qa-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in sorted(output.iterdir()) if p.is_file()}
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "files": files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"evidence_sha256": sha256_file(evidence_path), "manifest_sha256": sha256_file(manifest)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-envelope", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--review-evidence-dir", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--qa-output-root", type=Path, required=True)
    parser.add_argument("--gate-command", nargs="+", default=DEFAULT_GATE)
    args = parser.parse_args()

    started = time.monotonic()
    envelope = validate_envelope(load_json(args.job_envelope))
    config = validate_config(load_json(args.config, 256 * 1024))
    payload = envelope["payload"]
    source, review_dir = args.source_repo.resolve(), args.review_evidence_dir.resolve()
    workspace_root, output_root = args.workspace_root.resolve(), args.qa_output_root.resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    workspace = workspace_root / str(envelope["job_id"])
    output = output_root / str(envelope["job_id"])
    if workspace.exists() or output.exists():
        raise QAError("qa_workspace_or_output_exists")
    git = GitBudget(config["budgets"]["max_git_commands"], started, config["budgets"]["max_wall_seconds"])
    top = git.read(source, ["rev-parse", "--show-toplevel"], integrity=True).decode().strip()
    if Path(top).resolve() != source:
        raise QAError("source_repo_must_be_toplevel")

    gate = call_gate(envelope, source, args.gate_command, started, config["budgets"]["max_wall_seconds"], config["model"]["enabled"])
    gate_record = call_ledger(config, ledger_request(
        "record_gate", envelope, decision=gate["decision"], request_sha256=gate["request_sha256"], policy_sha256=gate["policy_sha256"],
        audit_event_id=gate["event_id"], reason=str(gate.get("reason", "unknown")),
    ), source, started, config["budgets"]["max_wall_seconds"])
    if gate["decision"] != "allow":
        expected = "waiting_approval" if gate["decision"] == "approval_required" else "denied"
        if gate_record.get("status") != expected:
            raise QAError("qa_gate_ledger_state_mismatch")
        raise QAError(f"gate_not_allowed:{gate['decision']}")
    if gate_record.get("status") != "leased":
        raise QAError("qa_gate_allow_not_leased")
    started_job = call_ledger(config, ledger_request("start", envelope), source, started, config["budgets"]["max_wall_seconds"])
    if started_job.get("status") != "running":
        raise QAError("qa_ledger_start_failed")
    execution_started = True

    try:
        heartbeat = call_ledger(config, ledger_request("heartbeat", envelope, lease_seconds=config["ledger"]["heartbeat_seconds"]), source, started, config["budgets"]["max_wall_seconds"])
        if not heartbeat.get("lease_expires_at"):
            raise QAError("qa_ledger_heartbeat_failed")
        try:
            review, review_diff = verify_review_bundle(review_dir, payload, config["budgets"]["max_evidence_bytes"])
            verify_candidate(git, source, payload)
        except QAIntegrityError as integrity:
            evidence = {
                "schema_version": 1, "job_id": envelope["job_id"], "review_job_id": payload["review_job_id"],
                "qa_status": "reject", "admission_verified": False, "admission_error": str(integrity),
                "candidate_executed": False, "promotion_authorized": False, "next_stage": None,
                "gate": {k: gate.get(k) for k in ("request_sha256", "policy_sha256", "event_id", "reason")},
            }
            hashes = write_bundle(output, evidence, None, {})
            completed = call_ledger(config, ledger_request("complete", envelope, result={"qa_status": "reject", **hashes, "promotion_authorized": False}), source, started, config["budgets"]["max_wall_seconds"])
            if completed.get("status") != "succeeded":
                raise QAError("qa_ledger_complete_failed")
            print(str(output))
            return 0

        required = payload.get("required_profile_ids", [])
        allowed = set(config["sandbox"]["allowed_profile_ids"])
        if not set(required).issubset(allowed):
            raise QAIntegrityError("required_profile_not_allowed")
        selected: list[str] = []
        for profile_id in [*config["sandbox"]["baseline_profile_ids"], *required]:
            if profile_id not in selected:
                selected.append(profile_id)
        model_record: dict[str, Any] | None = None
        rejected_suggestions: list[str] = []
        hypotheses: dict[str, str] = {}
        if config["model"]["enabled"]:
            prompt = {
                "schema_version": 1, "qa_job_id": envelope["job_id"], "objective": payload["objective"],
                "allowed_profile_ids": config["sandbox"]["allowed_profile_ids"],
                "already_selected_profile_ids": selected, "review_findings": review.get("findings", []),
                "changed_paths": review.get("changed_paths", []), "reconstructed_diff": review_diff.decode("utf-8", errors="replace"),
                "instruction": "Propose only additional profile IDs from allowed_profile_ids with adversarial hypotheses. Never output commands or a verdict.",
            }
            proposed, hypotheses, rejected_suggestions, model_record = call_model(config, prompt, source, started, config["budgets"]["max_wall_seconds"])
            for profile_id in proposed:
                if profile_id not in selected:
                    selected.append(profile_id)
        if len(selected) > config["sandbox"]["max_profiles"]:
            raise QAError("qa_profile_budget_exhausted")

        git.clone_detached(source, workspace, payload["head_commit"])
        if git.read(workspace, ["rev-parse", "HEAD"], integrity=True).decode().strip() != payload["head_commit"]:
            raise QAIntegrityError("qa_clone_head_mismatch")
        if git.read(workspace, ["status", "--porcelain=v1", "--untracked-files=all"], integrity=True).strip():
            raise QAIntegrityError("qa_clone_not_clean")

        logs: dict[str, bytes] = {}
        profile_results: list[dict[str, Any]] = []
        failed = False
        for profile_id in selected:
            result = call_sandbox(config, envelope["job_id"], workspace, profile_id, source, started, config["budgets"]["max_wall_seconds"])
            log_bytes = (result["stdout"] + "\n--- STDERR ---\n" + result["stderr"]).encode("utf-8")
            log_name = f"profile-{profile_id}.log"
            logs[log_name] = log_bytes
            profile_results.append({
                "profile_id": profile_id, "status": result["status"], "returncode": result.get("returncode"),
                "duration_seconds": result.get("duration_seconds"), "log_sha256": sha256_bytes(log_bytes),
                "log_bytes": len(log_bytes), "resource_policy": result.get("resource_policy", {}),
                "hypothesis": hypotheses.get(profile_id), "network": result.get("network"),
                "workspace_mode": result.get("workspace_mode"),
            })
            if result["status"] != "pass":
                failed = True
            if git.read(workspace, ["status", "--porcelain=v1", "--untracked-files=all"], integrity=True).strip():
                raise QAIntegrityError("sandbox_modified_host_candidate_clone")
        qa_status = "qa_failed" if failed else "qa_passed"
        evidence = {
            "schema_version": 1, "job_id": envelope["job_id"], "review_job_id": payload["review_job_id"],
            "base_commit": payload["base_commit"], "head_commit": payload["head_commit"], "branch": payload["branch"],
            "review_evidence_sha256": payload["review_evidence_sha256"], "review_manifest_sha256": payload["review_manifest_sha256"],
            "admission_verified": True, "qa_status": qa_status, "selected_profile_ids": selected,
            "rejected_model_profile_ids": rejected_suggestions, "profiles": profile_results,
            "model": model_record, "git_commands": git.used,
            "gate": {k: gate.get(k) for k in ("request_sha256", "policy_sha256", "event_id", "reason", "observed_at_utc")},
            "promotion_authorized": False, "next_stage": "B035 security review" if qa_status == "qa_passed" else None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        hashes = write_bundle(output, evidence, model_record, logs)
        completed = call_ledger(config, ledger_request("complete", envelope, result={"qa_status": qa_status, "head_commit": payload["head_commit"], **hashes, "promotion_authorized": False}), source, started, config["budgets"]["max_wall_seconds"])
        if completed.get("status") != "succeeded":
            raise QAError("qa_ledger_complete_failed")
        print(str(output))
        return 0
    except Exception as exc:
        if execution_started:
            try:
                call_ledger(config, ledger_request("fail", envelope, error_code="b034_qa_failed", error_summary=str(exc)[:1800], retryable=True, retry_delay_seconds=config["ledger"]["retry_delay_seconds"]), source, started, config["budgets"]["max_wall_seconds"])
            except Exception:
                pass
        raise
    finally:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QAError as exc:
        print(f"B034 QA: {exc}", file=sys.stderr)
        raise SystemExit(2)

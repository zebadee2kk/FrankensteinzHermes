#!/usr/bin/env python3
"""B032 bounded engineering implementation worker.

Consumes one already-leased B031 engineering job, records its exact B030 gate
response through a constrained ledger adapter, edits only a detached worktree,
and creates a durable branch/commit only after model validation and tests pass.
It never pushes, opens/merges PRs, deploys, uses sudo/Docker/SSH, or writes main.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
DEFAULT_GATE = ["/usr/local/libexec/frankensteinzhermes/action-gate-client"]
MAX_CONFIG_BYTES = 256 * 1024
MAX_TASK_BYTES = 1024 * 1024


class WorkerError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path, max_bytes: int) -> Any:
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise WorkerError(f"input_too_large:{path.name}")
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise WorkerError(f"invalid_json:{path.name}:{exc.msg}") from exc


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
    for key in (
        "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
        "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGSSLMODE", "PGSSLROOTCERT",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def remaining(started: float, wall: int) -> int:
    left = wall - int(time.monotonic() - started)
    if left <= 0:
        raise WorkerError("wall_clock_budget_exhausted")
    return left


def run_command(cmd: list[str], *, cwd: Path, timeout: int, input_bytes: bytes | None = None,
                env: dict[str, str] | None = None, max_output: int = 2_000_000) -> subprocess.CompletedProcess[bytes]:
    if not cmd or not all(isinstance(x, str) and x for x in cmd):
        raise WorkerError("invalid_command")
    try:
        cp = subprocess.run(
            cmd, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(f"command_timeout:{cmd[0]}") from exc
    if len(cp.stdout) + len(cp.stderr) > max_output:
        raise WorkerError(f"command_output_too_large:{cmd[0]}")
    return cp


def require_ok(cp: subprocess.CompletedProcess[bytes], label: str) -> bytes:
    if cp.returncode != 0:
        tail = cp.stderr.decode("utf-8", errors="replace")[-2000:]
        raise WorkerError(f"{label}_failed:{tail}")
    return cp.stdout


def validate_rel_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise WorkerError("invalid_path")
    p = PurePosixPath(raw)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise WorkerError(f"unsafe_path:{raw}")
    if ".git" in p.parts:
        raise WorkerError(f"git_metadata_path_denied:{raw}")
    return p.as_posix()


def path_allowed(path: str, payload: dict[str, Any], config: dict[str, Any]) -> bool:
    path = validate_rel_path(path)
    forbidden = {validate_rel_path(x) for x in config.get("forbidden_paths", [])}
    prefixes = [validate_rel_path(x.rstrip("/")) for x in config.get("forbidden_path_prefixes", [])]
    if path in forbidden or any(path == p or path.startswith(p + "/") for p in prefixes):
        return False
    allowed = payload.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed:
        return False
    return any(
        path == validate_rel_path(raw.rstrip("/"))
        or path.startswith(validate_rel_path(raw.rstrip("/")) + "/")
        for raw in allowed
    )


def ensure_no_symlink_components(worktree: Path, path: str) -> None:
    current = worktree
    parts = PurePosixPath(path).parts
    for part in parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise WorkerError(f"symlink_parent_denied:{path}")
    target = worktree / path
    if target.exists() and target.is_symlink():
        raise WorkerError(f"symlink_target_denied:{path}")


def parse_patch_paths(patch: bytes) -> list[str]:
    if b"\x00" in patch:
        raise WorkerError("patch_contains_nul")
    try:
        text = patch.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkerError("patch_not_utf8") from exc
    if "new file mode 120000" in text or "new mode 120000" in text:
        raise WorkerError("symlink_patch_denied")
    if "new file mode 160000" in text or "new mode 160000" in text:
        raise WorkerError("submodule_patch_denied")
    paths: list[str] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
                raise WorkerError("invalid_diff_header")
            before = validate_rel_path(parts[2][2:])
            after = validate_rel_path(parts[3][2:])
            if before != after:
                raise WorkerError("rename_or_copy_not_supported")
            paths.append(after)
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            raise WorkerError("rename_or_copy_not_supported")
        elif (line.startswith("--- /") or line.startswith("+++ /")) and line not in ("--- /dev/null", "+++ /dev/null"):
            raise WorkerError("absolute_patch_path")
    if not paths:
        raise WorkerError("patch_has_no_files")
    if len(paths) != len(set(paths)):
        raise WorkerError("duplicate_patch_file")
    return paths


def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorkerError("config_schema_invalid")
    ledger = value.get("ledger")
    model = value.get("model")
    budgets = value.get("budgets")
    allowlist = value.get("test_allowlist")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("adapter_command"), list) or not ledger["adapter_command"]:
        raise WorkerError("ledger_config_invalid")
    if not all(isinstance(x, str) and x for x in ledger["adapter_command"]):
        raise WorkerError("ledger_adapter_command_invalid")
    if not isinstance(ledger.get("heartbeat_seconds"), int) or not 60 <= ledger["heartbeat_seconds"] <= 3600:
        raise WorkerError("ledger_heartbeat_invalid")
    if not isinstance(ledger.get("retry_delay_seconds"), int) or not 0 <= ledger["retry_delay_seconds"] <= 3600:
        raise WorkerError("ledger_retry_delay_invalid")
    if not isinstance(model, dict) or not isinstance(model.get("adapter_command"), list) or not model["adapter_command"]:
        raise WorkerError("model_config_invalid")
    if not all(isinstance(x, str) and x for x in model["adapter_command"]):
        raise WorkerError("model_adapter_command_invalid")
    if not isinstance(allowlist, dict):
        raise WorkerError("test_allowlist_invalid")
    for name, cmd in allowlist.items():
        if not SAFE_ID.fullmatch(str(name)) or not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise WorkerError("test_allowlist_entry_invalid")
    if not isinstance(budgets, dict):
        raise WorkerError("budgets_invalid")
    for key, low, high in (
        ("max_wall_seconds", 30, 3600), ("max_tool_commands", 1, 100),
        ("max_changed_files", 1, 100), ("max_changed_bytes", 1, 2_000_000),
    ):
        v = budgets.get(key)
        if not isinstance(v, int) or not low <= v <= high:
            raise WorkerError(f"budget_invalid:{key}")
    for key, low, high in (
        ("max_calls", 1, 5), ("max_context_bytes", 1024, 512_000),
        ("max_prompt_bytes", 4096, 768_000), ("max_output_tokens", 128, 8192),
        ("max_response_bytes", 1024, 1_000_000), ("timeout_seconds", 5, 300),
    ):
        v = model.get(key)
        if not isinstance(v, int) or not low <= v <= high:
            raise WorkerError(f"model_budget_invalid:{key}")
    return value


def validate_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("job_kind") != "engineering.implement":
        raise WorkerError("job_kind_invalid")
    try:
        uuid.UUID(str(value.get("job_id")))
        uuid.UUID(str(value.get("lease_token")))
    except ValueError as exc:
        raise WorkerError("job_uuid_invalid") from exc
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise WorkerError("payload_invalid")
    if not isinstance(payload.get("objective"), str) or not payload["objective"].strip():
        raise WorkerError("objective_invalid")
    base = payload.get("base_commit")
    if not isinstance(base, str) or not HEX40.fullmatch(base):
        raise WorkerError("base_commit_must_be_sha40")
    allowed = payload.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed:
        raise WorkerError("allowed_paths_required")
    for path in allowed:
        validate_rel_path(str(path).rstrip("/"))
    if not isinstance(payload.get("context_paths", []), list):
        raise WorkerError("context_paths_invalid")
    if not isinstance(payload.get("test_ids", []), list):
        raise WorkerError("test_ids_invalid")
    if payload.get("output_branch") in ("main", "master"):
        raise WorkerError("production_branch_denied")
    return value


class ToolBudget:
    def __init__(self, max_commands: int, started: float, wall: int):
        self.max_commands, self.started, self.wall = max_commands, started, wall
        self.used = 0

    def git(self, repo: Path, args: list[str], *, timeout: int = 30, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        self.used += 1
        if self.used > self.max_commands:
            raise WorkerError("tool_budget_exhausted")
        return run_command(["git", *args], cwd=repo, timeout=min(timeout, remaining(self.started, self.wall)), input_bytes=input_bytes, env=minimal_env())

    def test(self, repo: Path, cmd: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[bytes]:
        self.used += 1
        if self.used > self.max_commands:
            raise WorkerError("tool_budget_exhausted")
        return run_command(cmd, cwd=repo, timeout=min(timeout, remaining(self.started, self.wall)), env=minimal_env())


def build_context(worktree: Path, payload: dict[str, Any], config: dict[str, Any]) -> list[dict[str, str]]:
    limit = config["model"]["max_context_bytes"]
    used = 0
    result: list[dict[str, str]] = []
    for raw in payload.get("context_paths", []):
        path = validate_rel_path(raw)
        if not path_allowed(path, payload, config):
            raise WorkerError(f"context_path_not_allowed:{path}")
        ensure_no_symlink_components(worktree, path)
        target = worktree / path
        if not target.is_file() or target.is_symlink():
            raise WorkerError(f"context_path_not_regular_file:{path}")
        data = target.read_bytes()
        used += len(data)
        if used > limit:
            raise WorkerError("model_context_budget_exhausted")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkerError(f"context_not_utf8:{path}") from exc
        result.append({"path": path, "sha256": sha256_bytes(data), "content": text})
    return result


def call_gate(envelope: dict[str, Any], source: Path, gate_cmd: list[str], started: float, wall: int) -> dict[str, Any]:
    payload = envelope["payload"]
    request = {
        "request_id": f"b032:{envelope['job_id']}", "actor": "b032-engineering-worker",
        "action_type": "workspace_branch_write", "risk_level": "L1", "environment": "isolated",
        "data_classification": envelope.get("data_classification", "INTERNAL"),
        "target": sha256_bytes(str(source.resolve()).encode()), "side_effecting": True,
        "reversible": True, "external_side_effect": False, "uses_llm": True,
        "metadata": {"job_id": envelope["job_id"], "base_commit": payload["base_commit"]},
    }
    cp = run_command(gate_cmd, cwd=source, timeout=min(10, remaining(started, wall)), input_bytes=canonical(request) + b"\n", env=minimal_env(), max_output=65536)
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("gate_response_invalid") from exc
    if response.get("decision") not in ("allow", "approval_required", "deny"):
        raise WorkerError("gate_decision_invalid")
    for key in ("request_sha256", "policy_sha256"):
        if not isinstance(response.get(key), str) or not HEX64.fullmatch(response[key]):
            raise WorkerError(f"gate_evidence_invalid:{key}")
    try:
        uuid.UUID(str(response.get("event_id")))
    except ValueError as exc:
        raise WorkerError("gate_evidence_invalid:event_id") from exc
    expected_rc = {"allow": 0, "approval_required": 20, "deny": 30}[response["decision"]]
    if cp.returncode != expected_rc:
        raise WorkerError("gate_exit_decision_mismatch")
    return response


def call_ledger(config: dict[str, Any], request: dict[str, Any], cwd: Path, started: float, wall: int) -> dict[str, Any]:
    cp = run_command(
        config["ledger"]["adapter_command"], cwd=cwd, timeout=min(10, remaining(started, wall)),
        input_bytes=canonical(request) + b"\n", env=ledger_env(), max_output=131072,
    )
    if cp.returncode != 0:
        raise WorkerError("ledger_adapter_failed")
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("ledger_response_invalid") from exc
    if not isinstance(response, dict):
        raise WorkerError("ledger_response_not_object")
    return response


def ledger_request(operation: str, envelope: dict[str, Any], **extra: Any) -> dict[str, Any]:
    value = {"operation": operation, "job_id": envelope["job_id"], "lease_token": envelope["lease_token"]}
    value.update(extra)
    return value


def call_model(config: dict[str, Any], prompt: dict[str, Any], cwd: Path, usage: dict[str, Any], started: float, wall: int) -> bytes:
    model = config["model"]
    usage["model_calls"] += 1
    if usage["model_calls"] > model["max_calls"]:
        raise WorkerError("model_call_budget_exhausted")
    prompt_bytes = canonical(prompt) + b"\n"
    if len(prompt_bytes) > model["max_prompt_bytes"]:
        raise WorkerError("model_prompt_budget_exhausted")
    usage["model_prompt_bytes"] += len(prompt_bytes)
    env = minimal_env({"FZH_MODEL_ALIAS": str(model.get("alias", "fzh-free-auto")), "FZH_MODEL_MAX_OUTPUT_TOKENS": str(model["max_output_tokens"])})
    for key in ("FZH_LITELLM_BASE_URL", "FZH_LITELLM_API_KEY"):
        if key in os.environ:
            env[key] = os.environ[key]
    cp = run_command(model["adapter_command"], cwd=cwd, timeout=min(model["timeout_seconds"], remaining(started, wall)), input_bytes=prompt_bytes, env=env, max_output=model["max_response_bytes"])
    if cp.returncode != 0:
        raise WorkerError("model_adapter_failed")
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("model_response_invalid_json") from exc
    if not isinstance(response.get("patch"), str):
        raise WorkerError("model_patch_missing")
    patch = response["patch"].encode("utf-8")
    if len(patch) > model["max_response_bytes"]:
        raise WorkerError("model_patch_too_large")
    provider_usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    if isinstance(provider_usage.get("completion_tokens"), int) and provider_usage["completion_tokens"] > model["max_output_tokens"]:
        raise WorkerError("model_reported_output_token_budget_exceeded")
    usage.update({"model_response_bytes": usage["model_response_bytes"] + len(patch), "model_id": str(response.get("model", model.get("alias", "unknown"))), "provider_usage": provider_usage})
    return patch


def cleanup_worktree(source: Path, worktree: Path, branch: str, keep_branch: bool) -> None:
    if worktree.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, env=minimal_env())
    subprocess.run(["git", "worktree", "prune"], cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, env=minimal_env())
    if not keep_branch:
        subprocess.run(["git", "branch", "-D", branch], cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, env=minimal_env())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--job-envelope", required=True, type=Path)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--source-repo", required=True, type=Path)
    p.add_argument("--workspace-root", required=True, type=Path)
    p.add_argument("--evidence-root", required=True, type=Path)
    p.add_argument("--gate-command", nargs="+", default=DEFAULT_GATE)
    args = p.parse_args()

    started = time.monotonic()
    envelope = validate_envelope(load_json(args.job_envelope, MAX_TASK_BYTES))
    config = validate_config(load_json(args.config, MAX_CONFIG_BYTES))
    payload = envelope["payload"]
    budgets = config["budgets"]
    source = args.source_repo.resolve()
    workspace_root, evidence_root = args.workspace_root.resolve(), args.evidence_root.resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    tool = ToolBudget(budgets["max_tool_commands"], started, budgets["max_wall_seconds"])

    top = require_ok(tool.git(source, ["rev-parse", "--show-toplevel"]), "source_repo").decode().strip()
    if Path(top).resolve() != source:
        raise WorkerError("source_repo_must_be_toplevel")
    current_branch = require_ok(tool.git(source, ["symbolic-ref", "--quiet", "--short", "HEAD"]), "source_branch").decode().strip()
    if not current_branch:
        raise WorkerError("source_detached_head_denied")
    if require_ok(tool.git(source, ["status", "--porcelain=v1", "--untracked-files=all"]), "source_status").strip():
        raise WorkerError("source_repo_dirty")
    base = payload["base_commit"]
    if require_ok(tool.git(source, ["rev-parse", "HEAD"]), "source_head").decode().strip() != base:
        raise WorkerError("source_head_not_requested_base")
    if require_ok(tool.git(source, ["rev-parse", "--verify", f"{base}^{{commit}}"]), "base_resolve").decode().strip() != base:
        raise WorkerError("base_commit_resolution_mismatch")

    gate = call_gate(envelope, source, args.gate_command, started, budgets["max_wall_seconds"])
    gate_record = call_ledger(config, ledger_request(
        "record_gate", envelope, decision=gate["decision"], request_sha256=gate["request_sha256"],
        policy_sha256=gate["policy_sha256"], audit_event_id=gate["event_id"], reason=str(gate.get("reason", "unknown")),
    ), source, started, budgets["max_wall_seconds"])
    if gate["decision"] != "allow":
        expected = "waiting_approval" if gate["decision"] == "approval_required" else "denied"
        if gate_record.get("status") != expected:
            raise WorkerError("ledger_gate_state_mismatch")
        raise WorkerError(f"gate_not_allowed:{gate['decision']}:{gate.get('reason','unknown')}")
    if gate_record.get("status") != "leased":
        raise WorkerError("ledger_gate_allow_not_leased")
    start_record = call_ledger(config, ledger_request("start", envelope), source, started, budgets["max_wall_seconds"])
    if start_record.get("status") != "running":
        raise WorkerError("ledger_start_failed")
    execution_started = True
    effect_started = False

    heartbeat = call_ledger(config, ledger_request("heartbeat", envelope, lease_seconds=config["ledger"]["heartbeat_seconds"]), source, started, budgets["max_wall_seconds"])
    if not heartbeat.get("lease_expires_at"):
        raise WorkerError("ledger_heartbeat_failed")

    short = str(envelope["job_id"]).replace("-", "")[:12]
    branch = f"fzh/job-{short}"
    if payload.get("output_branch") is not None and payload["output_branch"] != branch:
        raise WorkerError("output_branch_must_match_deterministic_job_branch")
    worktree = workspace_root / short
    evidence_dir = evidence_root / str(envelope["job_id"])
    if worktree.exists() or evidence_dir.exists():
        raise WorkerError("workspace_or_evidence_already_exists")
    evidence_dir.mkdir(mode=0o700)
    usage: dict[str, Any] = {"model_calls": 0, "model_prompt_bytes": 0, "model_response_bytes": 0, "model_id": None, "provider_usage": {}}
    worktree_added = False
    success = False
    branch_created = False

    try:
        # Detached until tests pass: model/test failures leave no durable branch ref.
        require_ok(tool.git(source, ["worktree", "add", "--detach", str(worktree), base]), "worktree_add")
        worktree_added = True
        if require_ok(tool.git(worktree, ["rev-parse", "HEAD"]), "worktree_head").decode().strip() != base:
            raise WorkerError("worktree_not_at_base")

        context = build_context(worktree, payload, config)
        prompt = {
            "schema_version": 1, "job_id": envelope["job_id"], "objective": payload["objective"],
            "base_commit": base, "allowed_paths": payload["allowed_paths"], "context": context,
            "instruction": "Return JSON with one patch string containing a unified git diff. Do not rename/copy, create symlinks/submodules, or touch paths outside allowed_paths.",
        }
        patch = call_model(config, prompt, worktree, usage, started, budgets["max_wall_seconds"])
        paths = parse_patch_paths(patch)
        if len(paths) > budgets["max_changed_files"]:
            raise WorkerError("changed_file_budget_exhausted")
        for path in paths:
            if not path_allowed(path, payload, config):
                raise WorkerError(f"path_not_allowed:{path}")
            ensure_no_symlink_components(worktree, path)

        patch_file = evidence_dir / "model.patch"
        patch_file.write_bytes(patch)
        require_ok(tool.git(worktree, ["apply", "--index", "--check", "--whitespace=error-all", "-"], input_bytes=patch), "git_apply_check")
        require_ok(tool.git(worktree, ["apply", "--index", "--whitespace=error-all", "-"], input_bytes=patch), "git_apply")
        staged = [validate_rel_path(x) for x in require_ok(tool.git(worktree, ["diff", "--cached", "--name-only", "--no-ext-diff"]), "staged_paths").decode().splitlines() if x]
        if sorted(staged) != sorted(paths):
            raise WorkerError("staged_paths_differ_from_patch")
        for path in staged:
            if not path_allowed(path, payload, config):
                raise WorkerError(f"staged_path_not_allowed:{path}")
            ensure_no_symlink_components(worktree, path)
            mode = require_ok(tool.git(worktree, ["ls-files", "-s", "--", path]), "index_mode").decode().strip()
            if mode.startswith("120000 ") or mode.startswith("160000 "):
                raise WorkerError(f"unsafe_index_mode:{path}")
        diff = require_ok(tool.git(worktree, ["diff", "--cached", "--binary", "--no-ext-diff"]), "staged_diff")
        if len(diff) > budgets["max_changed_bytes"]:
            raise WorkerError("changed_byte_budget_exhausted")
        if require_ok(tool.git(worktree, ["diff", "--name-only"]), "unstaged_check").strip() or require_ok(tool.git(worktree, ["ls-files", "--others", "--exclude-standard"]), "untracked_check").strip():
            raise WorkerError("unexpected_worktree_changes")

        tests: list[dict[str, Any]] = []
        for test_id in payload.get("test_ids", []):
            command = config["test_allowlist"].get(test_id)
            if command is None:
                raise WorkerError(f"test_not_allowlisted:{test_id}")
            result = tool.test(worktree, command)
            log = result.stdout + b"\n--- STDERR ---\n" + result.stderr
            log_path = evidence_dir / f"test-{test_id}.log"
            log_path.write_bytes(log)
            tests.append({"test_id": test_id, "command": command, "returncode": result.returncode, "log_sha256": sha256_file(log_path), "log_bytes": log_path.stat().st_size})
            if result.returncode != 0:
                raise WorkerError(f"test_failed:{test_id}")
        if require_ok(tool.git(worktree, ["diff", "--name-only"]), "post_test_unstaged_check").strip() or require_ok(tool.git(worktree, ["ls-files", "--others", "--exclude-standard"]), "post_test_untracked_check").strip():
            raise WorkerError("tests_modified_worktree")
        if sorted(x for x in require_ok(tool.git(worktree, ["diff", "--cached", "--name-only"]), "post_test_staged_check").decode().splitlines() if x) != sorted(staged):
            raise WorkerError("tests_modified_staged_set")

        # Durable local branch/commit is the B031 effect boundary.
        begin = call_ledger(config, ledger_request("begin_effect", envelope), source, started, budgets["max_wall_seconds"])
        if begin.get("state") != "started":
            raise WorkerError("ledger_effect_begin_failed")
        effect_started = True
        require_ok(tool.git(worktree, ["switch", "-c", branch]), "branch_create")
        branch_created = True
        tool.used += 1
        if tool.used > tool.max_commands:
            raise WorkerError("tool_budget_exhausted")
        commit_env = minimal_env({
            "GIT_AUTHOR_NAME": "FrankensteinzHermes B032", "GIT_AUTHOR_EMAIL": "b032@localhost",
            "GIT_COMMITTER_NAME": "FrankensteinzHermes B032", "GIT_COMMITTER_EMAIL": "b032@localhost",
        })
        require_ok(run_command(["git", "commit", "-m", f"B032 job {short}: implementation candidate"], cwd=worktree, timeout=min(30, remaining(started, budgets["max_wall_seconds"])), env=commit_env), "git_commit")
        head = require_ok(tool.git(worktree, ["rev-parse", "HEAD"]), "head_after_commit").decode().strip()
        parent = require_ok(tool.git(worktree, ["rev-parse", "HEAD^"]), "parent_after_commit").decode().strip()
        if parent != base or not HEX40.fullmatch(head):
            raise WorkerError("unexpected_commit_history")
        if require_ok(tool.git(worktree, ["status", "--porcelain=v1", "--untracked-files=all"]), "final_status").strip():
            raise WorkerError("worktree_dirty_after_commit")
        committed = call_ledger(config, ledger_request("commit_effect", envelope, head_commit=head), source, started, budgets["max_wall_seconds"])
        if committed.get("committed") is not True:
            raise WorkerError("ledger_effect_commit_failed")

        evidence = {
            "schema_version": 1, "job_id": envelope["job_id"], "attempt_id": envelope.get("attempt_id"),
            "branch": branch, "base_commit": base, "head_commit": head,
            "lease_token_sha256": sha256_bytes(str(envelope["lease_token"]).encode()),
            "patch_sha256": sha256_file(patch_file), "changed_paths": staged, "changed_bytes": len(diff),
            "context": [{"path": x["path"], "sha256": x["sha256"]} for x in context],
            "gate": {k: gate.get(k) for k in ("request_sha256", "policy_sha256", "event_id", "reason", "observed_at_utc")},
            "model": {"id": usage["model_id"], "calls": usage["model_calls"], "prompt_bytes": usage["model_prompt_bytes"], "response_bytes": usage["model_response_bytes"], "max_output_tokens": config["model"]["max_output_tokens"], "reported_usage": usage["provider_usage"]},
            "tools": {"commands": tool.used, "max_commands": tool.max_commands}, "tests": tests,
            "elapsed_seconds": round(time.monotonic() - started, 3), "promotion_authorized": False,
            "next_stage": "B033 independent reviewer",
        }
        evidence_path = evidence_dir / "implementation-evidence.json"
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_files = {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in sorted(evidence_dir.iterdir()) if p.is_file()}
        manifest_path = evidence_dir / "manifest.json"
        manifest_path.write_text(json.dumps({"schema_version": 1, "files": manifest_files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result_summary = {
            "branch": branch, "base_commit": base, "head_commit": head,
            "evidence_sha256": sha256_file(evidence_path), "manifest_sha256": sha256_file(manifest_path),
            "promotion_authorized": False,
        }
        completed = call_ledger(config, ledger_request("complete", envelope, result=result_summary), source, started, budgets["max_wall_seconds"])
        if completed.get("status") != "succeeded":
            raise WorkerError("ledger_complete_failed")
        success = True
        print(str(evidence_dir))
        return 0
    except Exception as exc:
        try:
            if execution_started:
                call_ledger(config, ledger_request(
                    "fail", envelope, error_code="b032_execution_failed", error_summary=str(exc)[:1800],
                    retryable=True, retry_delay_seconds=config["ledger"]["retry_delay_seconds"], effect_started=effect_started,
                ), source, started, budgets["max_wall_seconds"])
        except Exception as ledger_exc:
            failure_extra = {"ledger_fail_error": str(ledger_exc)}
        else:
            failure_extra = {}
        if evidence_dir.exists():
            (evidence_dir / "failure.json").write_text(json.dumps({
                "schema_version": 1, "job_id": envelope["job_id"], "error": str(exc),
                "effect_started": effect_started, "elapsed_seconds": round(time.monotonic() - started, 3),
                "promotion_authorized": False, **failure_extra,
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        if worktree_added:
            cleanup_worktree(source, worktree, branch, keep_branch=success and branch_created)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkerError as exc:
        print(f"B032 worker: {exc}", file=sys.stderr)
        raise SystemExit(2)

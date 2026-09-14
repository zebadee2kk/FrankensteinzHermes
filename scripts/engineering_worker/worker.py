#!/usr/bin/env python3
"""B032 bounded engineering implementation worker.

Input is one already-leased B031 engineering job envelope. The worker obtains
B030 admission, creates one isolated local Git worktree/branch, asks a trusted
model adapter for an untrusted unified diff, validates/applies it, runs only
allowlisted checks, commits locally, writes evidence, and stops. It contains no
push, PR, merge, deploy, sudo, Docker, package-manager or SSH path.
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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    if extra:
        env.update(extra)
    return env


def remaining(started: float, max_wall_seconds: int) -> int:
    value = max_wall_seconds - int(time.monotonic() - started)
    if value <= 0:
        raise WorkerError("wall_clock_budget_exhausted")
    return value


def run_command(
    cmd: list[str], *, cwd: Path, timeout: int, input_bytes: bytes | None = None,
    env: dict[str, str] | None = None, max_output: int = 2_000_000,
) -> subprocess.CompletedProcess[bytes]:
    if not cmd or not all(isinstance(item, str) and item for item in cmd):
        raise WorkerError("invalid_command")
    try:
        result = subprocess.run(
            cmd, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(f"command_timeout:{cmd[0]}") from exc
    if len(result.stdout) + len(result.stderr) > max_output:
        raise WorkerError(f"command_output_too_large:{cmd[0]}")
    return result


def require_ok(result: subprocess.CompletedProcess[bytes], label: str) -> bytes:
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", errors="replace")[-2000:]
        raise WorkerError(f"{label}_failed:{tail}")
    return result.stdout


def validate_rel_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise WorkerError("invalid_path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise WorkerError(f"unsafe_path:{raw}")
    if ".git" in path.parts:
        raise WorkerError(f"git_metadata_path_denied:{raw}")
    return path.as_posix()


def path_allowed(path: str, payload: dict[str, Any], config: dict[str, Any]) -> bool:
    normalized = validate_rel_path(path)
    forbidden = {validate_rel_path(item) for item in config.get("forbidden_paths", [])}
    prefixes = [validate_rel_path(item.rstrip("/")) for item in config.get("forbidden_path_prefixes", [])]
    if normalized in forbidden:
        return False
    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in prefixes):
        return False
    allowed = payload.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed:
        return False
    for raw in allowed:
        base = validate_rel_path(raw.rstrip("/"))
        if normalized == base or normalized.startswith(base + "/"):
            return True
    return False


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
    model = value.get("model")
    budgets = value.get("budgets")
    allowlist = value.get("test_allowlist")
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
        item = budgets.get(key)
        if not isinstance(item, int) or not low <= item <= high:
            raise WorkerError(f"budget_invalid:{key}")
    for key, low, high in (
        ("max_calls", 1, 5), ("max_context_bytes", 1024, 512_000),
        ("max_prompt_bytes", 4096, 768_000), ("max_output_tokens", 128, 8192),
        ("max_response_bytes", 1024, 1_000_000), ("timeout_seconds", 5, 300),
    ):
        item = model.get(key)
        if not isinstance(item, int) or not low <= item <= high:
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
    contexts = payload.get("context_paths", [])
    if not isinstance(contexts, list) or not all(isinstance(x, str) for x in contexts):
        raise WorkerError("context_paths_invalid")
    checks = payload.get("test_ids", [])
    if not isinstance(checks, list) or not all(isinstance(x, str) for x in checks):
        raise WorkerError("test_ids_invalid")
    if payload.get("output_branch") in ("main", "master"):
        raise WorkerError("production_branch_denied")
    return value


class ToolBudget:
    def __init__(self, max_commands: int, started: float, max_wall_seconds: int):
        self.max_commands = max_commands
        self.used = 0
        self.started = started
        self.max_wall_seconds = max_wall_seconds

    def run_git(self, repo: Path, args: list[str], *, timeout: int = 30, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        self.used += 1
        if self.used > self.max_commands:
            raise WorkerError("tool_budget_exhausted")
        allowed = min(timeout, remaining(self.started, self.max_wall_seconds))
        return run_command(["git", *args], cwd=repo, timeout=allowed, input_bytes=input_bytes, env=minimal_env())

    def run_test(self, repo: Path, cmd: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[bytes]:
        self.used += 1
        if self.used > self.max_commands:
            raise WorkerError("tool_budget_exhausted")
        allowed = min(timeout, remaining(self.started, self.max_wall_seconds))
        return run_command(cmd, cwd=repo, timeout=allowed, env=minimal_env(), max_output=2_000_000)


def build_context(worktree: Path, payload: dict[str, Any], config: dict[str, Any]) -> list[dict[str, str]]:
    limit = config["model"]["max_context_bytes"]
    used = 0
    context: list[dict[str, str]] = []
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
        context.append({"path": path, "sha256": sha256_bytes(data), "content": text})
    return context


def gate_admission(envelope: dict[str, Any], source_repo: Path, gate_cmd: list[str], started: float, wall: int) -> dict[str, Any]:
    payload = envelope["payload"]
    request = {
        "request_id": f"b032:{envelope['job_id']}",
        "actor": "b032-engineering-worker",
        "action_type": "workspace_branch_write",
        "risk_level": "L1", "environment": "isolated",
        "data_classification": envelope.get("data_classification", "INTERNAL"),
        "target": sha256_bytes(str(source_repo.resolve()).encode()),
        "side_effecting": True, "reversible": True, "external_side_effect": False,
        "uses_llm": True,
        "metadata": {"job_id": envelope["job_id"], "base_commit": payload["base_commit"]},
    }
    result = run_command(
        gate_cmd, cwd=source_repo, timeout=min(10, remaining(started, wall)),
        input_bytes=canonical(request) + b"\n", env=minimal_env(), max_output=65536,
    )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("gate_response_invalid") from exc
    if result.returncode != 0 or response.get("decision") != "allow":
        raise WorkerError(f"gate_not_allowed:{response.get('decision','invalid')}:{response.get('reason','unknown')}")
    for key in ("request_sha256", "policy_sha256"):
        if not isinstance(response.get(key), str) or not HEX64.fullmatch(response[key]):
            raise WorkerError(f"gate_evidence_invalid:{key}")
    try:
        uuid.UUID(str(response.get("event_id")))
    except ValueError as exc:
        raise WorkerError("gate_evidence_invalid:event_id") from exc
    return response


def call_model(config: dict[str, Any], prompt: dict[str, Any], cwd: Path, usage: dict[str, Any], started: float, wall: int) -> bytes:
    model = config["model"]
    usage["model_calls"] += 1
    if usage["model_calls"] > model["max_calls"]:
        raise WorkerError("model_call_budget_exhausted")
    prompt_bytes = canonical(prompt) + b"\n"
    if len(prompt_bytes) > model["max_prompt_bytes"]:
        raise WorkerError("model_prompt_budget_exhausted")
    usage["model_prompt_bytes"] += len(prompt_bytes)
    env = minimal_env({
        "FZH_MODEL_ALIAS": str(model.get("alias", "fzh-free-auto")),
        "FZH_MODEL_MAX_OUTPUT_TOKENS": str(model["max_output_tokens"]),
    })
    for key in ("FZH_LITELLM_BASE_URL", "FZH_LITELLM_API_KEY"):
        if key in os.environ:
            env[key] = os.environ[key]
    result = run_command(
        model["adapter_command"], cwd=cwd,
        timeout=min(model["timeout_seconds"], remaining(started, wall)),
        input_bytes=prompt_bytes, env=env, max_output=model["max_response_bytes"],
    )
    if result.returncode != 0:
        raise WorkerError("model_adapter_failed")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("model_response_invalid_json") from exc
    patch = response.get("patch")
    if not isinstance(patch, str):
        raise WorkerError("model_patch_missing")
    patch_bytes = patch.encode("utf-8")
    if len(patch_bytes) > model["max_response_bytes"]:
        raise WorkerError("model_patch_too_large")
    model_usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    completion_tokens = model_usage.get("completion_tokens")
    if isinstance(completion_tokens, int) and completion_tokens > model["max_output_tokens"]:
        raise WorkerError("model_reported_output_token_budget_exceeded")
    usage["model_response_bytes"] += len(patch_bytes)
    usage["model_id"] = str(response.get("model", model.get("alias", "unknown")))
    usage["provider_usage"] = model_usage
    return patch_bytes


def cleanup_worktree(source: Path, worktree: Path, branch: str, keep_branch: bool) -> None:
    # Safety cleanup is intentionally outside the worker tool budget; an exhausted
    # task budget must not prevent isolation cleanup.
    if worktree.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, env=minimal_env())
    subprocess.run(["git", "worktree", "prune"], cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, env=minimal_env())
    if not keep_branch:
        subprocess.run(["git", "branch", "-D", branch], cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, env=minimal_env())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-envelope", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--gate-command", nargs="+", default=DEFAULT_GATE)
    args = parser.parse_args()

    started = time.monotonic()
    envelope = validate_envelope(load_json(args.job_envelope, MAX_TASK_BYTES))
    config = validate_config(load_json(args.config, MAX_CONFIG_BYTES))
    payload = envelope["payload"]
    budgets = config["budgets"]
    source = args.source_repo.resolve()
    workspace_root = args.workspace_root.resolve()
    evidence_root = args.evidence_root.resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)

    tool = ToolBudget(budgets["max_tool_commands"], started, budgets["max_wall_seconds"])
    top = require_ok(tool.run_git(source, ["rev-parse", "--show-toplevel"]), "source_repo").decode().strip()
    if Path(top).resolve() != source:
        raise WorkerError("source_repo_must_be_toplevel")
    current_branch = require_ok(tool.run_git(source, ["symbolic-ref", "--quiet", "--short", "HEAD"]), "source_branch").decode().strip()
    if not current_branch:
        raise WorkerError("source_detached_head_denied")
    status = require_ok(tool.run_git(source, ["status", "--porcelain=v1", "--untracked-files=all"]), "source_status")
    if status.strip():
        raise WorkerError("source_repo_dirty")
    base = payload["base_commit"]
    source_head = require_ok(tool.run_git(source, ["rev-parse", "HEAD"]), "source_head").decode().strip()
    if source_head != base:
        raise WorkerError("source_head_not_requested_base")
    resolved = require_ok(tool.run_git(source, ["rev-parse", "--verify", f"{base}^{{commit}}"]), "base_resolve").decode().strip()
    if resolved != base:
        raise WorkerError("base_commit_resolution_mismatch")

    gate = gate_admission(envelope, source, args.gate_command, started, budgets["max_wall_seconds"])
    short = str(envelope["job_id"]).replace("-", "")[:12]
    branch = f"fzh/job-{short}"
    requested_branch = payload.get("output_branch")
    if requested_branch is not None and requested_branch != branch:
        raise WorkerError("output_branch_must_match_deterministic_job_branch")
    worktree = workspace_root / short
    evidence_dir = evidence_root / str(envelope["job_id"])
    if worktree.exists() or evidence_dir.exists():
        raise WorkerError("workspace_or_evidence_already_exists")
    evidence_dir.mkdir(mode=0o700)

    usage: dict[str, Any] = {
        "model_calls": 0, "model_prompt_bytes": 0, "model_response_bytes": 0,
        "model_id": None, "provider_usage": {},
    }
    worktree_added = False
    success = False
    try:
        require_ok(tool.run_git(source, ["worktree", "add", "-b", branch, str(worktree), base]), "worktree_add")
        worktree_added = True
        if require_ok(tool.run_git(worktree, ["rev-parse", "HEAD"]), "worktree_head").decode().strip() != base:
            raise WorkerError("worktree_not_at_base")

        context = build_context(worktree, payload, config)
        prompt = {
            "schema_version": 1,
            "job_id": envelope["job_id"],
            "objective": payload["objective"],
            "base_commit": base,
            "allowed_paths": payload["allowed_paths"],
            "context": context,
            "instruction": "Return JSON with one `patch` string containing a unified git diff. Do not rename/copy files, create symlinks/submodules, or touch paths outside allowed_paths.",
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
        require_ok(tool.run_git(worktree, ["apply", "--index", "--check", "--whitespace=error-all", "-"], input_bytes=patch), "git_apply_check")
        require_ok(tool.run_git(worktree, ["apply", "--index", "--whitespace=error-all", "-"], input_bytes=patch), "git_apply")

        staged = [validate_rel_path(x) for x in require_ok(tool.run_git(worktree, ["diff", "--cached", "--name-only", "--no-ext-diff"]), "staged_paths").decode().splitlines() if x]
        if sorted(staged) != sorted(paths):
            raise WorkerError("staged_paths_differ_from_patch")
        for path in staged:
            if not path_allowed(path, payload, config):
                raise WorkerError(f"staged_path_not_allowed:{path}")
            ensure_no_symlink_components(worktree, path)
            mode = require_ok(tool.run_git(worktree, ["ls-files", "-s", "--", path]), "index_mode").decode().strip()
            if mode.startswith("120000 ") or mode.startswith("160000 "):
                raise WorkerError(f"unsafe_index_mode:{path}")
        diff = require_ok(tool.run_git(worktree, ["diff", "--cached", "--binary", "--no-ext-diff"]), "staged_diff")
        if len(diff) > budgets["max_changed_bytes"]:
            raise WorkerError("changed_byte_budget_exhausted")
        if require_ok(tool.run_git(worktree, ["diff", "--name-only"]), "unstaged_check").strip():
            raise WorkerError("unexpected_unstaged_changes")
        if require_ok(tool.run_git(worktree, ["ls-files", "--others", "--exclude-standard"]), "untracked_check").strip():
            raise WorkerError("unexpected_untracked_files")

        tests: list[dict[str, Any]] = []
        for test_id in payload.get("test_ids", []):
            command = config["test_allowlist"].get(test_id)
            if command is None:
                raise WorkerError(f"test_not_allowlisted:{test_id}")
            result = tool.run_test(worktree, command)
            log = result.stdout + b"\n--- STDERR ---\n" + result.stderr
            log_path = evidence_dir / f"test-{test_id}.log"
            log_path.write_bytes(log)
            tests.append({
                "test_id": test_id, "command": command, "returncode": result.returncode,
                "log_sha256": sha256_file(log_path), "log_bytes": log_path.stat().st_size,
            })
            if result.returncode != 0:
                raise WorkerError(f"test_failed:{test_id}")

        if require_ok(tool.run_git(worktree, ["diff", "--name-only"]), "post_test_unstaged_check").strip():
            raise WorkerError("tests_modified_worktree")
        if require_ok(tool.run_git(worktree, ["ls-files", "--others", "--exclude-standard"]), "post_test_untracked_check").strip():
            raise WorkerError("tests_created_untracked_files")
        staged_after = [x for x in require_ok(tool.run_git(worktree, ["diff", "--cached", "--name-only"]), "post_test_staged_check").decode().splitlines() if x]
        if sorted(staged_after) != sorted(staged):
            raise WorkerError("tests_modified_staged_set")

        tool.used += 1
        if tool.used > tool.max_commands:
            raise WorkerError("tool_budget_exhausted")
        commit_env = minimal_env({
            "GIT_AUTHOR_NAME": "FrankensteinzHermes B032", "GIT_AUTHOR_EMAIL": "b032@localhost",
            "GIT_COMMITTER_NAME": "FrankensteinzHermes B032", "GIT_COMMITTER_EMAIL": "b032@localhost",
        })
        commit = run_command(
            ["git", "commit", "-m", f"B032 job {short}: implementation candidate"],
            cwd=worktree, timeout=min(30, remaining(started, budgets["max_wall_seconds"])), env=commit_env,
        )
        require_ok(commit, "git_commit")
        head = require_ok(tool.run_git(worktree, ["rev-parse", "HEAD"]), "head_after_commit").decode().strip()
        parent = require_ok(tool.run_git(worktree, ["rev-parse", "HEAD^"]), "parent_after_commit").decode().strip()
        if parent != base or not HEX40.fullmatch(head):
            raise WorkerError("unexpected_commit_history")
        if require_ok(tool.run_git(worktree, ["status", "--porcelain=v1", "--untracked-files=all"]), "final_status").strip():
            raise WorkerError("worktree_dirty_after_commit")

        evidence = {
            "schema_version": 1,
            "job_id": envelope["job_id"], "attempt_id": envelope.get("attempt_id"),
            "branch": branch, "base_commit": base, "head_commit": head,
            "lease_token_sha256": sha256_bytes(str(envelope["lease_token"]).encode()),
            "patch_sha256": sha256_file(patch_file), "changed_paths": staged,
            "changed_bytes": len(diff),
            "context": [{"path": item["path"], "sha256": item["sha256"]} for item in context],
            "gate": {key: gate.get(key) for key in ("request_sha256", "policy_sha256", "event_id", "reason", "observed_at_utc")},
            "model": {
                "id": usage["model_id"], "calls": usage["model_calls"],
                "prompt_bytes": usage["model_prompt_bytes"], "response_bytes": usage["model_response_bytes"],
                "max_output_tokens": config["model"]["max_output_tokens"], "reported_usage": usage["provider_usage"],
            },
            "tools": {"commands": tool.used, "max_commands": tool.max_commands},
            "tests": tests, "elapsed_seconds": round(time.monotonic() - started, 3),
            "promotion_authorized": False, "next_stage": "B033 independent reviewer",
        }
        evidence_path = evidence_dir / "implementation-evidence.json"
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_files = {}
        for path in sorted(evidence_dir.iterdir()):
            if path.is_file():
                manifest_files[path.name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        (evidence_dir / "manifest.json").write_text(json.dumps({"schema_version": 1, "files": manifest_files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        success = True
        print(str(evidence_dir))
        return 0
    except Exception as exc:
        (evidence_dir / "failure.json").write_text(json.dumps({
            "schema_version": 1, "job_id": envelope["job_id"], "error": str(exc),
            "elapsed_seconds": round(time.monotonic() - started, 3), "promotion_authorized": False,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        if worktree_added:
            cleanup_worktree(source, worktree, branch, keep_branch=success)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkerError as exc:
        print(f"B032 worker: {exc}", file=sys.stderr)
        raise SystemExit(2)

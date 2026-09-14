#!/usr/bin/env python3
"""B032 bounded engineering implementation worker.

Consumes one already-leased B031 job envelope, asks B030 for admission, creates
one isolated Git worktree/branch, accepts only validated unified diffs from a
configured model adapter, runs allowlisted tests, commits locally, and writes a
hashed evidence bundle. It never pushes, opens/merges PRs, or deploys.
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
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_ID = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")
DEFAULT_GATE = ["/usr/local/libexec/frankensteinzhermes/action-gate-client"]
MAX_CONFIG_BYTES = 256 * 1024
MAX_TASK_BYTES = 1024 * 1024

class WorkerError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


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


def run(cmd: list[str], *, cwd: Path, timeout: int, input_bytes: bytes | None = None,
        env: dict[str, str] | None = None, max_output: int = 2_000_000) -> subprocess.CompletedProcess[bytes]:
    if not cmd or not all(isinstance(x, str) and x for x in cmd):
        raise WorkerError("invalid_command")
    try:
        cp = subprocess.run(
            cmd, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(f"command_timeout:{cmd[0]}") from exc
    if len(cp.stdout) + len(cp.stderr) > max_output:
        raise WorkerError(f"command_output_too_large:{cmd[0]}")
    return cp


def minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    allowed = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    if extra:
        env.update(extra)
    return env


def validate_rel_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise WorkerError("invalid_path")
    p = PurePosixPath(raw)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise WorkerError(f"unsafe_path:{raw}")
    if p.parts[0] == ".git" or ".git" in p.parts:
        raise WorkerError(f"git_metadata_path_denied:{raw}")
    return p.as_posix()


def path_allowed(path: str, payload: dict[str, Any], config: dict[str, Any]) -> bool:
    path = validate_rel_path(path)
    forbidden = {validate_rel_path(x) for x in config.get("forbidden_paths", [])}
    prefixes = [validate_rel_path(x.rstrip("/")) + "/" for x in config.get("forbidden_path_prefixes", [])]
    if path in forbidden or any(path == p[:-1] or path.startswith(p) for p in prefixes):
        return False
    allowed = payload.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed:
        return False
    for raw in allowed:
        base = validate_rel_path(raw.rstrip("/"))
        if path == base or path.startswith(base + "/"):
            return True
    return False


def parse_patch_paths(patch: bytes) -> list[str]:
    if b"\x00" in patch:
        raise WorkerError("patch_contains_nul")
    try:
        text = patch.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkerError("patch_not_utf8") from exc
    paths: list[str] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
                raise WorkerError("invalid_diff_header")
            a = validate_rel_path(parts[2][2:])
            b = validate_rel_path(parts[3][2:])
            if a != b:
                raise WorkerError("rename_or_copy_not_supported")
            paths.append(b)
        elif line.startswith("rename from ") or line.startswith("rename to ") or line.startswith("copy from ") or line.startswith("copy to "):
            raise WorkerError("rename_or_copy_not_supported")
        elif line.startswith("--- /") or line.startswith("+++ /"):
            raise WorkerError("absolute_patch_path")
    if not paths:
        raise WorkerError("patch_has_no_files")
    if len(paths) != len(set(paths)):
        raise WorkerError("duplicate_patch_file")
    return paths


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


def validate_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise WorkerError("config_schema_invalid")
    model = config.get("model")
    budgets = config.get("budgets")
    allowlist = config.get("test_allowlist")
    if not isinstance(model, dict) or not isinstance(model.get("adapter_command"), list):
        raise WorkerError("model_config_invalid")
    if not isinstance(allowlist, dict):
        raise WorkerError("test_allowlist_invalid")
    for name, cmd in allowlist.items():
        if not SAFE_ID.fullmatch(str(name)) or not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise WorkerError("test_allowlist_entry_invalid")
    if not isinstance(budgets, dict):
        raise WorkerError("budgets_invalid")
    limits = {
        "max_wall_seconds": (30, 3600), "max_tool_commands": (1, 100),
        "max_changed_files": (1, 100), "max_changed_bytes": (1, 2_000_000),
    }
    for key, (lo, hi) in limits.items():
        value = budgets.get(key)
        if not isinstance(value, int) or not lo <= value <= hi:
            raise WorkerError(f"budget_invalid:{key}")
    for key, lo, hi in (("max_calls", 1, 5), ("max_response_bytes", 1024, 1_000_000), ("timeout_seconds", 5, 300)):
        value = model.get(key)
        if not isinstance(value, int) or not lo <= value <= hi:
            raise WorkerError(f"model_budget_invalid:{key}")
    return config


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
    if payload.get("output_branch") in ("main", "master"):
        raise WorkerError("production_branch_denied")
    checks = payload.get("test_ids", [])
    if not isinstance(checks, list) or not all(isinstance(x, str) for x in checks):
        raise WorkerError("test_ids_invalid")
    return value


def git(repo: Path, args: list[str], budget: dict[str, int], *, timeout: int = 30, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    budget["tool_commands"] += 1
    if budget["tool_commands"] > budget["max_tool_commands"]:
        raise WorkerError("tool_budget_exhausted")
    cp = run(["git", *args], cwd=repo, timeout=timeout, input_bytes=input_bytes, env=minimal_env())
    return cp


def require_ok(cp: subprocess.CompletedProcess[bytes], label: str) -> bytes:
    if cp.returncode != 0:
        detail = cp.stderr.decode("utf-8", errors="replace")[-2000:]
        raise WorkerError(f"{label}_failed:{detail}")
    return cp.stdout


def gate_admission(envelope: dict[str, Any], source_repo: Path, gate_cmd: list[str]) -> dict[str, Any]:
    payload = envelope["payload"]
    request = {
        "request_id": f"b032:{envelope['job_id']}",
        "actor": "b032-engineering-worker",
        "action_type": "workspace_branch_write",
        "risk_level": "L1",
        "environment": "isolated",
        "data_classification": envelope.get("data_classification", "INTERNAL"),
        "target": sha256_bytes(str(source_repo.resolve()).encode()),
        "side_effecting": True,
        "reversible": True,
        "external_side_effect": False,
        "uses_llm": True,
        "metadata": {"job_id": envelope["job_id"], "base_commit": payload["base_commit"]},
    }
    cp = run(gate_cmd, cwd=source_repo, timeout=10, input_bytes=canonical(request) + b"\n", env=minimal_env(), max_output=65536)
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("gate_response_invalid") from exc
    if cp.returncode != 0 or response.get("decision") != "allow":
        raise WorkerError(f"gate_not_allowed:{response.get('decision','invalid')}:{response.get('reason','unknown')}")
    for key in ("request_sha256", "policy_sha256", "event_id"):
        if not isinstance(response.get(key), str) or not response[key]:
            raise WorkerError(f"gate_evidence_missing:{key}")
    return response


def call_model(config: dict[str, Any], prompt: dict[str, Any], cwd: Path, usage: dict[str, int]) -> bytes:
    model = config["model"]
    usage["model_calls"] += 1
    if usage["model_calls"] > model["max_calls"]:
        raise WorkerError("model_call_budget_exhausted")
    env = minimal_env({"FZH_MODEL_ALIAS": str(model.get("alias", "fzh-free-auto"))})
    for key in ("FZH_LITELLM_BASE_URL", "FZH_LITELLM_API_KEY"):
        if key in os.environ:
            env[key] = os.environ[key]
    cp = run(model["adapter_command"], cwd=cwd, timeout=model["timeout_seconds"], input_bytes=canonical(prompt) + b"\n", env=env, max_output=model["max_response_bytes"])
    if cp.returncode != 0:
        raise WorkerError("model_adapter_failed")
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("model_response_invalid_json") from exc
    patch = response.get("patch")
    if not isinstance(patch, str):
        raise WorkerError("model_patch_missing")
    patch_bytes = patch.encode()
    if len(patch_bytes) > model["max_response_bytes"]:
        raise WorkerError("model_patch_too_large")
    usage["model_response_bytes"] += len(patch_bytes)
    usage["model_id"] = str(response.get("model", model.get("alias", "unknown")))
    return patch_bytes


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
    source = args.source_repo.resolve()
    if not (source / ".git").exists():
        raise WorkerError("source_not_git_repository")

    budgets = config["budgets"]
    usage: dict[str, Any] = {
        "tool_commands": 0, "max_tool_commands": budgets["max_tool_commands"],
        "model_calls": 0, "model_response_bytes": 0, "model_id": None,
    }

    status = require_ok(git(source, ["status", "--porcelain=v1", "--untracked-files=all"], usage), "source_status")
    if status.strip():
        raise WorkerError("source_repo_dirty")
    base = payload["base_commit"]
    resolved = require_ok(git(source, ["rev-parse", "--verify", f"{base}^{{commit}}"], usage), "base_resolve").decode().strip()
    if resolved != base:
        raise WorkerError("base_commit_resolution_mismatch")

    gate = gate_admission(envelope, source, args.gate_command)
    short = str(envelope["job_id"]).replace("-", "")[:12]
    branch = f"fzh/job-{short}"
    if branch in ("main", "master"):
        raise WorkerError("unsafe_branch")
    worktree = (args.workspace_root.resolve() / short)
    evidence_dir = (args.evidence_root.resolve() / str(envelope["job_id"]))
    if worktree.exists() or evidence_dir.exists():
        raise WorkerError("workspace_or_evidence_already_exists")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, mode=0o700)

    result: dict[str, Any] = {}
    try:
        require_ok(git(source, ["worktree", "add", "-b", branch, str(worktree), base], usage), "worktree_add")
        head = require_ok(git(worktree, ["rev-parse", "HEAD"], usage), "worktree_head").decode().strip()
        if head != base:
            raise WorkerError("worktree_not_at_base")

        prompt = {
            "schema_version": 1,
            "job_id": envelope["job_id"],
            "objective": payload["objective"],
            "base_commit": base,
            "allowed_paths": payload["allowed_paths"],
            "instruction": "Return JSON with a single `patch` field containing a unified git diff. Do not rename/copy files. Change only allowed paths.",
        }
        patch = call_model(config, prompt, worktree, usage)
        patch_paths = parse_patch_paths(patch)
        if len(patch_paths) > budgets["max_changed_files"]:
            raise WorkerError("changed_file_budget_exhausted")
        for path in patch_paths:
            if not path_allowed(path, payload, config):
                raise WorkerError(f"path_not_allowed:{path}")
            ensure_no_symlink_components(worktree, path)

        (evidence_dir / "model.patch").write_bytes(patch)
        require_ok(git(worktree, ["apply", "--check", "--whitespace=error-all", "-"], usage, input_bytes=patch), "git_apply_check")
        require_ok(git(worktree, ["apply", "--whitespace=error-all", "-"], usage, input_bytes=patch), "git_apply")

        changed_raw = require_ok(git(worktree, ["diff", "--name-only", "--no-ext-diff"], usage), "changed_paths").decode().splitlines()
        changed = [validate_rel_path(x) for x in changed_raw if x]
        if sorted(changed) != sorted(patch_paths):
            raise WorkerError("applied_paths_differ_from_patch")
        for path in changed:
            if not path_allowed(path, payload, config):
                raise WorkerError(f"dirty_path_not_allowed:{path}")
            ensure_no_symlink_components(worktree, path)
        diff = require_ok(git(worktree, ["diff", "--binary", "--no-ext-diff"], usage), "git_diff")
        if len(diff) > budgets["max_changed_bytes"]:
            raise WorkerError("changed_byte_budget_exhausted")

        test_results: list[dict[str, Any]] = []
        for test_id in payload.get("test_ids", []):
            cmd = config["test_allowlist"].get(test_id)
            if cmd is None:
                raise WorkerError(f"test_not_allowlisted:{test_id}")
            usage["tool_commands"] += 1
            if usage["tool_commands"] > budgets["max_tool_commands"]:
                raise WorkerError("tool_budget_exhausted")
            remaining = budgets["max_wall_seconds"] - int(time.monotonic() - started)
            if remaining <= 0:
                raise WorkerError("wall_clock_budget_exhausted")
            cp = run(cmd, cwd=worktree, timeout=min(remaining, 180), env=minimal_env(), max_output=2_000_000)
            log = cp.stdout + b"\n--- STDERR ---\n" + cp.stderr
            log_path = evidence_dir / f"test-{test_id}.log"
            log_path.write_bytes(log)
            test_results.append({"test_id": test_id, "command": cmd, "returncode": cp.returncode, "log_sha256": sha256_file(log_path)})
            if cp.returncode != 0:
                raise WorkerError(f"test_failed:{test_id}")

        require_ok(git(worktree, ["add", "--", *changed], usage), "git_add")
        staged = require_ok(git(worktree, ["diff", "--cached", "--name-only"], usage), "staged_paths").decode().splitlines()
        if sorted(staged) != sorted(changed):
            raise WorkerError("staged_paths_mismatch")
        commit_env = minimal_env({
            "GIT_AUTHOR_NAME": "FrankensteinzHermes B032",
            "GIT_AUTHOR_EMAIL": "b032@localhost",
            "GIT_COMMITTER_NAME": "FrankensteinzHermes B032",
            "GIT_COMMITTER_EMAIL": "b032@localhost",
        })
        usage["tool_commands"] += 1
        if usage["tool_commands"] > budgets["max_tool_commands"]:
            raise WorkerError("tool_budget_exhausted")
        cp = run(["git", "commit", "-m", f"B032 job {short}: implementation candidate"], cwd=worktree, timeout=30, env=commit_env)
        require_ok(cp, "git_commit")
        head = require_ok(git(worktree, ["rev-parse", "HEAD"], usage), "head_after_commit").decode().strip()
        parent = require_ok(git(worktree, ["rev-parse", "HEAD^"], usage), "parent_after_commit").decode().strip()
        if parent != base or not HEX40.fullmatch(head):
            raise WorkerError("unexpected_commit_history")
        final_status = require_ok(git(worktree, ["status", "--porcelain=v1", "--untracked-files=all"], usage), "final_status")
        if final_status.strip():
            raise WorkerError("worktree_dirty_after_commit")

        result = {
            "schema_version": 1,
            "job_id": envelope["job_id"], "attempt_id": envelope.get("attempt_id"),
            "lease_token_sha256": sha256_bytes(str(envelope["lease_token"]).encode()),
            "branch": branch, "base_commit": base, "head_commit": head,
            "patch_sha256": sha256_file(evidence_dir / "model.patch"),
            "changed_paths": changed, "changed_bytes": len(diff),
            "gate": {k: gate.get(k) for k in ("request_sha256", "policy_sha256", "event_id", "reason")},
            "model": {"id": usage["model_id"], "calls": usage["model_calls"], "response_bytes": usage["model_response_bytes"]},
            "tools": {"commands": usage["tool_commands"], "max_commands": budgets["max_tool_commands"]},
            "tests": test_results,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "promotion_authorized": False,
            "next_stage": "B033 independent reviewer",
        }
        evidence_path = evidence_dir / "implementation-evidence.json"
        evidence_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in sorted(evidence_dir.iterdir()) if p.is_file()}
        (evidence_dir / "manifest.json").write_text(json.dumps({"schema_version": 1, "files": manifest}, indent=2, sort_keys=True) + "\n")
        print(str(evidence_dir))
        return 0
    except Exception as exc:
        (evidence_dir / "failure.json").write_text(json.dumps({
            "schema_version": 1, "job_id": envelope["job_id"], "error": str(exc),
            "elapsed_seconds": round(time.monotonic() - started, 3), "promotion_authorized": False,
        }, indent=2, sort_keys=True) + "\n")
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkerError as exc:
        print(f"B032 worker: {exc}", file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""B033 independent engineering reviewer.

Consumes one already-leased `engineering.review` envelope. It never checks out or
modifies the candidate. B032 evidence is verified from externally anchored
hashes, Git base/head/diff are reconstructed independently with read-only
commands, a separate bounded model session produces structured findings, and a
deterministic policy derives the verdict. No push/merge/deploy path exists.
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
BRANCH = re.compile(r"^fzh/job-[0-9a-f]{12}$")
MAX_CONFIG_BYTES = 256 * 1024
MAX_JOB_BYTES = 1024 * 1024
READ_ONLY_GIT = {"rev-parse", "merge-base", "rev-list", "diff", "show", "cat-file"}
SEVERITIES = {"info", "low", "medium", "high", "critical"}
CATEGORIES = {
    "correctness", "regression_risk", "task_compliance", "maintainability",
    "scope_expansion", "missing_tests", "concurrency_state", "error_handling",
    "evidence_consistency", "security_obvious",
}


class ReviewerError(RuntimeError):
    pass


class IntegrityError(ReviewerError):
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


def load_json(path: Path, limit: int) -> Any:
    data = path.read_bytes()
    if len(data) > limit:
        raise ReviewerError(f"input_too_large:{path.name}")
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ReviewerError(f"invalid_json:{path.name}:{exc.msg}") from exc


def minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    if extra:
        env.update(extra)
    return env


def remaining(started: float, wall: int) -> int:
    left = wall - int(time.monotonic() - started)
    if left <= 0:
        raise ReviewerError("wall_clock_budget_exhausted")
    return left


def run(cmd: list[str], *, cwd: Path, timeout: int, input_bytes: bytes | None = None,
        env: dict[str, str] | None = None, max_output: int = 2_000_000) -> subprocess.CompletedProcess[bytes]:
    if not cmd or not all(isinstance(x, str) and x for x in cmd):
        raise ReviewerError("invalid_command")
    try:
        cp = subprocess.run(cmd, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, env=env, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise ReviewerError(f"command_unavailable:{cmd[0]}:{exc.__class__.__name__}") from exc
    if len(cp.stdout) + len(cp.stderr) > max_output:
        raise ReviewerError(f"command_output_too_large:{cmd[0]}")
    return cp


def require_ok(cp: subprocess.CompletedProcess[bytes], label: str) -> bytes:
    if cp.returncode != 0:
        tail = cp.stderr.decode("utf-8", errors="replace")[-1600:]
        raise IntegrityError(f"{label}_failed:{tail}")
    return cp.stdout


def validate_rel_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise ReviewerError("invalid_path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ReviewerError(f"unsafe_path:{raw}")
    if ".git" in path.parts:
        raise ReviewerError(f"git_metadata_path_denied:{raw}")
    return path.as_posix()


def path_allowed(path: str, payload: dict[str, Any], config: dict[str, Any]) -> bool:
    normalized = validate_rel_path(path)
    forbidden = {validate_rel_path(x) for x in config.get("forbidden_paths", [])}
    prefixes = [validate_rel_path(x.rstrip("/")) for x in config.get("forbidden_path_prefixes", [])]
    if normalized in forbidden or any(normalized == p or normalized.startswith(p + "/") for p in prefixes):
        return False
    for raw in payload["allowed_paths"]:
        base = validate_rel_path(raw.rstrip("/"))
        if normalized == base or normalized.startswith(base + "/"):
            return True
    return False


def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ReviewerError("config_schema_invalid")
    ledger, model, budgets, policy = value.get("ledger"), value.get("model"), value.get("budgets"), value.get("verdict_policy")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("adapter_command"), list) or not ledger["adapter_command"]:
        raise ReviewerError("ledger_config_invalid")
    if not isinstance(model, dict) or not isinstance(model.get("adapter_command"), list) or not model["adapter_command"]:
        raise ReviewerError("model_config_invalid")
    for command in (ledger["adapter_command"], model["adapter_command"]):
        if not all(isinstance(x, str) and x for x in command):
            raise ReviewerError("adapter_command_invalid")
    if not isinstance(budgets, dict):
        raise ReviewerError("budgets_invalid")
    for key, low, high in (
        ("max_wall_seconds", 15, 1800), ("max_git_commands", 1, 60),
        ("max_diff_bytes", 1, 2_000_000), ("max_context_bytes", 0, 512_000),
    ):
        item = budgets.get(key)
        if not isinstance(item, int) or not low <= item <= high:
            raise ReviewerError(f"budget_invalid:{key}")
    for key, low, high in (
        ("max_calls", 1, 3), ("max_prompt_bytes", 4096, 2_500_000),
        ("max_output_tokens", 128, 8192), ("max_response_bytes", 1024, 1_000_000),
        ("timeout_seconds", 5, 300), ("max_findings", 1, 100),
    ):
        item = model.get(key)
        if not isinstance(item, int) or not low <= item <= high:
            raise ReviewerError(f"model_budget_invalid:{key}")
    if not isinstance(policy, dict):
        raise ReviewerError("verdict_policy_invalid")
    threshold = policy.get("medium_findings_require_changes", 1)
    if not isinstance(threshold, int) or not 1 <= threshold <= 100:
        raise ReviewerError("medium_threshold_invalid")
    heartbeat = ledger.get("heartbeat_seconds", 900)
    retry_delay = ledger.get("retry_delay_seconds", 60)
    if not isinstance(heartbeat, int) or not 15 <= heartbeat <= 3600:
        raise ReviewerError("ledger_heartbeat_invalid")
    if not isinstance(retry_delay, int) or not 0 <= retry_delay <= 3600:
        raise ReviewerError("ledger_retry_delay_invalid")
    return value


def validate_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("job_kind") != "engineering.review":
        raise ReviewerError("job_kind_invalid")
    for key in ("job_id", "lease_token"):
        try:
            uuid.UUID(str(value.get(key)))
        except ValueError as exc:
            raise ReviewerError(f"invalid_{key}") from exc
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ReviewerError("payload_invalid")
    try:
        uuid.UUID(str(payload.get("implementation_job_id")))
    except ValueError as exc:
        raise ReviewerError("implementation_job_id_invalid") from exc
    for key in ("base_commit", "head_commit"):
        if not isinstance(payload.get(key), str) or not HEX40.fullmatch(payload[key]):
            raise ReviewerError(f"{key}_invalid")
    if not isinstance(payload.get("branch"), str) or not BRANCH.fullmatch(payload["branch"]):
        raise ReviewerError("branch_invalid")
    for key in ("implementation_evidence_sha256", "implementation_manifest_sha256"):
        if not isinstance(payload.get(key), str) or not HEX64.fullmatch(payload[key]):
            raise ReviewerError(f"{key}_invalid")
    if not isinstance(payload.get("objective"), str) or not payload["objective"].strip():
        raise ReviewerError("objective_invalid")
    allowed = payload.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed:
        raise ReviewerError("allowed_paths_required")
    for item in allowed:
        validate_rel_path(str(item).rstrip("/"))
    criteria = payload.get("acceptance_criteria", [])
    context = payload.get("context_paths", [])
    if not isinstance(criteria, list) or not all(isinstance(x, str) and x for x in criteria):
        raise ReviewerError("acceptance_criteria_invalid")
    if not isinstance(context, list) or not all(isinstance(x, str) for x in context):
        raise ReviewerError("context_paths_invalid")
    return value


class GitReader:
    def __init__(self, repo: Path, maximum: int, started: float, wall: int):
        self.repo, self.maximum, self.started, self.wall = repo, maximum, started, wall
        self.used = 0

    def call(self, args: list[str], *, timeout: int = 30, max_output: int = 2_000_000) -> subprocess.CompletedProcess[bytes]:
        if not args or args[0] not in READ_ONLY_GIT:
            raise ReviewerError("git_write_or_unknown_command_denied")
        self.used += 1
        if self.used > self.maximum:
            raise ReviewerError("git_command_budget_exhausted")
        return run(["git", *args], cwd=self.repo, timeout=min(timeout, remaining(self.started, self.wall)),
                   env=minimal_env(), max_output=max_output)


def verify_manifest(evidence_dir: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not evidence_dir.is_dir() or evidence_dir.is_symlink():
        raise IntegrityError("implementation_evidence_directory_invalid")
    manifest_path = evidence_dir / "manifest.json"
    evidence_path = evidence_dir / "implementation-evidence.json"
    if not manifest_path.is_file() or manifest_path.is_symlink() or not evidence_path.is_file() or evidence_path.is_symlink():
        raise IntegrityError("required_implementation_evidence_missing")
    if sha256_file(manifest_path) != payload["implementation_manifest_sha256"]:
        raise IntegrityError("implementation_manifest_anchor_mismatch")
    if sha256_file(evidence_path) != payload["implementation_evidence_sha256"]:
        raise IntegrityError("implementation_evidence_anchor_mismatch")
    manifest = load_json(manifest_path, MAX_JOB_BYTES)
    evidence = load_json(evidence_path, MAX_JOB_BYTES)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), dict):
        raise IntegrityError("implementation_manifest_schema_invalid")
    expected_names = set(manifest["files"])
    if not {"implementation-evidence.json", "model.patch"}.issubset(expected_names):
        raise IntegrityError("implementation_manifest_required_files_missing")
    actual_names = {p.name for p in evidence_dir.iterdir() if p.is_file() and not p.is_symlink()}
    if actual_names != expected_names | {"manifest.json"}:
        raise IntegrityError("implementation_evidence_file_set_mismatch")
    for name, metadata in manifest["files"].items():
        if PurePosixPath(name).name != name or not isinstance(metadata, dict):
            raise IntegrityError("implementation_manifest_filename_invalid")
        path = evidence_dir / name
        if not path.is_file() or path.is_symlink():
            raise IntegrityError(f"implementation_artifact_invalid:{name}")
        if metadata.get("sha256") != sha256_file(path) or metadata.get("bytes") != path.stat().st_size:
            raise IntegrityError(f"implementation_artifact_hash_mismatch:{name}")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise IntegrityError("implementation_evidence_schema_invalid")
    if evidence.get("promotion_authorized") is not False or evidence.get("next_stage") != "B033 independent reviewer":
        raise IntegrityError("implementation_promotion_boundary_invalid")
    return manifest, evidence


def reconstruct_candidate(git: GitReader, payload: dict[str, Any], implementation: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    base, head, branch = payload["base_commit"], payload["head_commit"], payload["branch"]
    for name, sha in (("base", base), ("head", head)):
        resolved = require_ok(git.call(["rev-parse", "--verify", f"{sha}^{{commit}}"]), f"{name}_commit_resolve").decode().strip()
        if resolved != sha:
            raise IntegrityError(f"{name}_commit_mismatch")
    branch_head = require_ok(git.call(["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"]), "candidate_branch_resolve").decode().strip()
    if branch_head != head:
        raise IntegrityError("candidate_branch_head_mismatch")
    ancestor = git.call(["merge-base", "--is-ancestor", base, head])
    if ancestor.returncode != 0:
        raise IntegrityError("base_not_ancestor_of_head")
    history = require_ok(git.call(["rev-list", "--parents", "-n", "1", head]), "head_parents").decode().strip().split()
    if len(history) != 2 or history[0] != head or history[1] != base:
        raise IntegrityError("candidate_not_single_commit_on_base")
    count = require_ok(git.call(["rev-list", "--count", f"{base}..{head}"]), "candidate_commit_count").decode().strip()
    if count != "1":
        raise IntegrityError("candidate_history_not_linear_single_commit")
    diff = require_ok(git.call(["diff", "--binary", "--no-ext-diff", "--no-renames", base, head, "--"], max_output=config["budgets"]["max_diff_bytes"] + 1), "candidate_diff")
    if len(diff) > config["budgets"]["max_diff_bytes"]:
        raise ReviewerError("diff_budget_exhausted")
    changed = [validate_rel_path(x) for x in require_ok(git.call(["diff", "--name-only", "--no-renames", base, head, "--"]), "candidate_changed_paths").decode().splitlines() if x]
    if not changed:
        raise IntegrityError("candidate_has_no_changes")
    if sorted(changed) != sorted(implementation.get("changed_paths", [])):
        raise IntegrityError("changed_paths_evidence_mismatch")
    if len(diff) != implementation.get("changed_bytes"):
        raise IntegrityError("changed_bytes_evidence_mismatch")
    if implementation.get("base_commit") != base or implementation.get("head_commit") != head or implementation.get("branch") != branch:
        raise IntegrityError("implementation_git_binding_mismatch")
    if implementation.get("job_id") != payload["implementation_job_id"]:
        raise IntegrityError("implementation_job_binding_mismatch")
    patch_path_hash = implementation.get("patch_sha256")
    if not isinstance(patch_path_hash, str) or not HEX64.fullmatch(patch_path_hash):
        raise IntegrityError("implementation_patch_hash_invalid")
    for path in changed:
        if not path_allowed(path, payload, config):
            raise IntegrityError(f"review_scope_violation:{path}")
    return {"diff": diff, "changed_paths": changed, "diff_sha256": sha256_bytes(diff), "changed_bytes": len(diff)}


def build_context(git: GitReader, payload: dict[str, Any], config: dict[str, Any], reconstructed: dict[str, Any]) -> list[dict[str, str]]:
    maximum = config["budgets"]["max_context_bytes"]
    used = 0
    result: list[dict[str, str]] = []
    for raw in payload.get("context_paths", []):
        path = validate_rel_path(raw)
        if not path_allowed(path, payload, config):
            raise ReviewerError(f"context_path_not_allowed:{path}")
        cp = git.call(["show", f"{payload['head_commit']}:{path}"], max_output=maximum + 1)
        data = require_ok(cp, f"context_show:{path}")
        used += len(data)
        if used > maximum:
            raise ReviewerError("review_context_budget_exhausted")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewerError(f"review_context_not_utf8:{path}") from exc
        result.append({"path": path, "sha256": sha256_bytes(data), "content": text})
    return result


def validate_model_response(value: Any, config: dict[str, Any], changed_paths: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ReviewerError("review_model_schema_invalid")
    if not isinstance(value.get("summary"), str) or not value["summary"].strip() or len(value["summary"]) > 4000:
        raise ReviewerError("review_model_summary_invalid")
    findings = value.get("findings")
    if not isinstance(findings, list) or len(findings) > config["model"]["max_findings"]:
        raise ReviewerError("review_model_findings_invalid")
    normalized: list[dict[str, Any]] = []
    for item in findings:
        if not isinstance(item, dict) or item.get("severity") not in SEVERITIES or item.get("category") not in CATEGORIES:
            raise ReviewerError("review_finding_classification_invalid")
        for field in ("rationale", "remediation"):
            if not isinstance(item.get(field), str) or not item[field].strip() or len(item[field]) > 4000:
                raise ReviewerError(f"review_finding_{field}_invalid")
        path = item.get("path")
        if path is not None:
            path = validate_rel_path(path)
            if path not in changed_paths:
                raise ReviewerError("review_finding_path_not_changed")
        line = item.get("line")
        if line is not None and (not isinstance(line, int) or not 1 <= line <= 10_000_000):
            raise ReviewerError("review_finding_line_invalid")
        normalized.append({
            "severity": item["severity"], "category": item["category"],
            "path": path, "line": line, "rationale": item["rationale"],
            "remediation": item["remediation"],
        })
    return {"schema_version": 1, "summary": value["summary"], "findings": normalized}


def derive_verdict(findings: list[dict[str, Any]], config: dict[str, Any]) -> str:
    severities = [x["severity"] for x in findings]
    if "critical" in severities or "high" in severities:
        return "changes_required"
    mediums = sum(1 for value in severities if value == "medium")
    if mediums >= config["verdict_policy"]["medium_findings_require_changes"]:
        return "changes_required"
    return "approve"


def ledger_env() -> dict[str, str]:
    env = minimal_env()
    for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGPASSFILE", "PGSSLMODE"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def call_ledger(config: dict[str, Any], request: dict[str, Any], cwd: Path, started: float, wall: int) -> dict[str, Any]:
    cp = run(config["ledger"]["adapter_command"], cwd=cwd, timeout=min(10, remaining(started, wall)),
             input_bytes=canonical(request) + b"\n", env=ledger_env(), max_output=131072)
    if cp.returncode != 0:
        raise ReviewerError("ledger_adapter_failed")
    try:
        response = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise ReviewerError("ledger_response_invalid") from exc
    if not isinstance(response, dict):
        raise ReviewerError("ledger_response_not_object")
    return response


def ledger_request(operation: str, envelope: dict[str, Any], **extra: Any) -> dict[str, Any]:
    result = {"operation": operation, "job_id": envelope["job_id"], "lease_token": envelope["lease_token"]}
    result.update(extra)
    return result


def call_model(config: dict[str, Any], prompt: dict[str, Any], repo: Path, started: float, wall: int) -> tuple[dict[str, Any], dict[str, Any]]:
    model = config["model"]
    prompt_bytes = canonical(prompt) + b"\n"
    if len(prompt_bytes) > model["max_prompt_bytes"]:
        raise ReviewerError("review_prompt_budget_exhausted")
    env = minimal_env({"FZH_MODEL_ALIAS": str(model.get("alias", "fzh-free-auto")), "FZH_MODEL_MAX_OUTPUT_TOKENS": str(model["max_output_tokens"])})
    for key in ("FZH_LITELLM_BASE_URL", "FZH_LITELLM_API_KEY"):
        if key in os.environ:
            env[key] = os.environ[key]
    cp = run(model["adapter_command"], cwd=repo, timeout=min(model["timeout_seconds"], remaining(started, wall)),
             input_bytes=prompt_bytes, env=env, max_output=model["max_response_bytes"])
    if cp.returncode != 0:
        raise ReviewerError("review_model_adapter_failed")
    try:
        raw = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise ReviewerError("review_model_invalid_json") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("review"), dict):
        raise ReviewerError("review_model_envelope_invalid")
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    if isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] > model["max_output_tokens"]:
        raise ReviewerError("review_model_output_token_budget_exceeded")
    review = validate_model_response(raw["review"], config, prompt["changed_paths"])
    metadata = {"model_id": str(raw.get("model", model.get("alias", "unknown"))), "prompt_bytes": len(prompt_bytes), "response_bytes": len(cp.stdout), "reported_usage": usage}
    return review, metadata


def write_review_bundle(output_dir: Path, evidence: dict[str, Any], reconstructed_diff: bytes | None, model_response: dict[str, Any] | None) -> dict[str, str]:
    output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    if reconstructed_diff is not None:
        (output_dir / "reconstructed.diff").write_bytes(reconstructed_diff)
    if model_response is not None:
        (output_dir / "review-model.json").write_text(json.dumps(model_response, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence_path = output_dir / "review-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in sorted(output_dir.iterdir()) if p.is_file()}
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"schema_version": 1, "files": files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"evidence_sha256": sha256_file(evidence_path), "manifest_sha256": sha256_file(manifest_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-envelope", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--implementation-evidence-dir", required=True, type=Path)
    parser.add_argument("--review-output-root", required=True, type=Path)
    args = parser.parse_args()

    started = time.monotonic()
    envelope = validate_envelope(load_json(args.job_envelope, MAX_JOB_BYTES))
    config = validate_config(load_json(args.config, MAX_CONFIG_BYTES))
    payload = envelope["payload"]
    repo = args.source_repo.resolve()
    implementation_dir = args.implementation_evidence_dir.resolve()
    output_root = args.review_output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output_dir = output_root / str(envelope["job_id"])
    if output_dir.exists():
        raise ReviewerError("review_output_already_exists")
    git = GitReader(repo, config["budgets"]["max_git_commands"], started, config["budgets"]["max_wall_seconds"])

    top = require_ok(git.call(["rev-parse", "--show-toplevel"]), "source_repo").decode().strip()
    if Path(top).resolve() != repo:
        raise ReviewerError("source_repo_must_be_toplevel")

    start_record = call_ledger(config, ledger_request("start", envelope), repo, started, config["budgets"]["max_wall_seconds"])
    if start_record.get("status") != "running":
        raise ReviewerError("ledger_start_failed")
    execution_started = True
    try:
        heartbeat = call_ledger(config, ledger_request("heartbeat", envelope, lease_seconds=config["ledger"]["heartbeat_seconds"]), repo, started, config["budgets"]["max_wall_seconds"])
        if not heartbeat.get("lease_expires_at"):
            raise ReviewerError("ledger_heartbeat_failed")

        try:
            _manifest, implementation = verify_manifest(implementation_dir, payload)
            reconstructed = reconstruct_candidate(git, payload, implementation, config)
        except IntegrityError as integrity:
            evidence = {
                "schema_version": 1, "job_id": envelope["job_id"], "implementation_job_id": payload["implementation_job_id"],
                "verdict": "reject", "integrity_verified": False, "integrity_error": str(integrity),
                "model_called": False, "promotion_authorized": False, "next_stage": None,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            hashes = write_review_bundle(output_dir, evidence, None, None)
            completed = call_ledger(config, ledger_request("complete", envelope, result={"verdict": "reject", **hashes, "promotion_authorized": False}), repo, started, config["budgets"]["max_wall_seconds"])
            if completed.get("status") != "succeeded":
                raise ReviewerError("ledger_complete_failed")
            print(str(output_dir))
            return 0

        context = build_context(git, payload, config, reconstructed)
        try:
            diff_text = reconstructed["diff"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewerError("reconstructed_diff_not_utf8") from exc
        prompt = {
            "schema_version": 1, "review_job_id": envelope["job_id"],
            "implementation_job_id": payload["implementation_job_id"], "objective": payload["objective"],
            "acceptance_criteria": payload.get("acceptance_criteria", []),
            "base_commit": payload["base_commit"], "head_commit": payload["head_commit"],
            "changed_paths": reconstructed["changed_paths"], "diff_sha256": reconstructed["diff_sha256"],
            "diff": diff_text, "context": context,
            "instruction": "Review independently. Return only the required structured findings. Do not assume B032 reasoning is correct. Do not request or perform code changes.",
        }
        review, model_metadata = call_model(config, prompt, repo, started, config["budgets"]["max_wall_seconds"])
        verdict = derive_verdict(review["findings"], config)
        evidence = {
            "schema_version": 1, "job_id": envelope["job_id"], "implementation_job_id": payload["implementation_job_id"],
            "base_commit": payload["base_commit"], "head_commit": payload["head_commit"], "branch": payload["branch"],
            "integrity_verified": True, "reconstructed_diff_sha256": reconstructed["diff_sha256"],
            "changed_paths": reconstructed["changed_paths"], "changed_bytes": reconstructed["changed_bytes"],
            "implementation_evidence_sha256": payload["implementation_evidence_sha256"],
            "implementation_manifest_sha256": payload["implementation_manifest_sha256"],
            "review_summary": review["summary"], "findings": review["findings"], "verdict": verdict,
            "model": model_metadata, "git_commands": git.used, "promotion_authorized": False,
            "next_stage": "B034 adversarial QA" if verdict == "approve" else None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        hashes = write_review_bundle(output_dir, evidence, reconstructed["diff"], review)
        completed = call_ledger(config, ledger_request("complete", envelope, result={"verdict": verdict, "head_commit": payload["head_commit"], **hashes, "promotion_authorized": False}), repo, started, config["budgets"]["max_wall_seconds"])
        if completed.get("status") != "succeeded":
            raise ReviewerError("ledger_complete_failed")
        print(str(output_dir))
        return 0
    except Exception as exc:
        if execution_started:
            try:
                call_ledger(config, ledger_request("fail", envelope, error_code="b033_review_failed", error_summary=str(exc)[:1800], retryable=True, retry_delay_seconds=config["ledger"]["retry_delay_seconds"]), repo, started, config["budgets"]["max_wall_seconds"])
            except Exception:
                pass
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReviewerError as exc:
        print(f"B033 reviewer: {exc}", file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""B035 independent security reviewer.

Consumes only a QA-passed B034 evidence bundle, independently re-binds the
candidate from Git objects, performs deterministic read-only security analysis,
and optionally obtains structured findings from a separate bounded model.
Neither deterministic analyzers nor the model may mutate candidate Git state or
authorize release/promotion.
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
SAFE_SEVERITIES = {"critical", "high", "medium", "low", "info"}
SAFE_CATEGORIES = {
    "secret", "credential", "unsafe_execution", "supply_chain", "workflow",
    "permissions", "path_scope", "symlink", "submodule", "correctness_security",
    "input_validation", "authentication", "authorization", "cryptography",
    "network", "injection", "other",
}
READ_ONLY_GIT = {"rev-parse", "rev-list", "diff", "ls-tree", "show", "status"}
MAX_JSON = 1024 * 1024


class SecurityError(RuntimeError):
    pass


class SecurityIntegrityError(SecurityError):
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


def load_json(path: Path, maximum: int = MAX_JSON) -> Any:
    data = path.read_bytes()
    if len(data) > maximum:
        raise SecurityError(f"input_too_large:{path.name}")
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise SecurityError(f"invalid_json:{path.name}:{exc.msg}") from exc


def minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    env = {key: os.environ[key] for key in keep if key in os.environ}
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
        raise SecurityError("wall_clock_budget_exhausted")
    return left


def run(cmd: list[str], *, cwd: Path, timeout: int, input_bytes: bytes | None = None,
        env: dict[str, str] | None = None, max_output: int = 2_000_000) -> subprocess.CompletedProcess[bytes]:
    try:
        cp = subprocess.run(
            cmd, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, env=env, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise SecurityError(f"command_unavailable:{cmd[0]}:{exc.__class__.__name__}") from exc
    if len(cp.stdout) + len(cp.stderr) > max_output:
        raise SecurityError(f"command_output_too_large:{cmd[0]}")
    return cp


def require_ok(cp: subprocess.CompletedProcess[bytes], label: str, *, integrity: bool = False) -> bytes:
    if cp.returncode != 0:
        message = f"{label}_failed:{cp.stderr.decode('utf-8', errors='replace')[-1200:]}"
        if integrity:
            raise SecurityIntegrityError(message)
        raise SecurityError(message)
    return cp.stdout


def validate_rel_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > 1024:
        raise SecurityIntegrityError("invalid_changed_path")
    if any(ord(ch) < 32 for ch in raw) or "\\" in raw or ":" in raw:
        raise SecurityIntegrityError(f"unsafe_changed_path:{raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts or str(path) != raw:
        raise SecurityIntegrityError(f"unsafe_changed_path:{raw}")
    return raw


def path_allowed(path: str, allowed: list[str]) -> bool:
    for raw in allowed:
        prefix = raw.rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SecurityError("config_schema_invalid")
    for section in ("ledger", "model", "budgets", "policy"):
        if not isinstance(value.get(section), dict):
            raise SecurityError(f"config_section_invalid:{section}")
    ledger, model, budgets, policy = value["ledger"], value["model"], value["budgets"], value["policy"]
    cmd = ledger.get("adapter_command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
        raise SecurityError("ledger_adapter_invalid")
    if model.get("enabled") not in (True, False):
        raise SecurityError("model_enabled_invalid")
    if model["enabled"]:
        cmd = model.get("adapter_command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise SecurityError("model_adapter_invalid")
    for key, low, high in (
        ("max_wall_seconds", 30, 3600), ("max_git_commands", 1, 100),
        ("max_diff_bytes", 1024, 2_000_000), ("max_evidence_bytes", 4096, 8_000_000),
        ("max_scanned_files", 1, 256), ("max_scanned_bytes", 4096, 8_000_000),
    ):
        item = budgets.get(key)
        if not isinstance(item, int) or not low <= item <= high:
            raise SecurityError(f"budget_invalid:{key}")
    for key, low, high in (
        ("max_calls", 1, 2), ("max_prompt_bytes", 4096, 1_000_000),
        ("max_output_tokens", 128, 4096), ("max_response_bytes", 1024, 512_000),
        ("timeout_seconds", 5, 300),
    ):
        item = model.get(key)
        if not isinstance(item, int) or not low <= item <= high:
            raise SecurityError(f"model_budget_invalid:{key}")
    threshold = policy.get("medium_failure_threshold")
    if not isinstance(threshold, int) or not 1 <= threshold <= 20:
        raise SecurityError("medium_failure_threshold_invalid")
    for key in ("protected_path_prefixes", "dependency_files"):
        items = policy.get(key)
        if not isinstance(items, list) or not all(isinstance(x, str) and x for x in items):
            raise SecurityError(f"policy_list_invalid:{key}")
    heartbeat = ledger.get("heartbeat_seconds", 900)
    retry = ledger.get("retry_delay_seconds", 60)
    if not isinstance(heartbeat, int) or not 15 <= heartbeat <= 3600:
        raise SecurityError("heartbeat_invalid")
    if not isinstance(retry, int) or not 0 <= retry <= 3600:
        raise SecurityError("retry_delay_invalid")
    return value


def validate_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("job_kind") != "engineering.security_review":
        raise SecurityError("job_kind_invalid")
    for key in ("job_id", "lease_token"):
        try:
            uuid.UUID(str(value.get(key)))
        except ValueError as exc:
            raise SecurityError(f"invalid_{key}") from exc
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise SecurityError("payload_invalid")
    try:
        uuid.UUID(str(payload.get("qa_job_id")))
    except ValueError as exc:
        raise SecurityError("qa_job_id_invalid") from exc
    for key in ("base_commit", "head_commit"):
        if not isinstance(payload.get(key), str) or not HEX40.fullmatch(payload[key]):
            raise SecurityError(f"{key}_invalid")
    if not isinstance(payload.get("branch"), str) or not BRANCH.fullmatch(payload["branch"]):
        raise SecurityError("branch_invalid")
    for key in ("qa_evidence_sha256", "qa_manifest_sha256"):
        if not isinstance(payload.get(key), str) or not HEX64.fullmatch(payload[key]):
            raise SecurityError(f"{key}_invalid")
    if not isinstance(payload.get("objective"), str) or not payload["objective"].strip() or len(payload["objective"]) > 12000:
        raise SecurityError("objective_invalid")
    criteria = payload.get("acceptance_criteria", [])
    if not isinstance(criteria, list) or len(criteria) > 64 or not all(isinstance(x, str) and 0 < len(x) <= 4000 for x in criteria):
        raise SecurityError("acceptance_criteria_invalid")
    allowed = payload.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed or len(allowed) > 128:
        raise SecurityError("allowed_paths_invalid")
    for item in allowed:
        validate_rel_path(item.rstrip("/"))
    return value


class GitBudget:
    def __init__(self, maximum: int, started: float, wall: int):
        self.maximum, self.started, self.wall, self.used = maximum, started, wall, 0

    def _charge(self) -> None:
        self.used += 1
        if self.used > self.maximum:
            raise SecurityError("git_command_budget_exhausted")

    def read(self, repo: Path, args: list[str], *, integrity: bool = False, maximum: int = 2_000_000) -> bytes:
        if not args or args[0] not in READ_ONLY_GIT:
            raise SecurityError("git_command_not_readonly")
        self._charge()
        return require_ok(
            run(["git", *args], cwd=repo, timeout=min(30, remaining(self.started, self.wall)), env=minimal_env(), max_output=maximum),
            "git_" + args[0], integrity=integrity,
        )

    def ancestor(self, repo: Path, base: str, head: str) -> None:
        self._charge()
        cp = run(["git", "merge-base", "--is-ancestor", base, head], cwd=repo,
                 timeout=min(30, remaining(self.started, self.wall)), env=minimal_env())
        if cp.returncode != 0:
            raise SecurityIntegrityError("candidate_base_not_ancestor")


def call_ledger(config: dict[str, Any], request: dict[str, Any], cwd: Path, started: float, wall: int) -> dict[str, Any]:
    cp = run(config["ledger"]["adapter_command"], cwd=cwd, timeout=min(10, remaining(started, wall)),
             input_bytes=canonical(request) + b"\n", env=ledger_env(), max_output=131072)
    if cp.returncode != 0:
        raise SecurityError("ledger_adapter_failed")
    try:
        value = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise SecurityError("ledger_response_invalid") from exc
    if not isinstance(value, dict):
        raise SecurityError("ledger_response_not_object")
    return value


def ledger_request(operation: str, envelope: dict[str, Any], **extra: Any) -> dict[str, Any]:
    value = {"operation": operation, "job_id": envelope["job_id"], "lease_token": envelope["lease_token"]}
    value.update(extra)
    return value


def verify_qa_bundle(directory: Path, payload: dict[str, Any], maximum: int) -> dict[str, Any]:
    if not directory.is_dir() or directory.is_symlink():
        raise SecurityIntegrityError("qa_evidence_directory_invalid")
    evidence_path, manifest_path = directory / "qa-evidence.json", directory / "manifest.json"
    if not evidence_path.is_file() or evidence_path.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise SecurityIntegrityError("required_qa_evidence_missing")
    if evidence_path.stat().st_size + manifest_path.stat().st_size > maximum:
        raise SecurityIntegrityError("qa_evidence_budget_exhausted")
    if sha256_file(evidence_path) != payload["qa_evidence_sha256"]:
        raise SecurityIntegrityError("qa_evidence_anchor_mismatch")
    if sha256_file(manifest_path) != payload["qa_manifest_sha256"]:
        raise SecurityIntegrityError("qa_manifest_anchor_mismatch")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecurityIntegrityError("qa_evidence_json_invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), dict):
        raise SecurityIntegrityError("qa_manifest_schema_invalid")
    expected = set(manifest["files"])
    actual = {p.name for p in directory.iterdir() if p.is_file() and not p.is_symlink()}
    if actual != expected | {"manifest.json"}:
        raise SecurityIntegrityError("qa_evidence_file_set_mismatch")
    total = 0
    for name, metadata in manifest["files"].items():
        if PurePosixPath(name).name != name or not isinstance(metadata, dict):
            raise SecurityIntegrityError("qa_manifest_filename_invalid")
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise SecurityIntegrityError(f"qa_artifact_missing:{name}")
        total += path.stat().st_size
        if total > maximum:
            raise SecurityIntegrityError("qa_evidence_budget_exhausted")
        if metadata.get("sha256") != sha256_file(path) or metadata.get("bytes") != path.stat().st_size:
            raise SecurityIntegrityError(f"qa_artifact_hash_mismatch:{name}")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise SecurityIntegrityError("qa_evidence_schema_invalid")
    if evidence.get("job_id") != payload["qa_job_id"]:
        raise SecurityIntegrityError("qa_job_binding_mismatch")
    if evidence.get("qa_status") != "qa_passed" or evidence.get("admission_verified") is not True:
        raise SecurityIntegrityError("qa_not_passed")
    if evidence.get("promotion_authorized") is not False or evidence.get("next_stage") != "B035 security review":
        raise SecurityIntegrityError("qa_handoff_boundary_invalid")
    for key in ("base_commit", "head_commit", "branch"):
        if evidence.get(key) != payload[key]:
            raise SecurityIntegrityError(f"qa_{key}_binding_mismatch")
    selected = evidence.get("selected_profile_ids")
    profiles = evidence.get("profiles")
    if not isinstance(selected, list) or not isinstance(profiles, list) or len(selected) != len(profiles):
        raise SecurityIntegrityError("qa_profile_evidence_invalid")
    seen: list[str] = []
    for profile in profiles:
        if not isinstance(profile, dict) or not isinstance(profile.get("profile_id"), str) or profile.get("status") != "pass":
            raise SecurityIntegrityError("qa_profile_not_passed")
        seen.append(profile["profile_id"])
        if not isinstance(profile.get("log_sha256"), str) or not HEX64.fullmatch(profile["log_sha256"]):
            raise SecurityIntegrityError("qa_profile_log_digest_invalid")
    if seen != selected:
        raise SecurityIntegrityError("qa_profile_selection_mismatch")
    return evidence


def verify_candidate(git: GitBudget, source: Path, payload: dict[str, Any], maximum_diff: int) -> tuple[bytes, list[str]]:
    base, head, branch = payload["base_commit"], payload["head_commit"], payload["branch"]
    for name, sha in (("base", base), ("head", head)):
        resolved = git.read(source, ["rev-parse", "--verify", f"{sha}^{{commit}}"], integrity=True).decode().strip()
        if resolved != sha:
            raise SecurityIntegrityError(f"{name}_commit_mismatch")
    branch_head = git.read(source, ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"], integrity=True).decode().strip()
    if branch_head != head:
        raise SecurityIntegrityError("candidate_branch_head_mismatch")
    git.ancestor(source, base, head)
    parents = git.read(source, ["rev-list", "--parents", "-n", "1", head], integrity=True).decode().strip().split()
    if parents != [head, base]:
        raise SecurityIntegrityError("candidate_history_changed")
    diff = git.read(source, ["diff", "--binary", "--no-ext-diff", "--no-renames", base, head], integrity=True, maximum=maximum_diff + 1)
    if len(diff) > maximum_diff:
        raise SecurityIntegrityError("security_diff_budget_exhausted")
    raw_paths = git.read(source, ["diff", "--name-only", "--no-renames", base, head], integrity=True, maximum=256 * 1024).decode("utf-8", errors="strict").splitlines()
    paths = [validate_rel_path(path) for path in raw_paths if path]
    if not paths:
        raise SecurityIntegrityError("candidate_diff_empty")
    return diff, paths


def finding(severity: str, category: str, rationale: str, *, path: str | None = None,
            line: int | None = None, evidence: str = "deterministic", remediation: str = "Review and remediate before release.") -> dict[str, Any]:
    return {
        "severity": severity, "category": category, "path": path, "line": line,
        "rationale": rationale, "evidence": evidence, "remediation": remediation,
        "source": "deterministic",
    }


def tree_mode(git: GitBudget, source: Path, commit: str, path: str) -> str | None:
    data = git.read(source, ["ls-tree", commit, "--", path], integrity=True, maximum=65536).decode("utf-8", errors="strict").strip()
    if not data:
        return None
    first = data.splitlines()[0]
    mode = first.split(" ", 1)[0]
    return mode


def read_blob(git: GitBudget, source: Path, head: str, path: str, maximum: int) -> bytes | None:
    mode = tree_mode(git, source, head, path)
    if mode is None:
        return None
    if mode in {"120000", "160000"}:
        return b""
    return git.read(source, ["show", f"{head}:{path}"], integrity=True, maximum=maximum + 1)


def deterministic_analysis(git: GitBudget, source: Path, payload: dict[str, Any], paths: list[str], diff: bytes,
                           config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    findings: list[dict[str, Any]] = []
    modes: dict[str, str] = {}
    allowed = payload["allowed_paths"]
    protected = config["policy"]["protected_path_prefixes"]
    dependency_files = set(config["policy"]["dependency_files"])
    max_files = config["budgets"]["max_scanned_files"]
    max_bytes = config["budgets"]["max_scanned_bytes"]
    if len(paths) > max_files:
        raise SecurityIntegrityError("security_scanned_file_budget_exhausted")
    scanned = 0

    private_key = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")
    credential = re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*['\"][^'\"\r\n]{8,}['\"]")
    shell_pipe = re.compile(rb"(?i)(?:curl|wget)[^\r\n|]{0,240}\|\s*(?:ba)?sh\b")
    shell_true = re.compile(rb"(?i)(?:shell\s*=\s*True|subprocess\.[A-Za-z_]+\([^\r\n]{0,300}shell\s*=\s*True)")
    os_system = re.compile(rb"\bos\.system\s*\(")
    write_all = re.compile(rb"(?im)^\s*permissions\s*:\s*write-all\s*$")
    pr_target = re.compile(rb"(?im)^\s*pull_request_target\s*:")

    for path in paths:
        if not path_allowed(path, allowed):
            findings.append(finding("high", "path_scope", "Candidate changed a path outside the declared implementation scope.", path=path, evidence="allowed_paths"))
        if any(path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/") for prefix in protected):
            findings.append(finding("medium", "path_scope", "Candidate changes a security-sensitive/protected path.", path=path, evidence="protected_path_prefixes", remediation="Require explicit security justification and targeted review."))
        if PurePosixPath(path).name in dependency_files:
            findings.append(finding("medium", "supply_chain", "Candidate changes dependency or lockfile metadata.", path=path, evidence="dependency_files", remediation="Review dependency provenance and version changes before release."))

        mode = tree_mode(git, source, payload["head_commit"], path)
        if mode is None:
            continue
        modes[path] = mode
        if mode == "160000":
            findings.append(finding("critical", "submodule", "Candidate introduces or changes a Git submodule entry.", path=path, evidence="git_mode:160000", remediation="Remove the submodule or review it through an explicitly authorized dependency process."))
            continue
        if mode == "120000":
            findings.append(finding("high", "symlink", "Candidate introduces or changes a symbolic link.", path=path, evidence="git_mode:120000", remediation="Replace the symlink with a regular file or obtain explicit security approval."))
            continue
        base_mode = tree_mode(git, source, payload["base_commit"], path)
        if mode == "100755" and base_mode != "100755":
            findings.append(finding("medium", "permissions", "Candidate makes a file executable.", path=path, evidence=f"mode:{base_mode}->{mode}", remediation="Confirm executable permission is required and safe."))

        remaining_bytes = max_bytes - scanned
        if remaining_bytes <= 0:
            raise SecurityIntegrityError("security_scanned_byte_budget_exhausted")
        blob = read_blob(git, source, payload["head_commit"], path, remaining_bytes)
        if blob is None:
            continue
        if len(blob) > remaining_bytes:
            raise SecurityIntegrityError("security_scanned_byte_budget_exhausted")
        scanned += len(blob)

        checks = (
            (private_key, "critical", "secret", "Candidate contains private-key material.", "Remove the key immediately and rotate/revoke any exposed credential."),
            (credential, "high", "credential", "Candidate contains an apparent hard-coded credential/token/password.", "Remove the credential and use the approved secret-injection mechanism."),
            (shell_pipe, "high", "unsafe_execution", "Candidate pipes downloaded network content directly into a shell.", "Pin and verify downloaded artefacts; do not execute network content directly."),
            (shell_true, "high", "injection", "Candidate enables shell interpretation in a subprocess path.", "Use an argv list with shell disabled and validate all inputs."),
            (os_system, "medium", "unsafe_execution", "Candidate invokes os.system().", "Use a bounded subprocess argv without a shell."),
            (write_all, "high", "workflow", "Candidate grants GitHub Actions write-all permissions.", "Grant only the minimal explicit workflow permissions required."),
            (pr_target, "medium", "workflow", "Candidate uses pull_request_target, which executes in a privileged base-repository context.", "Prefer pull_request or strictly isolate untrusted checkout/content."),
        )
        for regex, severity, category, rationale, remediation in checks:
            match = regex.search(blob)
            if match:
                line = blob[:match.start()].count(b"\n") + 1
                findings.append(finding(severity, category, rationale, path=path, line=line,
                                        evidence=f"deterministic_pattern:{regex.pattern[:80]!r}", remediation=remediation))

    if private_key.search(diff):
        # Diff-level guard catches removed/renamed-looking material even when the head blob is absent.
        findings.append(finding("high", "secret", "The reconstructed patch contains private-key marker text.", evidence="reconstructed_diff", remediation="Inspect the patch history and ensure no secret material is introduced or exposed."))
    return findings, modes


def validate_model_review(value: Any, changed_paths: set[str]) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SecurityError("security_model_schema_invalid")
    summary = value.get("summary")
    findings = value.get("findings")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 6000:
        raise SecurityError("security_model_summary_invalid")
    if not isinstance(findings, list) or len(findings) > 64:
        raise SecurityError("security_model_findings_invalid")
    normalized: list[dict[str, Any]] = []
    for raw in findings:
        if not isinstance(raw, dict) or set(raw) != {"severity", "category", "path", "line", "rationale", "evidence", "remediation"}:
            raise SecurityError("security_model_finding_shape_invalid")
        severity, category = raw["severity"], raw["category"]
        if severity not in SAFE_SEVERITIES or category not in SAFE_CATEGORIES:
            raise SecurityError("security_model_finding_enum_invalid")
        path = raw["path"]
        if path is not None and (not isinstance(path, str) or path not in changed_paths):
            raise SecurityError("security_model_finding_path_invalid")
        line = raw["line"]
        if line is not None and (not isinstance(line, int) or not 1 <= line <= 10_000_000):
            raise SecurityError("security_model_finding_line_invalid")
        for key in ("rationale", "evidence", "remediation"):
            if not isinstance(raw[key], str) or not raw[key].strip() or len(raw[key]) > 6000:
                raise SecurityError(f"security_model_finding_text_invalid:{key}")
        item = dict(raw)
        item["source"] = "model"
        normalized.append(item)
    return normalized, summary


def call_model(config: dict[str, Any], prompt: dict[str, Any], source: Path, started: float, wall: int,
               changed_paths: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model = config["model"]
    prompt_bytes = canonical(prompt) + b"\n"
    if len(prompt_bytes) > model["max_prompt_bytes"]:
        raise SecurityError("security_model_prompt_budget_exhausted")
    env = minimal_env({"FZH_MODEL_ALIAS": str(model.get("alias", "fzh-free-auto")), "FZH_MODEL_MAX_OUTPUT_TOKENS": str(model["max_output_tokens"])})
    for key in ("FZH_LITELLM_BASE_URL", "FZH_LITELLM_API_KEY"):
        if key in os.environ:
            env[key] = os.environ[key]
    cp = run(model["adapter_command"], cwd=source, timeout=min(model["timeout_seconds"], remaining(started, wall)),
             input_bytes=prompt_bytes, env=env, max_output=model["max_response_bytes"])
    if cp.returncode != 0:
        raise SecurityError("security_model_adapter_failed")
    try:
        envelope = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise SecurityError("security_model_invalid_json") from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("review"), dict):
        raise SecurityError("security_model_envelope_invalid")
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    if isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] > model["max_output_tokens"]:
        raise SecurityError("security_model_output_token_budget_exceeded")
    findings, summary = validate_model_review(envelope["review"], changed_paths)
    metadata = {
        "id": str(envelope.get("model", model.get("alias", "unknown"))), "summary": summary,
        "prompt_bytes": len(prompt_bytes), "response_bytes": len(cp.stdout), "reported_usage": usage,
    }
    return findings, metadata


def derive_status(findings: list[dict[str, Any]], medium_threshold: int) -> str:
    severities = [item["severity"] for item in findings]
    if "critical" in severities or "high" in severities:
        return "security_failed"
    if severities.count("medium") >= medium_threshold:
        return "security_failed"
    return "security_passed"


def write_bundle(output: Path, evidence: dict[str, Any], diff: bytes, model_review: dict[str, Any] | None) -> dict[str, str]:
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    (output / "reconstructed.diff").write_bytes(diff)
    if model_review is not None:
        (output / "security-model.json").write_text(json.dumps(model_review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence_path = output / "security-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in sorted(output.iterdir()) if p.is_file()}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps({"schema_version": 1, "files": files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"evidence_sha256": sha256_file(evidence_path), "manifest_sha256": sha256_file(manifest_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-envelope", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--qa-evidence-dir", required=True, type=Path)
    parser.add_argument("--security-output-root", required=True, type=Path)
    args = parser.parse_args()

    started = time.monotonic()
    envelope = validate_envelope(load_json(args.job_envelope))
    config = validate_config(load_json(args.config, 256 * 1024))
    payload = envelope["payload"]
    source, qa_dir, output_root = args.source_repo.resolve(), args.qa_evidence_dir.resolve(), args.security_output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / str(envelope["job_id"])
    if output.exists():
        raise SecurityError("security_output_exists")
    git = GitBudget(config["budgets"]["max_git_commands"], started, config["budgets"]["max_wall_seconds"])
    top = git.read(source, ["rev-parse", "--show-toplevel"], integrity=True).decode().strip()
    if Path(top).resolve() != source:
        raise SecurityError("source_repo_must_be_toplevel")

    start_record = call_ledger(config, ledger_request("start", envelope), source, started, config["budgets"]["max_wall_seconds"])
    if start_record.get("status") != "running":
        raise SecurityError("security_ledger_start_failed")
    execution_started = True
    try:
        heartbeat = call_ledger(config, ledger_request("heartbeat", envelope, lease_seconds=config["ledger"]["heartbeat_seconds"]), source, started, config["budgets"]["max_wall_seconds"])
        if not heartbeat.get("lease_expires_at"):
            raise SecurityError("security_ledger_heartbeat_failed")
        try:
            qa_evidence = verify_qa_bundle(qa_dir, payload, config["budgets"]["max_evidence_bytes"])
            diff, paths = verify_candidate(git, source, payload, config["budgets"]["max_diff_bytes"])
        except SecurityIntegrityError as exc:
            evidence = {
                "schema_version": 1, "job_id": envelope["job_id"], "qa_job_id": payload["qa_job_id"],
                "security_status": "reject", "admission_verified": False, "admission_error": str(exc),
                "candidate_executed": False, "analyzers_executed": False,
                "promotion_authorized": False, "next_stage": None,
            }
            hashes = write_bundle(output, evidence, b"", None)
            completed = call_ledger(config, ledger_request("complete", envelope, result={"security_status": "reject", **hashes, "promotion_authorized": False}), source, started, config["budgets"]["max_wall_seconds"])
            if completed.get("status") != "succeeded":
                raise SecurityError("security_ledger_complete_failed")
            print(str(output))
            return 0

        deterministic, modes = deterministic_analysis(git, source, payload, paths, diff, config)
        model_findings: list[dict[str, Any]] = []
        model_record: dict[str, Any] | None = None
        model_raw: dict[str, Any] | None = None
        if config["model"]["enabled"]:
            prompt = {
                "schema_version": 1,
                "security_job_id": envelope["job_id"],
                "objective": payload["objective"],
                "acceptance_criteria": payload.get("acceptance_criteria", []),
                "base_commit": payload["base_commit"], "head_commit": payload["head_commit"],
                "changed_paths": paths,
                "deterministic_findings": deterministic,
                "reconstructed_diff": diff.decode("utf-8", errors="replace"),
                "instruction": "Return structured security findings only. Do not propose commands, tools, a verdict, merge, release or deployment actions.",
            }
            model_findings, model_record = call_model(config, prompt, source, started, config["budgets"]["max_wall_seconds"], set(paths))
            model_raw = {"schema_version": 1, "findings": model_findings, "model": model_record}

        all_findings = [*deterministic, *model_findings]
        status = derive_status(all_findings, config["policy"]["medium_failure_threshold"])
        evidence = {
            "schema_version": 1, "job_id": envelope["job_id"], "qa_job_id": payload["qa_job_id"],
            "base_commit": payload["base_commit"], "head_commit": payload["head_commit"], "branch": payload["branch"],
            "qa_evidence_sha256": payload["qa_evidence_sha256"], "qa_manifest_sha256": payload["qa_manifest_sha256"],
            "admission_verified": True, "candidate_executed": False, "analyzers_executed": True,
            "qa_profiles_verified": qa_evidence.get("selected_profile_ids", []),
            "reconstructed_diff_sha256": sha256_bytes(diff), "changed_paths": paths, "head_modes": modes,
            "deterministic_findings": deterministic, "model_findings": model_findings, "findings": all_findings,
            "model": model_record, "security_status": status, "git_commands": git.used,
            "promotion_authorized": False, "next_stage": "B036 release" if status == "security_passed" else None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        hashes = write_bundle(output, evidence, diff, model_raw)
        completed = call_ledger(config, ledger_request("complete", envelope, result={
            "security_status": status, "head_commit": payload["head_commit"], **hashes, "promotion_authorized": False,
        }), source, started, config["budgets"]["max_wall_seconds"])
        if completed.get("status") != "succeeded":
            raise SecurityError("security_ledger_complete_failed")
        print(str(output))
        return 0
    except Exception as exc:
        if execution_started:
            try:
                call_ledger(config, ledger_request(
                    "fail", envelope, error_code="b035_security_review_failed", error_summary=str(exc)[:1800],
                    retryable=True, retry_delay_seconds=config["ledger"]["retry_delay_seconds"],
                ), source, started, config["budgets"]["max_wall_seconds"])
            except Exception:
                pass
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SecurityError as exc:
        print(f"B035 security reviewer: {exc}", file=sys.stderr)
        raise SystemExit(2)

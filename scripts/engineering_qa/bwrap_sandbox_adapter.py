#!/usr/bin/env python3
"""Production B034 sandbox adapter using user-systemd + Bubblewrap.

The QA worker supplies only a job UUID, the fixed job workspace path and a
profile ID. Commands/resource limits are loaded from a root-owned profile file.
Candidate code is mounted read-only at /candidate then copied into an in-sandbox
tmpfs /workspace. Network is absent because Bubblewrap unshares all namespaces.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

PROFILE_CONFIG = pathlib.Path("/etc/frankensteinzhermes/qa-profiles.json")
WORKSPACE_ROOT = pathlib.Path("/var/lib/frankensteinzhermes/qa-workspaces")
MAX_REQUEST_BYTES = 64 * 1024
SAFE_PROFILE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
SAFE_RESOURCE = re.compile(r"^[A-Za-z0-9.%]+$")


def fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def minimal_env() -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
    }


def validate_root_owned_file(path: pathlib.Path) -> None:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise ValueError("QA profile config must be root-owned and non-group/world-writable")


def load_profile(profile_id: str) -> dict:
    validate_root_owned_file(PROFILE_CONFIG)
    raw = PROFILE_CONFIG.read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError("QA profile config too large")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("profiles"), dict):
        raise ValueError("invalid QA profile config")
    profile = value["profiles"].get(profile_id)
    if not isinstance(profile, dict):
        raise ValueError("unknown QA profile")
    command = profile.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x and len(x) <= 4096 for x in command):
        raise ValueError("invalid QA profile command")
    timeout = profile.get("timeout_seconds")
    tasks = profile.get("tasks_max")
    output = profile.get("max_output_bytes")
    memory = profile.get("memory_max")
    cpu = profile.get("cpu_quota")
    if not isinstance(timeout, int) or not 1 <= timeout <= 900:
        raise ValueError("invalid QA profile timeout")
    if not isinstance(tasks, int) or not 8 <= tasks <= 256:
        raise ValueError("invalid QA profile task limit")
    if not isinstance(output, int) or not 1024 <= output <= 4 * 1024 * 1024:
        raise ValueError("invalid QA profile output limit")
    if not isinstance(memory, str) or not SAFE_RESOURCE.fullmatch(memory):
        raise ValueError("invalid QA memory limit")
    if not isinstance(cpu, str) or not SAFE_RESOURCE.fullmatch(cpu):
        raise ValueError("invalid QA CPU limit")
    return profile


def validate_request(value: object) -> tuple[str, pathlib.Path, str]:
    if not isinstance(value, dict) or value.get("operation") != "run_profile":
        raise ValueError("unsupported sandbox operation")
    job_id = str(uuid.UUID(str(value.get("job_id"))))
    profile_id = value.get("profile_id")
    if not isinstance(profile_id, str) or not SAFE_PROFILE.fullmatch(profile_id):
        raise ValueError("invalid profile ID")
    workspace = pathlib.Path(str(value.get("workspace", "")))
    expected = WORKSPACE_ROOT / job_id
    if workspace.is_symlink() or workspace.resolve() != expected.resolve() or not workspace.is_dir():
        raise ValueError("workspace outside fixed QA root")
    return job_id, workspace, profile_id


def stop_unit(unit: str) -> None:
    subprocess.run(["systemctl", "--user", "stop", unit], env=minimal_env(), stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=10, check=False)


def collect_bounded(proc: subprocess.Popen[bytes], unit: str, timeout: int, maximum: int) -> tuple[str, int, bytes, bytes, float]:
    assert proc.stdout is not None and proc.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    total = 0
    started = time.monotonic()
    forced_status: str | None = None
    while selector.get_map():
        elapsed = time.monotonic() - started
        if elapsed > timeout:
            forced_status = "timeout"
            stop_unit(unit)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        for key, _ in selector.select(timeout=min(0.25, max(0.01, timeout - elapsed))):
            data = os.read(key.fileobj.fileno(), 65536)
            if not data:
                selector.unregister(key.fileobj)
                continue
            total += len(data)
            if total > maximum:
                forced_status = "output_exhausted"
                stop_unit(unit)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
            chunks[key.data].append(data)
        if forced_status:
            break
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        stop_unit(unit)
        proc.kill()
        rc = proc.wait(timeout=5)
    elapsed = time.monotonic() - started
    status = forced_status or ("pass" if rc == 0 else "fail")
    return status, rc, b"".join(chunks["stdout"]), b"".join(chunks["stderr"]), elapsed


def build_command(job_id: str, workspace: pathlib.Path, profile_id: str, profile: dict) -> tuple[str, list[str]]:
    unit = f"fzh-qa-{job_id.replace('-', '')[:20]}-{profile_id.replace(':', '-')[:24]}"
    bwrap = [
        "bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--clearenv",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home",
        "--dir", "/home/qa", "--tmpfs", "/workspace",
        "--setenv", "HOME", "/home/qa", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "LANG", "C.UTF-8", "--ro-bind", str(workspace), "/candidate",
    ]
    for path in ("/usr", "/usr/local", "/bin", "/lib", "/lib64"):
        if pathlib.Path(path).exists():
            bwrap.extend(["--ro-bind", path, path])
    for path in ("/etc/ld.so.cache", "/etc/nsswitch.conf", "/etc/passwd", "/etc/group"):
        if pathlib.Path(path).is_file():
            bwrap.extend(["--ro-bind", path, path])
    bwrap.extend([
        "/bin/sh", "-c", "cp -a /candidate/. /workspace/ && cd /workspace && exec \"$@\"",
        "fzh-qa", *profile["command"],
    ])
    command = [
        "systemd-run", "--user", "--quiet", "--wait", "--pipe", "--collect", f"--unit={unit}",
        "--property=NoNewPrivileges=yes", "--property=RestrictSUIDSGID=yes",
        f"--property=MemoryMax={profile['memory_max']}", f"--property=TasksMax={profile['tasks_max']}",
        f"--property=CPUQuota={profile['cpu_quota']}", f"--property=RuntimeMaxSec={profile['timeout_seconds'] + 5}",
        "--", *bwrap,
    ]
    return unit, command


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        return fail("sandbox request too large")
    try:
        request = json.loads(raw)
        job_id, workspace, profile_id = validate_request(request)
        profile = load_profile(profile_id)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return fail(f"invalid sandbox request/config: {exc}")
    if shutil.which("bwrap") is None or shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return fail("required sandbox runtime unavailable", 3)
    unit, command = build_command(job_id, workspace, profile_id, profile)
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=minimal_env(), start_new_session=True)
        status, rc, stdout, stderr, elapsed = collect_bounded(
            proc, unit, profile["timeout_seconds"], profile["max_output_bytes"]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        stop_unit(unit)
        return fail(f"sandbox infrastructure failure: {exc.__class__.__name__}", 3)
    result = {
        "schema_version": 1, "profile_id": profile_id, "status": status, "returncode": rc,
        "duration_seconds": round(elapsed, 3),
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "network": "unshared", "workspace_mode": "tmpfs-copy-from-readonly-candidate",
        "resource_policy": {
            "memory_max": profile["memory_max"], "tasks_max": profile["tasks_max"],
            "cpu_quota": profile["cpu_quota"], "timeout_seconds": profile["timeout_seconds"],
            "max_output_bytes": profile["max_output_bytes"],
        },
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

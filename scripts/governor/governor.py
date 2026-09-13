#!/usr/bin/env python3
"""FrankensteinzHermes seed-node resource admission governor.

This utility deliberately stays small: it observes host pressure, decides whether
new work may start, exposes JSON/Prometheus status, and reserves finite heavy-job
slots using advisory file locks. It does not kill running core services.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "config" / "resource-policy.yaml"
GIB = 1024 ** 3


def env_float(name: str, default: float | None = None) -> float | None:
    value = os.getenv(name)
    return float(value) if value is not None else default


def read_policy(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("resource policy must be a version: 1 mapping")
    return data


def memory_state() -> tuple[int, int]:
    forced_available = env_float("FZH_TEST_MEM_AVAILABLE_GIB")
    forced_total = env_float("FZH_TEST_MEM_TOTAL_GIB")
    if forced_available is not None:
        total = forced_total if forced_total is not None else 32.0
        return int(total * GIB), int(forced_available * GIB)

    fields: dict[str, int] = {}
    with Path("/proc/meminfo").open("r", encoding="utf-8") as handle:
        for line in handle:
            key, value = line.split(":", 1)
            fields[key] = int(value.strip().split()[0]) * 1024
    return fields["MemTotal"], fields["MemAvailable"]


def disk_state() -> tuple[int, int, float]:
    forced_free = env_float("FZH_TEST_DISK_FREE_GIB")
    forced_used = env_float("FZH_TEST_DISK_USED_PERCENT")
    if forced_free is not None or forced_used is not None:
        free = forced_free if forced_free is not None else 100.0
        used = forced_used if forced_used is not None else 10.0
        return 200 * GIB, int(free * GIB), float(used)

    usage = shutil.disk_usage("/")
    used_percent = ((usage.total - usage.free) / usage.total) * 100.0
    return usage.total, usage.free, used_percent


def load_state() -> tuple[float, int]:
    forced = env_float("FZH_TEST_LOAD1")
    cpu_count = int(os.getenv("FZH_TEST_CPU_COUNT", str(os.cpu_count() or 1)))
    load1 = forced if forced is not None else os.getloadavg()[0]
    return float(load1), cpu_count


def swap_used_percent() -> float:
    forced = env_float("FZH_TEST_SWAP_USED_PERCENT")
    if forced is not None:
        return forced
    total = free = 0
    with Path("/proc/meminfo").open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("SwapTotal:"):
                total = int(line.split()[1])
            elif line.startswith("SwapFree:"):
                free = int(line.split()[1])
    if total <= 0:
        return 0.0
    return ((total - free) / total) * 100.0


def max_temperature_c() -> float | None:
    forced = env_float("FZH_TEST_TEMP_C")
    if forced is not None:
        return forced
    values: list[float] = []
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            raw = float(path.read_text(encoding="utf-8").strip())
            value = raw / 1000.0 if raw > 200 else raw
            if -20 <= value <= 150:
                values.append(value)
        except (OSError, ValueError):
            continue
    return max(values) if values else None


def snapshot(policy: dict[str, Any], work_class: str) -> dict[str, Any]:
    total, available = memory_state()
    _, disk_free, disk_used_percent = disk_state()
    load1, cpus = load_state()
    swap_percent = swap_used_percent()
    temp = max_temperature_c()

    reasons: list[str] = []
    mem_key = "heavy_min_available_gib" if work_class == "heavy" else "light_min_available_gib"
    min_mem = float(policy["memory"][mem_key]) * GIB
    if available < min_mem:
        reasons.append("memory_pressure")

    if disk_free < float(policy["root_disk"]["min_free_gib"]) * GIB:
        reasons.append("disk_free_below_minimum")
    if disk_used_percent >= float(policy["root_disk"]["max_used_percent"]):
        reasons.append("disk_usage_above_maximum")

    if work_class == "heavy":
        load_limit = float(policy["load"]["heavy_max_load_per_cpu"]) * cpus
        if load1 > load_limit:
            reasons.append("load_pressure")
        if swap_percent > float(policy["swap"]["heavy_max_used_percent"]):
            reasons.append("swap_pressure")
        thermal = policy.get("thermal", {})
        if thermal.get("enabled", False) and temp is not None and temp >= float(thermal["heavy_max_celsius"]):
            reasons.append("thermal_pressure")

    return {
        "work_class": work_class,
        "admitted": not reasons,
        "reasons": reasons,
        "memory_total_bytes": total,
        "memory_available_bytes": available,
        "root_disk_free_bytes": disk_free,
        "root_disk_used_percent": round(disk_used_percent, 2),
        "load1": round(load1, 2),
        "logical_cpus": cpus,
        "swap_used_percent": round(swap_percent, 2),
        "max_temperature_celsius": None if temp is None else round(temp, 1),
        "timestamp_seconds": int(time.time()),
    }


def prometheus(snapshot_data: dict[str, Any]) -> str:
    admitted = 1 if snapshot_data["admitted"] else 0
    klass = snapshot_data["work_class"]
    lines = [
        "# TYPE fzh_governor_admit gauge",
        f'fzh_governor_admit{{class="{klass}"}} {admitted}',
        "# TYPE fzh_governor_memory_available_bytes gauge",
        f'fzh_governor_memory_available_bytes {snapshot_data["memory_available_bytes"]}',
        "# TYPE fzh_governor_root_disk_free_bytes gauge",
        f'fzh_governor_root_disk_free_bytes {snapshot_data["root_disk_free_bytes"]}',
        "# TYPE fzh_governor_root_disk_used_percent gauge",
        f'fzh_governor_root_disk_used_percent {snapshot_data["root_disk_used_percent"]}',
        "# TYPE fzh_governor_load1 gauge",
        f'fzh_governor_load1 {snapshot_data["load1"]}',
        "# TYPE fzh_governor_swap_used_percent gauge",
        f'fzh_governor_swap_used_percent {snapshot_data["swap_used_percent"]}',
        "# TYPE fzh_governor_snapshot_timestamp_seconds gauge",
        f'fzh_governor_snapshot_timestamp_seconds {snapshot_data["timestamp_seconds"]}',
    ]
    if snapshot_data["max_temperature_celsius"] is not None:
        lines.extend([
            "# TYPE fzh_governor_max_temperature_celsius gauge",
            f'fzh_governor_max_temperature_celsius {snapshot_data["max_temperature_celsius"]}',
        ])
    return "\n".join(lines) + "\n"


def acquire_heavy_slot(policy: dict[str, Any]):
    lock_dir = Path(os.getenv("FZH_GOVERNOR_LOCK_DIR", policy["runtime"]["lock_directory"]))
    lock_dir.mkdir(parents=True, exist_ok=True)
    slots = int(policy["concurrency"]["heavy_slots"])
    handles = []
    for index in range(slots):
        handle = (lock_dir / f"heavy-{index}.lock").open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle, index
        except BlockingIOError:
            handles.append(handle)
    for handle in handles:
        handle.close()
    return None, None


def write_metrics(policy: dict[str, Any], data: dict[str, Any]) -> Path:
    directory = Path(os.getenv("FZH_TEXTFILE_DIR", policy["metrics"]["textfile_directory"]))
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / policy["metrics"]["filename"]
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(prometheus(data), encoding="utf-8")
    temporary.replace(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--class", dest="work_class", choices=("light", "heavy"), default="heavy")
    status.add_argument("--format", choices=("json", "prometheus"), default="json")
    status.add_argument("--write-metrics", action="store_true")

    run = sub.add_parser("run")
    run.add_argument("--class", dest="work_class", choices=("light", "heavy"), default="heavy")
    run.add_argument("argv", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    policy = read_policy(args.policy)
    data = snapshot(policy, args.work_class)

    if args.command == "status":
        if args.write_metrics:
            write_metrics(policy, data)
        print(json.dumps(data, sort_keys=True) if args.format == "json" else prometheus(data), end="\n" if args.format == "json" else "")
        return 0 if data["admitted"] else 75

    if not args.argv:
        parser.error("run requires a command after --")
    if args.argv[0] == "--":
        args.argv = args.argv[1:]
    if not args.argv:
        parser.error("run requires a non-empty command")
    if not data["admitted"]:
        print(json.dumps(data, sort_keys=True), file=sys.stderr)
        return 75

    slot_handle = None
    if args.work_class == "heavy":
        slot_handle, slot = acquire_heavy_slot(policy)
        if slot_handle is None:
            print(json.dumps({"admitted": False, "reasons": ["heavy_concurrency_limit"]}), file=sys.stderr)
            return 75
        print(f"governor: acquired heavy slot {slot}", file=sys.stderr)

    try:
        result = subprocess.run(args.argv, check=False)
        return result.returncode
    finally:
        if slot_handle is not None:
            fcntl.flock(slot_handle, fcntl.LOCK_UN)
            slot_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())

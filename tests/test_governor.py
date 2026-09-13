from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
GOV = ROOT / "scripts" / "governor" / "governor.py"
POLICY = ROOT / "config" / "resource-policy.yaml"


class GovernorTests(unittest.TestCase):
    def run_governor(self, *args: str, env_extra: dict[str, str] | None = None):
        env = os.environ.copy()
        env.update(
            {
                "FZH_TEST_MEM_TOTAL_GIB": "32",
                "FZH_TEST_MEM_AVAILABLE_GIB": "20",
                "FZH_TEST_DISK_FREE_GIB": "100",
                "FZH_TEST_DISK_USED_PERCENT": "20",
                "FZH_TEST_LOAD1": "1",
                "FZH_TEST_CPU_COUNT": "8",
                "FZH_TEST_SWAP_USED_PERCENT": "0",
                "FZH_TEST_TEMP_C": "45",
            }
        )
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(GOV), "--policy", str(POLICY), *args],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_policy_is_version_one_and_has_two_heavy_slots(self):
        data = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["concurrency"]["heavy_slots"], 2)

    def test_healthy_heavy_job_is_admitted(self):
        result = self.run_governor("status", "--class", "heavy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"admitted": true', result.stdout)

    def test_low_memory_denies_heavy_job(self):
        result = self.run_governor(
            "status", "--class", "heavy", env_extra={"FZH_TEST_MEM_AVAILABLE_GIB": "2"}
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("memory_pressure", result.stdout)

    def test_low_disk_denies_heavy_job(self):
        result = self.run_governor(
            "status", "--class", "heavy", env_extra={"FZH_TEST_DISK_FREE_GIB": "5"}
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("disk_free_below_minimum", result.stdout)

    def test_child_exit_code_propagates(self):
        with tempfile.TemporaryDirectory() as lock_dir:
            result = self.run_governor(
                "run", "--class", "heavy", "--", "sh", "-c", "exit 23",
                env_extra={"FZH_GOVERNOR_LOCK_DIR": lock_dir},
            )
        self.assertEqual(result.returncode, 23)

    def test_concurrency_limit_denies_third_heavy_job(self):
        with tempfile.TemporaryDirectory() as lock_dir:
            handles = []
            for index in range(2):
                handle = open(Path(lock_dir) / f"heavy-{index}.lock", "a+")
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                handles.append(handle)
            try:
                result = self.run_governor(
                    "run", "--class", "heavy", "--", "true",
                    env_extra={"FZH_GOVERNOR_LOCK_DIR": lock_dir},
                )
                self.assertEqual(result.returncode, 75)
                self.assertIn("heavy_concurrency_limit", result.stderr)
            finally:
                for handle in handles:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                    handle.close()

    def test_prometheus_output(self):
        result = self.run_governor("status", "--class", "heavy", "--format", "prometheus")
        self.assertEqual(result.returncode, 0)
        self.assertIn('fzh_governor_admit{class="heavy"} 1', result.stdout)
        self.assertIn("fzh_governor_memory_available_bytes", result.stdout)


if __name__ == "__main__":
    unittest.main()

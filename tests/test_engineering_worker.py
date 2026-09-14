import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "scripts/engineering_worker/worker.py"
FAKE_MODEL = ROOT / "tests/helpers/fake_engineering_model.py"
FAKE_GATE = ROOT / "tests/helpers/fake_action_gate.py"
DENY_GATE = ROOT / "tests/helpers/fake_action_gate_deny.py"

spec = importlib.util.spec_from_file_location("fzh_b032_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(worker)


def git(repo: pathlib.Path, *args: str) -> str:
    cp = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return cp.stdout.strip()


class EngineeringWorkerTests(unittest.TestCase):
    def make_repo(self, root: pathlib.Path, *, include_policy: bool = False) -> tuple[pathlib.Path, str]:
        repo = root / "source"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "CI Owner"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repo, check=True)
        (repo / "app").mkdir()
        (repo / "app/value.txt").write_text("old\n", encoding="utf-8")
        if include_policy:
            (repo / "policy").mkdir()
            (repo / "policy/safe.txt").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        return repo, git(repo, "rev-parse", "HEAD")

    def config(self, path: pathlib.Path, patch: pathlib.Path, *, max_tools: int = 30) -> pathlib.Path:
        value = {
            "schema_version": 1,
            "model": {
                "adapter_command": [sys.executable, str(FAKE_MODEL), str(patch)],
                "alias": "ci/fake",
                "max_calls": 1,
                "max_context_bytes": 32768,
                "max_prompt_bytes": 65536,
                "max_output_tokens": 256,
                "max_response_bytes": 65536,
                "timeout_seconds": 10,
            },
            "budgets": {
                "max_wall_seconds": 60,
                "max_tool_commands": max_tools,
                "max_changed_files": 4,
                "max_changed_bytes": 32768,
            },
            "test_allowlist": {
                "value-check": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('app/value.txt').read_text() == 'new\\n'",
                ]
            },
            "forbidden_path_prefixes": [".git/", ".github/", "policy/", "scripts/action_gate/", "bootstrap/"],
            "forbidden_paths": [
                "docs/AUTONOMY-CONSTITUTION.md",
                "docs/ARCHITECTURE.md",
                "docs/THREAT-MODEL.md",
                "config/autonomy-policy.example.yaml",
            ],
        }
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def envelope(self, path: pathlib.Path, base: str, *, allowed=None, context=None, tests=None, output_branch=None) -> tuple[pathlib.Path, str]:
        job_id = str(uuid.uuid4())
        value = {
            "job_id": job_id,
            "attempt_id": 1,
            "lease_token": str(uuid.uuid4()),
            "job_kind": "engineering.implement",
            "data_classification": "INTERNAL",
            "payload": {
                "objective": "Change app/value.txt from old to new.",
                "base_commit": base,
                "allowed_paths": allowed or ["app/"],
                "context_paths": context if context is not None else ["app/value.txt"],
                "test_ids": tests if tests is not None else ["value-check"],
            },
        }
        if output_branch is not None:
            value["payload"]["output_branch"] = output_branch
        path.write_text(json.dumps(value), encoding="utf-8")
        return path, job_id

    def run_worker(self, repo, job, config, workspace, evidence, gate=FAKE_GATE):
        return subprocess.run(
            [
                sys.executable,
                str(WORKER_PATH),
                "--job-envelope", str(job),
                "--config", str(config),
                "--source-repo", str(repo),
                "--workspace-root", str(workspace),
                "--evidence-root", str(evidence),
                "--gate-command", sys.executable, str(gate),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_path_validation_rejects_escape_and_git_metadata(self):
        for value in ("../outside", "/etc/passwd", ".git/config", "app/../policy/x"):
            with self.subTest(value=value), self.assertRaises(worker.WorkerError):
                worker.validate_rel_path(value)

    def test_patch_parser_rejects_symlink_and_rename(self):
        with self.assertRaises(worker.WorkerError):
            worker.parse_patch_paths(b"diff --git a/app/x b/app/x\nnew file mode 120000\n")
        with self.assertRaises(worker.WorkerError):
            worker.parse_patch_paths(b"diff --git a/app/a b/app/b\nrename from app/a\nrename to app/b\n")

    def test_success_creates_only_isolated_job_branch_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, base = self.make_repo(root)
            patch = root / "patch.diff"
            patch.write_text(
                "diff --git a/app/value.txt b/app/value.txt\n"
                "--- a/app/value.txt\n"
                "+++ b/app/value.txt\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n",
                encoding="utf-8",
            )
            config = self.config(root / "config.json", patch)
            job, job_id = self.envelope(root / "job.json", base)
            workspace = root / "workspaces"
            evidence = root / "evidence"
            cp = self.run_worker(repo, job, config, workspace, evidence)
            self.assertEqual(cp.returncode, 0, cp.stderr)

            # Canonical checkout/main is unchanged and clean.
            self.assertEqual(git(repo, "rev-parse", "HEAD"), base)
            self.assertEqual(git(repo, "branch", "--show-current"), "main")
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual((repo / "app/value.txt").read_text(encoding="utf-8"), "old\n")

            branch = f"fzh/job-{job_id.replace('-', '')[:12]}"
            head = git(repo, "rev-parse", branch)
            self.assertNotEqual(head, base)
            self.assertEqual(git(repo, "show", f"{branch}:app/value.txt"), "new")
            self.assertFalse((workspace / job_id.replace("-", "")[:12]).exists())

            bundle = evidence / job_id
            report = json.loads((bundle / "implementation-evidence.json").read_text(encoding="utf-8"))
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(report["base_commit"], base)
            self.assertEqual(report["head_commit"], head)
            self.assertEqual(report["branch"], branch)
            self.assertEqual(report["changed_paths"], ["app/value.txt"])
            self.assertFalse(report["promotion_authorized"])
            self.assertEqual(report["next_stage"], "B033 independent reviewer")
            self.assertEqual(report["gate"]["event_id"], "11111111-1111-4111-8111-111111111111")
            self.assertEqual(report["model"]["id"], "ci/fake-model")
            self.assertEqual(report["model"]["calls"], 1)
            self.assertLessEqual(report["tools"]["commands"], report["tools"]["max_commands"])
            self.assertIn("model.patch", manifest["files"])
            self.assertIn("implementation-evidence.json", manifest["files"])

    def test_forbidden_path_patch_is_rejected_and_branch_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, base = self.make_repo(root, include_policy=True)
            patch = root / "patch.diff"
            patch.write_text(
                "diff --git a/policy/safe.txt b/policy/safe.txt\n"
                "--- a/policy/safe.txt\n"
                "+++ b/policy/safe.txt\n"
                "@@ -1 +1 @@\n-old\n+new\n",
                encoding="utf-8",
            )
            config = self.config(root / "config.json", patch)
            job, job_id = self.envelope(root / "job.json", base, allowed=["policy/"], context=[] , tests=[])
            cp = self.run_worker(repo, job, config, root / "workspaces", root / "evidence")
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("path_not_allowed", cp.stderr)
            self.assertEqual(git(repo, "rev-parse", "HEAD"), base)
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            branches = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/fzh/")
            self.assertEqual(branches, "")
            self.assertTrue((root / "evidence" / job_id / "failure.json").exists())

    def test_gate_deny_prevents_worktree_or_branch_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, base = self.make_repo(root)
            patch = root / "patch.diff"
            patch.write_text("unused", encoding="utf-8")
            config = self.config(root / "config.json", patch)
            job, job_id = self.envelope(root / "job.json", base)
            cp = self.run_worker(repo, job, config, root / "workspaces", root / "evidence", gate=DENY_GATE)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("gate_not_allowed:deny", cp.stderr)
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/fzh/"), "")
            self.assertFalse((root / "evidence" / job_id).exists())

    def test_main_output_branch_is_rejected_before_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, base = self.make_repo(root)
            patch = root / "patch.diff"
            patch.write_text("unused", encoding="utf-8")
            config = self.config(root / "config.json", patch)
            job, _ = self.envelope(root / "job.json", base, output_branch="main")
            cp = self.run_worker(repo, job, config, root / "workspaces", root / "evidence")
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("production_branch_denied", cp.stderr)
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_small_tool_budget_fails_closed_and_removes_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, base = self.make_repo(root)
            patch = root / "patch.diff"
            patch.write_text(
                "diff --git a/app/value.txt b/app/value.txt\n--- a/app/value.txt\n+++ b/app/value.txt\n@@ -1 +1 @@\n-old\n+new\n",
                encoding="utf-8",
            )
            # Config validation allows >=1; a deliberately tiny budget must stop execution.
            config = self.config(root / "config.json", patch, max_tools=5)
            job, _ = self.envelope(root / "job.json", base)
            cp = self.run_worker(repo, job, config, root / "workspaces", root / "evidence")
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("tool_budget_exhausted", cp.stderr)
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/fzh/"), "")


if __name__ == "__main__":
    unittest.main()

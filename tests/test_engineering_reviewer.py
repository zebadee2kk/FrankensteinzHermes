import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
REVIEWER_PATH = ROOT / "scripts/engineering_reviewer/reviewer.py"
FAKE_REVIEWER = ROOT / "tests/helpers/fake_engineering_reviewer.py"
FAKE_LEDGER = ROOT / "tests/helpers/fake_engineering_review_ledger.py"

spec = importlib.util.spec_from_file_location("fzh_b033_reviewer", REVIEWER_PATH)
reviewer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(reviewer)


def git(repo: pathlib.Path, *args: str) -> str:
    cp = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return cp.stdout.strip()


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EngineeringReviewerTests(unittest.TestCase):
    def make_candidate(self, root: pathlib.Path):
        repo = root / "source"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "CI Owner"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repo, check=True)
        (repo / "app").mkdir()
        (repo / "app/value.txt").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        base = git(repo, "rev-parse", "HEAD")
        implementation_job_id = str(uuid.uuid4())
        branch = f"fzh/job-{implementation_job_id.replace('-', '')[:12]}"
        subprocess.run(["git", "switch", "-c", branch], cwd=repo, check=True, capture_output=True)
        (repo / "app/value.txt").write_text("new\n", encoding="utf-8")
        subprocess.run(["git", "add", "app/value.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "candidate"], cwd=repo, check=True, capture_output=True)
        head = git(repo, "rev-parse", "HEAD")
        subprocess.run(["git", "switch", "main"], cwd=repo, check=True, capture_output=True)
        diff = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff", "--no-renames", base, head, "--"],
            cwd=repo, check=True, capture_output=True,
        ).stdout
        evidence_dir = root / "implementation-evidence"
        evidence_dir.mkdir()
        patch = evidence_dir / "model.patch"
        patch.write_bytes(diff)
        implementation = {
            "schema_version": 1,
            "job_id": implementation_job_id,
            "attempt_id": 1,
            "branch": branch,
            "base_commit": base,
            "head_commit": head,
            "lease_token_sha256": "a" * 64,
            "patch_sha256": sha(patch),
            "changed_paths": ["app/value.txt"],
            "changed_bytes": len(diff),
            "context": [],
            "gate": {"request_sha256": "b" * 64, "policy_sha256": "c" * 64, "event_id": str(uuid.uuid4())},
            "model": {"id": "ci/fake-implementer", "calls": 1},
            "tools": {"commands": 8, "max_commands": 20},
            "tests": [],
            "elapsed_seconds": 1.0,
            "promotion_authorized": False,
            "next_stage": "B033 independent reviewer",
        }
        evidence_path = evidence_dir / "implementation-evidence.json"
        evidence_path.write_text(json.dumps(implementation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files = {
            "implementation-evidence.json": {"sha256": sha(evidence_path), "bytes": evidence_path.stat().st_size},
            "model.patch": {"sha256": sha(patch), "bytes": patch.stat().st_size},
        }
        manifest = evidence_dir / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "files": files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return repo, base, head, branch, implementation_job_id, evidence_dir, evidence_path, manifest

    def make_response(self, root: pathlib.Path, findings=None):
        response = root / "review-response.json"
        response.write_text(json.dumps({
            "schema_version": 1,
            "summary": "Independent review completed.",
            "findings": findings or [],
        }), encoding="utf-8")
        return response

    def make_config(self, root: pathlib.Path, response: pathlib.Path):
        call_log = root / "reviewer-calls.log"
        ledger_log = root / "review-ledger.log"
        config = {
            "schema_version": 1,
            "ledger": {
                "adapter_command": [sys.executable, str(FAKE_LEDGER), str(ledger_log)],
                "heartbeat_seconds": 900,
                "retry_delay_seconds": 1,
            },
            "model": {
                "adapter_command": [sys.executable, str(FAKE_REVIEWER), str(response), str(call_log)],
                "alias": "ci/fake-reviewer",
                "max_calls": 1,
                "max_prompt_bytes": 262144,
                "max_output_tokens": 512,
                "max_response_bytes": 65536,
                "timeout_seconds": 10,
                "max_findings": 20,
            },
            "budgets": {
                "max_wall_seconds": 60,
                "max_git_commands": 24,
                "max_diff_bytes": 65536,
                "max_context_bytes": 32768,
            },
            "verdict_policy": {"medium_findings_require_changes": 1},
            "forbidden_path_prefixes": [".github/", "policy/", "scripts/action_gate/", "bootstrap/"],
            "forbidden_paths": [
                "docs/AUTONOMY-CONSTITUTION.md", "docs/ARCHITECTURE.md",
                "docs/THREAT-MODEL.md", "config/autonomy-policy.example.yaml",
            ],
        }
        path = root / "review-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path, call_log, ledger_log

    def make_job(self, root, base, head, branch, implementation_job_id, evidence_path, manifest):
        review_job_id = str(uuid.uuid4())
        value = {
            "job_id": review_job_id,
            "lease_token": str(uuid.uuid4()),
            "job_kind": "engineering.review",
            "payload": {
                "implementation_job_id": implementation_job_id,
                "base_commit": base,
                "head_commit": head,
                "branch": branch,
                "implementation_evidence_sha256": sha(evidence_path),
                "implementation_manifest_sha256": sha(manifest),
                "objective": "Change app/value.txt from old to new.",
                "acceptance_criteria": ["app/value.txt contains new"],
                "allowed_paths": ["app/"],
                "context_paths": [],
            },
        }
        path = root / "review-job.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path, review_job_id

    def ledger_ops(self, path: pathlib.Path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def run_reviewer(self, repo, job, config, evidence_dir, output_root):
        return subprocess.run([
            sys.executable, str(REVIEWER_PATH),
            "--job-envelope", str(job), "--config", str(config),
            "--source-repo", str(repo), "--implementation-evidence-dir", str(evidence_dir),
            "--review-output-root", str(output_root),
        ], cwd=ROOT, text=True, capture_output=True, check=False)

    def setup_case(self, root, findings=None):
        candidate = self.make_candidate(root)
        repo, base, head, branch, impl_job, evidence_dir, evidence_path, manifest = candidate
        response = self.make_response(root, findings)
        config, call_log, ledger_log = self.make_config(root, response)
        job, review_job = self.make_job(root, base, head, branch, impl_job, evidence_path, manifest)
        return candidate, config, call_log, ledger_log, job, review_job

    def test_git_reader_denies_mutating_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, *_ = self.make_candidate(root)
            reader = reviewer.GitReader(repo, 5, reviewer.time.monotonic(), 60)
            for command in (["switch", "main"], ["branch", "x"], ["checkout", "main"], ["commit", "-m", "x"]):
                with self.subTest(command=command), self.assertRaises(reviewer.ReviewerError):
                    reader.call(command)

    def test_clean_review_approves_without_mutating_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            candidate, config, call_log, ledger_log, job, review_job = self.setup_case(root)
            repo, base, head, branch, _, evidence_dir, _, _ = candidate
            before_branch = git(repo, "rev-parse", branch)
            cp = self.run_reviewer(repo, job, config, evidence_dir, root / "reviews")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(git(repo, "rev-parse", "main"), base)
            self.assertEqual(git(repo, "rev-parse", branch), before_branch)
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual(before_branch, head)
            evidence = json.loads((root / "reviews" / review_job / "review-evidence.json").read_text(encoding="utf-8"))
            self.assertTrue(evidence["integrity_verified"])
            self.assertEqual(evidence["verdict"], "approve")
            self.assertEqual(evidence["next_stage"], "B034 adversarial QA")
            self.assertFalse(evidence["promotion_authorized"])
            self.assertEqual(len(call_log.read_text(encoding="utf-8").splitlines()), 1)
            ops = self.ledger_ops(ledger_log)
            self.assertEqual([x["operation"] for x in ops], ["start", "heartbeat", "complete"])
            self.assertEqual(ops[-1]["result"]["verdict"], "approve")

    def test_high_finding_requires_changes_regardless_of_model_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            finding = {
                "severity": "high", "category": "correctness", "path": "app/value.txt", "line": 1,
                "rationale": "The candidate is materially incorrect.", "remediation": "Correct the implementation and rerun tests.",
            }
            candidate, config, _, ledger_log, job, review_job = self.setup_case(root, [finding])
            repo, _, _, _, _, evidence_dir, _, _ = candidate
            cp = self.run_reviewer(repo, job, config, evidence_dir, root / "reviews")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = json.loads((root / "reviews" / review_job / "review-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["verdict"], "changes_required")
            self.assertIsNone(evidence["next_stage"])
            self.assertEqual(self.ledger_ops(ledger_log)[-1]["result"]["verdict"], "changes_required")

    def test_tampered_artifact_rejects_before_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            candidate, config, call_log, ledger_log, job, review_job = self.setup_case(root)
            repo, _, _, _, _, evidence_dir, _, _ = candidate
            (evidence_dir / "model.patch").write_text("tampered\n", encoding="utf-8")
            cp = self.run_reviewer(repo, job, config, evidence_dir, root / "reviews")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = json.loads((root / "reviews" / review_job / "review-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["verdict"], "reject")
            self.assertFalse(evidence["integrity_verified"])
            self.assertFalse(evidence["model_called"])
            self.assertFalse(call_log.exists())
            self.assertEqual([x["operation"] for x in self.ledger_ops(ledger_log)], ["start", "heartbeat", "complete"])

    def test_wrong_branch_binding_rejects_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            candidate, config, call_log, _, job, review_job = self.setup_case(root)
            repo, base, _, branch, _, evidence_dir, _, _ = candidate
            subprocess.run(["git", "branch", "-f", branch, base], cwd=repo, check=True)
            cp = self.run_reviewer(repo, job, config, evidence_dir, root / "reviews")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = json.loads((root / "reviews" / review_job / "review-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["verdict"], "reject")
            self.assertIn("candidate_branch_head_mismatch", evidence["integrity_error"])
            self.assertFalse(call_log.exists())

    def test_malformed_model_output_fails_closed_and_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            malformed = [{
                "severity": "banana", "category": "correctness", "path": "app/value.txt",
                "rationale": "bad", "remediation": "bad",
            }]
            candidate, config, call_log, ledger_log, job, review_job = self.setup_case(root, malformed)
            repo, _, _, _, _, evidence_dir, _, _ = candidate
            cp = self.run_reviewer(repo, job, config, evidence_dir, root / "reviews")
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("review_finding_classification_invalid", cp.stderr)
            self.assertTrue(call_log.exists())
            self.assertFalse((root / "reviews" / review_job).exists())
            ops = self.ledger_ops(ledger_log)
            self.assertEqual([x["operation"] for x in ops], ["start", "heartbeat", "fail"])
            self.assertTrue(ops[-1]["retryable"])


if __name__ == "__main__":
    unittest.main()

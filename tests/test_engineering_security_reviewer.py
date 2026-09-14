from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts/engineering_security_reviewer/worker.py"
FAKE_MODEL = ROOT / "tests/helpers/fake_engineering_security_model.py"
FAKE_LEDGER = ROOT / "tests/helpers/fake_engineering_security_ledger.py"


def run(cmd, cwd, *, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def git(cwd, *args):
    cp = run(["git", *args], cwd)
    if cp.returncode != 0:
        raise AssertionError(cp.stderr)
    return cp.stdout.strip()


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: pathlib.Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class EngineeringSecurityReviewerTests(unittest.TestCase):
    def make_candidate(self, root: pathlib.Path, *, content: str = "VALUE = 2\n", symlink: bool = False, path: str = "app/main.py"):
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.name", "Contract Test")
        git(repo, "config", "user.email", "contract@example.invalid")
        (repo / "app").mkdir()
        (repo / "app/main.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")
        branch = "fzh/job-111111111111"
        git(repo, "switch", "-q", "-c", branch)
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if symlink:
            if target.exists() or target.is_symlink():
                target.unlink()
            os.symlink("../app/main.py", target)
        else:
            target.write_text(content, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "candidate")
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "switch", "-q", "main")
        return repo, base, head, branch

    def make_qa_bundle(self, root: pathlib.Path, *, qa_status: str, base: str, head: str, branch: str):
        directory = root / "qa-evidence"
        directory.mkdir()
        qa_job_id = "22222222-2222-4222-8222-222222222222"
        log = directory / "profile-python-unit.log"
        log.write_text("PASS\n", encoding="utf-8")
        evidence = {
            "schema_version": 1,
            "job_id": qa_job_id,
            "review_job_id": "11111111-1111-4111-8111-111111111111",
            "base_commit": base,
            "head_commit": head,
            "branch": branch,
            "review_evidence_sha256": "a" * 64,
            "review_manifest_sha256": "b" * 64,
            "admission_verified": True,
            "qa_status": qa_status,
            "selected_profile_ids": ["python-unit"],
            "rejected_model_profile_ids": [],
            "profiles": [{
                "profile_id": "python-unit",
                "status": "pass" if qa_status == "qa_passed" else "fail",
                "returncode": 0 if qa_status == "qa_passed" else 1,
                "duration_seconds": 0.1,
                "log_sha256": sha256(log),
                "log_bytes": log.stat().st_size,
                "resource_policy": {},
                "hypothesis": None,
                "network": "unshared",
                "workspace_mode": "tmpfs-copy-from-readonly-candidate",
            }],
            "model": None,
            "git_commands": 1,
            "gate": {"request_sha256": "c" * 64, "policy_sha256": "d" * 64, "event_id": "55555555-5555-4555-8555-555555555555", "reason": "allow"},
            "promotion_authorized": False,
            "next_stage": "B035 security review" if qa_status == "qa_passed" else None,
        }
        evidence_path = directory / "qa-evidence.json"
        write_json(evidence_path, evidence)
        files = {}
        for path in sorted(directory.iterdir()):
            if path.is_file():
                files[path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
        manifest = directory / "manifest.json"
        write_json(manifest, {"schema_version": 1, "files": files})
        return directory, qa_job_id, sha256(evidence_path), sha256(manifest)

    def setup_case(self, root: pathlib.Path, *, content="VALUE = 2\n", symlink=False, candidate_path="app/main.py", qa_status="qa_passed", model_review=None, allowed_paths=None):
        repo, base, head, branch = self.make_candidate(root, content=content, symlink=symlink, path=candidate_path)
        qa_dir, qa_job_id, qa_evidence_sha, qa_manifest_sha = self.make_qa_bundle(root, qa_status=qa_status, base=base, head=head, branch=branch)
        response = root / "model-response.json"
        if model_review is None:
            model_review = {"schema_version": 1, "summary": "No additional security findings.", "findings": []}
        if isinstance(model_review, str):
            response.write_text(model_review, encoding="utf-8")
        else:
            write_json(response, model_review)
        ledger_log = root / "ledger.log"
        model_log = root / "model.log"
        config = {
            "schema_version": 1,
            "ledger": {"adapter_command": [sys.executable, str(FAKE_LEDGER), str(ledger_log)], "heartbeat_seconds": 300, "retry_delay_seconds": 10},
            "model": {
                "enabled": True,
                "adapter_command": [sys.executable, str(FAKE_MODEL), str(response), str(model_log)],
                "alias": "fake-security", "max_calls": 1, "max_prompt_bytes": 262144,
                "max_output_tokens": 2048, "max_response_bytes": 131072, "timeout_seconds": 30,
            },
            "budgets": {"max_wall_seconds": 120, "max_git_commands": 40, "max_diff_bytes": 524288, "max_evidence_bytes": 2097152, "max_scanned_files": 32, "max_scanned_bytes": 524288},
            "policy": {"medium_failure_threshold": 3, "protected_path_prefixes": [".github/workflows/", "db/migrations/", "deploy/"], "dependency_files": ["requirements.txt", "package.json", "package-lock.json"]},
        }
        config_path = root / "config.json"
        write_json(config_path, config)
        job = {
            "job_id": "33333333-3333-4333-8333-333333333333",
            "attempt_id": 1,
            "lease_token": "44444444-4444-4444-8444-444444444444",
            "job_kind": "engineering.security_review",
            "data_classification": "INTERNAL",
            "payload": {
                "qa_job_id": qa_job_id,
                "base_commit": base,
                "head_commit": head,
                "branch": branch,
                "qa_evidence_sha256": qa_evidence_sha,
                "qa_manifest_sha256": qa_manifest_sha,
                "objective": "Review candidate security before release.",
                "acceptance_criteria": ["No secrets", "No unsafe execution"],
                "allowed_paths": allowed_paths or ["app/"],
            },
        }
        job_path = root / "job.json"
        write_json(job_path, job)
        return repo, qa_dir, config_path, job_path, ledger_log, model_log, root / "security-output", job

    def run_worker(self, repo, qa_dir, config, job, output):
        return run([
            sys.executable, str(WORKER), "--job-envelope", str(job), "--config", str(config),
            "--source-repo", str(repo), "--qa-evidence-dir", str(qa_dir), "--security-output-root", str(output),
        ], ROOT)

    @staticmethod
    def read_ledger(path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def evidence(self, output, job):
        return json.loads((output / job["job_id"] / "security-evidence.json").read_text(encoding="utf-8"))

    def test_clean_candidate_passes_without_source_or_ref_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, ledger, model_log, output, job = self.setup_case(root)
            main_before = git(repo, "rev-parse", "main")
            candidate_before = git(repo, "rev-parse", job["payload"]["branch"])
            status_before = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
            cp = self.run_worker(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_passed")
            self.assertEqual(evidence["next_stage"], "B036 release")
            self.assertFalse(evidence["promotion_authorized"])
            self.assertFalse(evidence["candidate_executed"])
            self.assertEqual(git(repo, "rev-parse", "main"), main_before)
            self.assertEqual(git(repo, "rev-parse", job["payload"]["branch"]), candidate_before)
            self.assertEqual(git(repo, "status", "--porcelain=v1", "--untracked-files=all"), status_before)
            self.assertTrue(model_log.exists())
            self.assertEqual([x["operation"] for x in self.read_ledger(ledger)], ["start", "heartbeat", "complete"])

    def test_tampered_qa_artifact_rejects_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, ledger, model_log, output, job = self.setup_case(root)
            (qa / "profile-python-unit.log").write_text("TAMPERED\n", encoding="utf-8")
            cp = self.run_worker(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "reject")
            self.assertFalse(evidence["admission_verified"])
            self.assertFalse(model_log.exists())

    def test_non_qa_passed_bundle_rejects_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, _, model_log, output, job = self.setup_case(root, qa_status="qa_failed")
            cp = self.run_worker(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(self.evidence(output, job)["security_status"], "reject")
            self.assertFalse(model_log.exists())

    def test_private_key_material_deterministically_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            material = "-----BEGIN PRIVATE KEY-----\nnot-a-real-key-but-security-fixture\n-----END PRIVATE KEY-----\n"
            repo, qa, config, job_path, _, _, output, job = self.setup_case(root, content=material)
            cp = self.run_worker(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_failed")
            self.assertIn("critical", [x["severity"] for x in evidence["deterministic_findings"]])
            self.assertIsNone(evidence["next_stage"])

    def test_symlink_change_deterministically_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, _, _, output, job = self.setup_case(root, symlink=True, candidate_path="app/link")
            cp = self.run_worker(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_failed")
            self.assertIn("symlink", [x["category"] for x in evidence["deterministic_findings"]])

    def test_out_of_scope_change_is_high_and_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, _, _, output, job = self.setup_case(root, candidate_path="scripts/unsafe.py", allowed_paths=["app/"])
            cp = self.run_worker(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_failed")
            self.assertIn("path_scope", [x["category"] for x in evidence["deterministic_findings"]])

    def test_high_model_finding_blocks_even_without_deterministic_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            finding = {
                "severity": "high", "category": "authorization", "path": "app/main.py", "line": 1,
                "rationale": "Authorization check can be bypassed.", "evidence": "reviewed branch condition",
                "remediation": "Enforce authorization before the operation."
            }
            model = {"schema_version": 1, "summary": "Found a high severity issue.", "findings": [finding]}
            repo, qa, config, job_path, _, _, output, job = self.setup_case(root, model_review=model)
            cp = self.run_worker(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_failed")
            self.assertEqual(evidence["model_findings"][0]["source"], "model")

    def test_malformed_model_output_fails_closed_and_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, ledger, _, output, _ = self.setup_case(root, model_review="RAW:{not-json")
            cp = self.run_worker(repo, qa, config, job_path, output)
            self.assertNotEqual(cp.returncode, 0)
            operations = self.read_ledger(ledger)
            self.assertEqual([x["operation"] for x in operations], ["start", "heartbeat", "fail"])
            self.assertTrue(operations[-1]["retryable"])
            self.assertFalse(output.exists() and any(output.iterdir()))


if __name__ == "__main__":
    unittest.main()

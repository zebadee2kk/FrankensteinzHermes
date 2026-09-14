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


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def git(cwd, *args):
    cp = run(["git", *args], cwd)
    if cp.returncode:
        raise AssertionError(cp.stderr)
    return cp.stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class EngineeringSecurityReviewerTests(unittest.TestCase):
    def candidate(self, root, *, content="VALUE = 2\n", symlink=False, path="app/main.py"):
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

    def qa_bundle(self, root, *, status, base, head, branch):
        directory = root / "qa-evidence"
        directory.mkdir()
        qa_job_id = "22222222-2222-4222-8222-222222222222"
        log = directory / "profile-python-unit.log"
        log.write_text("PASS\n", encoding="utf-8")
        evidence = {
            "schema_version": 1, "job_id": qa_job_id,
            "review_job_id": "11111111-1111-4111-8111-111111111111",
            "base_commit": base, "head_commit": head, "branch": branch,
            "review_evidence_sha256": "a" * 64, "review_manifest_sha256": "b" * 64,
            "admission_verified": True, "qa_status": status,
            "selected_profile_ids": ["python-unit"], "rejected_model_profile_ids": [],
            "profiles": [{
                "profile_id": "python-unit", "status": "pass" if status == "qa_passed" else "fail",
                "returncode": 0 if status == "qa_passed" else 1, "duration_seconds": 0.1,
                "log_sha256": digest(log), "log_bytes": log.stat().st_size,
                "resource_policy": {}, "hypothesis": None, "network": "unshared",
                "workspace_mode": "tmpfs-copy-from-readonly-candidate",
            }],
            "model": None, "git_commands": 1,
            "gate": {"request_sha256": "c" * 64, "policy_sha256": "d" * 64,
                     "event_id": "55555555-5555-4555-8555-555555555555", "reason": "allow"},
            "promotion_authorized": False,
            "next_stage": "B035 security review" if status == "qa_passed" else None,
        }
        evidence_path = directory / "qa-evidence.json"
        write_json(evidence_path, evidence)
        files = {p.name: {"sha256": digest(p), "bytes": p.stat().st_size} for p in sorted(directory.iterdir()) if p.is_file()}
        manifest = directory / "manifest.json"
        write_json(manifest, {"schema_version": 1, "files": files})
        return directory, qa_job_id, digest(evidence_path), digest(manifest)

    def setup_case(self, root, *, content="VALUE = 2\n", symlink=False, path="app/main.py",
                   qa_status="qa_passed", model_review=None, allowed_paths=None):
        repo, base, head, branch = self.candidate(root, content=content, symlink=symlink, path=path)
        qa, qa_job_id, qa_sha, manifest_sha = self.qa_bundle(root, status=qa_status, base=base, head=head, branch=branch)
        response = root / "model-response.json"
        if model_review is None:
            model_review = {"schema_version": 1, "summary": "No additional security findings.", "findings": []}
        if isinstance(model_review, str):
            response.write_text(model_review, encoding="utf-8")
        else:
            write_json(response, model_review)
        ledger, model_log = root / "ledger.log", root / "model.log"
        config = {
            "schema_version": 1,
            "ledger": {"adapter_command": [sys.executable, str(FAKE_LEDGER), str(ledger)], "heartbeat_seconds": 300, "retry_delay_seconds": 10},
            "model": {"enabled": True, "adapter_command": [sys.executable, str(FAKE_MODEL), str(response), str(model_log)],
                      "alias": "fake-security", "max_calls": 1, "max_prompt_bytes": 262144,
                      "max_output_tokens": 2048, "max_response_bytes": 131072, "timeout_seconds": 30},
            "budgets": {"max_wall_seconds": 120, "max_git_commands": 40, "max_diff_bytes": 524288,
                        "max_evidence_bytes": 2097152, "max_scanned_files": 32, "max_scanned_bytes": 524288},
            "policy": {"medium_failure_threshold": 3,
                       "protected_path_prefixes": [".github/workflows/", "db/migrations/", "deploy/"],
                       "dependency_files": ["requirements.txt", "package.json", "package-lock.json"]},
        }
        config_path = root / "config.json"
        write_json(config_path, config)
        job = {
            "job_id": "33333333-3333-4333-8333-333333333333", "attempt_id": 1,
            "lease_token": "44444444-4444-4444-8444-444444444444", "job_kind": "engineering.security_review",
            "data_classification": "INTERNAL",
            "payload": {"qa_job_id": qa_job_id, "base_commit": base, "head_commit": head, "branch": branch,
                        "qa_evidence_sha256": qa_sha, "qa_manifest_sha256": manifest_sha,
                        "objective": "Review candidate security before release.",
                        "acceptance_criteria": ["No secrets", "No unsafe execution"],
                        "allowed_paths": allowed_paths or ["app/"]},
        }
        job_path = root / "job.json"
        write_json(job_path, job)
        return repo, qa, config_path, job_path, ledger, model_log, root / "security-output", job

    def execute(self, repo, qa, config, job, output):
        return run([sys.executable, str(WORKER), "--job-envelope", str(job), "--config", str(config),
                    "--source-repo", str(repo), "--qa-evidence-dir", str(qa),
                    "--security-output-root", str(output)], ROOT)

    @staticmethod
    def ledger(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.exists() else []

    @staticmethod
    def evidence(output, job):
        return json.loads((output / job["job_id"] / "security-evidence.json").read_text(encoding="utf-8"))

    def test_clean_candidate_passes_without_source_or_ref_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, ledger, model_log, output, job = self.setup_case(root)
            before = (git(repo, "rev-parse", "main"), git(repo, "rev-parse", job["payload"]["branch"]),
                      git(repo, "status", "--porcelain=v1", "--untracked-files=all"))
            cp = self.execute(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_passed")
            self.assertEqual(evidence["next_stage"], "B036 release")
            self.assertFalse(evidence["promotion_authorized"])
            self.assertFalse(evidence["candidate_executed"])
            self.assertEqual((git(repo, "rev-parse", "main"), git(repo, "rev-parse", job["payload"]["branch"]),
                              git(repo, "status", "--porcelain=v1", "--untracked-files=all")), before)
            self.assertTrue(model_log.exists())
            self.assertEqual([x["operation"] for x in self.ledger(ledger)], ["start", "heartbeat", "complete"])

    def test_tampered_qa_artifact_rejects_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, _, model_log, output, job = self.setup_case(root)
            (qa / "profile-python-unit.log").write_text("TAMPERED\n", encoding="utf-8")
            cp = self.execute(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(self.evidence(output, job)["security_status"], "reject")
            self.assertFalse(model_log.exists())

    def test_non_qa_passed_bundle_rejects_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, _, model_log, output, job = self.setup_case(root, qa_status="qa_failed")
            cp = self.execute(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(self.evidence(output, job)["security_status"], "reject")
            self.assertFalse(model_log.exists())

    def test_private_key_material_deterministically_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            fence = "-" * 5
            # Assemble the exact PEM marker only in the temporary candidate so
            # the repository's own private-key guard remains effective.
            material = fence + "BEGIN " + "PRIVATE KEY" + fence + "\nfixture-only\n" + fence + "END " + "PRIVATE KEY" + fence + "\n"
            repo, qa, config, job_path, _, _, output, job = self.setup_case(root, content=material)
            cp = self.execute(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_failed")
            self.assertIn("critical", [x["severity"] for x in evidence["deterministic_findings"]])

    def test_symlink_change_deterministically_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, _, _, output, job = self.setup_case(root, symlink=True, path="app/link")
            cp = self.execute(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_failed")
            self.assertIn("symlink", [x["category"] for x in evidence["deterministic_findings"]])

    def test_out_of_scope_change_is_high_and_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, _, _, output, job = self.setup_case(root, path="scripts/unsafe.py", allowed_paths=["app/"])
            cp = self.execute(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = self.evidence(output, job)
            self.assertEqual(evidence["security_status"], "security_failed")
            self.assertIn("path_scope", [x["category"] for x in evidence["deterministic_findings"]])

    def test_high_model_finding_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            finding = {"severity": "high", "category": "authorization", "path": "app/main.py", "line": 1,
                       "rationale": "Authorization check can be bypassed.", "evidence": "branch condition",
                       "remediation": "Enforce authorization before the operation."}
            review = {"schema_version": 1, "summary": "High severity issue.", "findings": [finding]}
            repo, qa, config, job_path, _, _, output, job = self.setup_case(root, model_review=review)
            cp = self.execute(repo, qa, config, job_path, output)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(self.evidence(output, job)["security_status"], "security_failed")

    def test_malformed_model_output_fails_closed_and_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo, qa, config, job_path, ledger, _, output, _ = self.setup_case(root, model_review="RAW:{not-json")
            cp = self.execute(repo, qa, config, job_path, output)
            self.assertNotEqual(cp.returncode, 0)
            records = self.ledger(ledger)
            self.assertEqual([x["operation"] for x in records], ["start", "heartbeat", "fail"])
            self.assertTrue(records[-1]["retryable"])
            self.assertFalse(output.exists() and any(output.iterdir()))


if __name__ == "__main__":
    unittest.main()

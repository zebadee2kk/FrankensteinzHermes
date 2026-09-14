import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts/engineering_qa/worker.py"
FAKE_GATE = ROOT / "tests/helpers/fake_action_gate.py"
DENY_GATE = ROOT / "tests/helpers/fake_action_gate_deny.py"
FAKE_LEDGER = ROOT / "tests/helpers/fake_engineering_qa_ledger.py"
FAKE_SANDBOX = ROOT / "tests/helpers/fake_engineering_qa_sandbox.py"
FAKE_MODEL = ROOT / "tests/helpers/fake_engineering_qa_model.py"


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EngineeringQATests(unittest.TestCase):
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
        implementation_job = str(uuid.uuid4())
        branch = f"fzh/job-{implementation_job.replace('-', '')[:12]}"
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
        return repo, base, head, branch, implementation_job, diff

    def make_review_bundle(self, root, candidate, *, verdict="approve", integrity=True, next_stage="B034 adversarial QA"):
        repo, base, head, branch, implementation_job, diff = candidate
        review_job = str(uuid.uuid4())
        directory = root / "review-evidence"
        directory.mkdir()
        diff_path = directory / "reconstructed.diff"
        diff_path.write_bytes(diff)
        model_path = directory / "review-model.json"
        model_path.write_text(json.dumps({"schema_version": 1, "summary": "clean", "findings": []}, indent=2) + "\n", encoding="utf-8")
        evidence = {
            "schema_version": 1,
            "job_id": review_job,
            "implementation_job_id": implementation_job,
            "base_commit": base,
            "head_commit": head,
            "branch": branch,
            "integrity_verified": integrity,
            "reconstructed_diff_sha256": hashlib.sha256(diff).hexdigest(),
            "changed_paths": ["app/value.txt"],
            "changed_bytes": len(diff),
            "implementation_evidence_sha256": "a" * 64,
            "implementation_manifest_sha256": "b" * 64,
            "review_summary": "Independent review completed.",
            "findings": [],
            "verdict": verdict,
            "model": {"model_id": "ci/fake-reviewer"},
            "git_commands": 6,
            "promotion_authorized": False,
            "next_stage": next_stage,
            "elapsed_seconds": 0.5,
        }
        evidence_path = directory / "review-evidence.json"
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files = {
            p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
            for p in sorted(directory.iterdir()) if p.is_file()
        }
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "files": files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return directory, evidence_path, manifest, review_job

    def make_model_response(self, root, profiles=None, *, malformed=False):
        path = root / "qa-model-response.json"
        if malformed:
            value = {"schema_version": 1, "summary": "malformed", "profiles": [{"id": "python-unit", "hypothesis": "x", "command": "rm -rf /"}]}
        else:
            value = {"schema_version": 1, "summary": "adversarial plan", "profiles": profiles or []}
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def make_config(self, root, model_response, scenario, *, baseline=None, allowed=None):
        ledger_log = root / "qa-ledger.log"
        sandbox_log = root / "qa-sandbox.log"
        model_log = root / "qa-model.log"
        scenario_path = root / "qa-scenario.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        value = {
            "schema_version": 1,
            "ledger": {"adapter_command": [sys.executable, str(FAKE_LEDGER), str(ledger_log)], "heartbeat_seconds": 900, "retry_delay_seconds": 1},
            "model": {
                "enabled": True,
                "adapter_command": [sys.executable, str(FAKE_MODEL), str(model_response), str(model_log)],
                "alias": "ci/fake-qa", "max_calls": 1, "max_prompt_bytes": 262144,
                "max_output_tokens": 512, "max_response_bytes": 65536, "timeout_seconds": 10,
                "max_profile_suggestions": 8,
            },
            "sandbox": {
                "adapter_command": [sys.executable, str(FAKE_SANDBOX), str(scenario_path), str(sandbox_log)],
                "allowed_profile_ids": allowed or ["python-unit", "repo-governance"],
                "baseline_profile_ids": baseline or ["python-unit"],
                "max_profiles": 8,
                "max_adapter_response_bytes": 262144,
            },
            "budgets": {"max_wall_seconds": 60, "max_git_commands": 20, "max_review_diff_bytes": 65536, "max_evidence_bytes": 262144},
        }
        config = root / "qa-config.json"
        config.write_text(json.dumps(value), encoding="utf-8")
        return config, ledger_log, sandbox_log, model_log

    def make_job(self, root, candidate, review_bundle, *, required=None):
        repo, base, head, branch, _, _ = candidate
        review_dir, evidence_path, manifest, review_job = review_bundle
        job_id = str(uuid.uuid4())
        value = {
            "job_id": job_id,
            "lease_token": str(uuid.uuid4()),
            "job_kind": "engineering.qa",
            "data_classification": "INTERNAL",
            "payload": {
                "review_job_id": review_job,
                "base_commit": base,
                "head_commit": head,
                "branch": branch,
                "review_evidence_sha256": sha(evidence_path),
                "review_manifest_sha256": sha(manifest),
                "objective": "Change app/value.txt from old to new.",
                "required_profile_ids": required or [],
            },
        }
        path = root / "qa-job.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path, job_id

    def run_worker(self, repo, job, config, review_dir, root, gate=FAKE_GATE):
        return subprocess.run([
            sys.executable, str(WORKER), "--job-envelope", str(job), "--config", str(config),
            "--source-repo", str(repo), "--review-evidence-dir", str(review_dir),
            "--workspace-root", str(root / "qa-workspaces"), "--qa-output-root", str(root / "qa-output"),
            "--gate-command", sys.executable, str(gate),
        ], cwd=ROOT, text=True, capture_output=True, check=False)

    def read_log(self, path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def setup_case(self, root, *, scenario=None, profiles=None, verdict="approve", integrity=True, malformed_model=False, required=None):
        candidate = self.make_candidate(root)
        review = self.make_review_bundle(root, candidate, verdict=verdict, integrity=integrity)
        model_response = self.make_model_response(root, profiles, malformed=malformed_model)
        config, ledger_log, sandbox_log, model_log = self.make_config(root, model_response, scenario or {})
        job, job_id = self.make_job(root, candidate, review, required=required)
        return candidate, review, config, ledger_log, sandbox_log, model_log, job, job_id

    def test_all_profiles_pass_hands_off_to_b035_without_source_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            profiles = [{"id": "repo-governance", "hypothesis": "Exercise repository invariants."}]
            candidate, review, config, ledger_log, sandbox_log, model_log, job, job_id = self.setup_case(root, profiles=profiles)
            repo, base, head, branch, _, _ = candidate
            before_branch = git(repo, "rev-parse", branch)
            cp = self.run_worker(repo, job, config, review[0], root)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(git(repo, "rev-parse", "main"), base)
            self.assertEqual(git(repo, "rev-parse", branch), before_branch)
            self.assertEqual(before_branch, head)
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            evidence = json.loads((root / "qa-output" / job_id / "qa-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["qa_status"], "qa_passed")
            self.assertEqual(evidence["next_stage"], "B035 security review")
            self.assertFalse(evidence["promotion_authorized"])
            self.assertEqual(evidence["selected_profile_ids"], ["python-unit", "repo-governance"])
            requests = self.read_log(sandbox_log)
            self.assertEqual([x["profile_id"] for x in requests], ["python-unit", "repo-governance"])
            self.assertTrue(all(set(x) == {"operation", "job_id", "workspace", "profile_id"} for x in requests))
            self.assertEqual(len(self.read_log(model_log)), 1)
            self.assertFalse((root / "qa-workspaces" / job_id).exists())
            ops = self.read_log(ledger_log)
            self.assertEqual([x["operation"] for x in ops], ["record_gate", "start", "heartbeat", "complete"])
            self.assertEqual(ops[-1]["result"]["qa_status"], "qa_passed")

    def test_required_profile_failure_is_terminal_qa_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            case = self.setup_case(root, scenario={"repo-governance": "fail"}, required=["repo-governance"])
            candidate, review, config, ledger_log, sandbox_log, _, job, job_id = case
            cp = self.run_worker(candidate[0], job, config, review[0], root)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = json.loads((root / "qa-output" / job_id / "qa-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["qa_status"], "qa_failed")
            self.assertIsNone(evidence["next_stage"])
            self.assertIn("repo-governance", [x["profile_id"] for x in self.read_log(sandbox_log)])
            self.assertEqual(self.read_log(ledger_log)[-1]["operation"], "complete")
            self.assertEqual(self.read_log(ledger_log)[-1]["result"]["qa_status"], "qa_failed")

    def test_tampered_review_artifact_rejects_before_model_or_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            candidate, review, config, ledger_log, sandbox_log, model_log, job, job_id = self.setup_case(root)
            (review[0] / "reconstructed.diff").write_text("tampered\n", encoding="utf-8")
            cp = self.run_worker(candidate[0], job, config, review[0], root)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = json.loads((root / "qa-output" / job_id / "qa-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["qa_status"], "reject")
            self.assertFalse(evidence["candidate_executed"])
            self.assertFalse(model_log.exists())
            self.assertFalse(sandbox_log.exists())
            self.assertEqual([x["operation"] for x in self.read_log(ledger_log)], ["record_gate", "start", "heartbeat", "complete"])

    def test_nonapprove_review_rejects_before_model_or_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            candidate = self.make_candidate(root)
            review = self.make_review_bundle(root, candidate, verdict="changes_required", next_stage=None)
            model = self.make_model_response(root, [])
            config, ledger_log, sandbox_log, model_log = self.make_config(root, model, {})
            job, job_id = self.make_job(root, candidate, review)
            cp = self.run_worker(candidate[0], job, config, review[0], root)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = json.loads((root / "qa-output" / job_id / "qa-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["qa_status"], "reject")
            self.assertIn("review_not_approved", evidence["admission_error"])
            self.assertFalse(model_log.exists())
            self.assertFalse(sandbox_log.exists())

    def test_unknown_model_profile_is_recorded_but_never_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            profiles = [
                {"id": "evil-shell", "hypothesis": "Try an unapproved arbitrary command profile."},
                {"id": "repo-governance", "hypothesis": "Use an approved profile."},
            ]
            candidate, review, config, _, sandbox_log, _, job, job_id = self.setup_case(root, profiles=profiles)
            cp = self.run_worker(candidate[0], job, config, review[0], root)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            evidence = json.loads((root / "qa-output" / job_id / "qa-evidence.json").read_text(encoding="utf-8"))
            self.assertIn("evil-shell", evidence["rejected_model_profile_ids"])
            executed = [x["profile_id"] for x in self.read_log(sandbox_log)]
            self.assertNotIn("evil-shell", executed)
            self.assertEqual(executed, ["python-unit", "repo-governance"])

    def test_model_cannot_smuggle_command_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            candidate, review, config, ledger_log, sandbox_log, model_log, job, job_id = self.setup_case(root, malformed_model=True)
            cp = self.run_worker(candidate[0], job, config, review[0], root)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("qa_model_profile_shape_invalid", cp.stderr)
            self.assertTrue(model_log.exists())
            self.assertFalse(sandbox_log.exists())
            self.assertFalse((root / "qa-output" / job_id).exists())
            ops = self.read_log(ledger_log)
            self.assertEqual([x["operation"] for x in ops], ["record_gate", "start", "heartbeat", "fail"])
            self.assertTrue(ops[-1]["retryable"])

    def test_sandbox_infrastructure_failure_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            candidate, review, config, ledger_log, sandbox_log, _, job, job_id = self.setup_case(root, scenario={"python-unit": "infra_error"})
            cp = self.run_worker(candidate[0], job, config, review[0], root)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("qa_sandbox_infrastructure_failure", cp.stderr)
            ops = self.read_log(ledger_log)
            self.assertEqual([x["operation"] for x in ops], ["record_gate", "start", "heartbeat", "fail"])
            self.assertTrue(ops[-1]["retryable"])
            self.assertFalse((root / "qa-output" / job_id).exists())

    def test_gate_deny_prevents_review_model_and_candidate_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            candidate, review, config, ledger_log, sandbox_log, model_log, job, _ = self.setup_case(root)
            cp = self.run_worker(candidate[0], job, config, review[0], root, gate=DENY_GATE)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("gate_not_allowed:deny", cp.stderr)
            self.assertFalse(model_log.exists())
            self.assertFalse(sandbox_log.exists())
            ops = self.read_log(ledger_log)
            self.assertEqual([x["operation"] for x in ops], ["record_gate"])
            self.assertEqual(ops[0]["decision"], "deny")


if __name__ == "__main__":
    unittest.main()

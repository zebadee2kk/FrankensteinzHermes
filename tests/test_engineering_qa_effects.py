import json
import pathlib
import re
import tempfile
import unittest

from tests.test_engineering_qa import EngineeringQATests

HEX64 = re.compile(r"^[0-9a-f]{64}$")


class EngineeringQAEffectTests(unittest.TestCase):
    def harness(self):
        return EngineeringQATests(methodName="test_all_profiles_pass_hands_off_to_b035_without_source_mutation")

    @staticmethod
    def read_log(path: pathlib.Path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_each_executed_profile_has_started_and_committed_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            h = self.harness()
            profiles = [{"id": "repo-governance", "hypothesis": "Exercise repository invariants."}]
            candidate, review, config, ledger_log, sandbox_log, _, job, _ = h.setup_case(root, profiles=profiles)
            cp = h.run_worker(candidate[0], job, config, review[0], root)
            self.assertEqual(cp.returncode, 0, cp.stderr)

            executed = [x["profile_id"] for x in self.read_log(sandbox_log)]
            effects = self.read_log(ledger_log.with_name(ledger_log.stem + ".effects.log"))
            self.assertEqual(len(effects), 2 * len(executed))
            for index, profile_id in enumerate(executed):
                begin, commit = effects[index * 2 : index * 2 + 2]
                self.assertEqual(begin["operation"], "begin_effect")
                self.assertEqual(begin["profile_id"], profile_id)
                self.assertEqual(commit["operation"], "commit_effect")
                self.assertEqual(commit["profile_id"], profile_id)
                self.assertRegex(commit["result_sha256"], HEX64)

    def test_sandbox_infrastructure_failure_leaves_effect_uncommitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            h = self.harness()
            candidate, review, config, ledger_log, _, _, job, _ = h.setup_case(
                root, scenario={"python-unit": "infra_error"}
            )
            cp = h.run_worker(candidate[0], job, config, review[0], root)
            self.assertNotEqual(cp.returncode, 0)

            effects = self.read_log(ledger_log.with_name(ledger_log.stem + ".effects.log"))
            self.assertEqual(len(effects), 1)
            self.assertEqual(effects[0]["operation"], "begin_effect")
            self.assertEqual(effects[0]["profile_id"], "python-unit")


if __name__ == "__main__":
    unittest.main()

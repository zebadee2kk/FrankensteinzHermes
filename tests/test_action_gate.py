import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/action_gate/action_gate.py"
POLICY_PATH = ROOT / "policy/action-gate.json"

spec = importlib.util.spec_from_file_location("fzh_action_gate", MODULE_PATH)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)


class ActionGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = gate.load_policy(POLICY_PATH)

    def request(self, **changes):
        value = {
            "request_id": "test-1",
            "actor": "test-worker",
            "action_type": "read_status",
            "risk_level": "L0",
            "environment": "local",
            "data_classification": "PUBLIC",
            "target": "fixture-target",
            "side_effecting": False,
            "reversible": True,
            "external_side_effect": False,
            "uses_llm": False,
            "metadata": {},
        }
        value.update(changes)
        return value

    def decision(self, **changes):
        return gate.decide(self.request(**changes), self.policy)

    def test_l0_read_only_allows(self):
        self.assertEqual(self.decision()["decision"], "allow")

    def test_l1_bounded_reversible_allows(self):
        result = self.decision(
            action_type="workspace_branch_write",
            risk_level="L1",
            environment="isolated",
            side_effecting=True,
            reversible=True,
        )
        self.assertEqual(result["decision"], "allow")

    def test_l2_and_l3_require_approval(self):
        self.assertEqual(self.decision(risk_level="L2")["decision"], "approval_required")
        self.assertEqual(self.decision(risk_level="L3")["decision"], "approval_required")

    def test_hard_deny_beats_low_risk_claim(self):
        result = self.decision(action_type="credential_export", risk_level="L0")
        self.assertEqual(result["decision"], "deny")
        self.assertTrue(result["reason"].startswith("hard_deny_action:"))

    def test_policy_self_modification_is_denied(self):
        for action in (
            "action_gate_policy_modify",
            "action_gate_executable_modify",
            "action_gate_kill_switch_modify",
        ):
            with self.subTest(action=action):
                self.assertEqual(self.decision(action_type=action)["decision"], "deny")

    def test_explicit_approval_action_beats_l0_default(self):
        result = self.decision(action_type="production_deploy", risk_level="L0")
        self.assertEqual(result["decision"], "approval_required")

    def test_l0_side_effect_mismatch_escalates(self):
        result = self.decision(side_effecting=True)
        self.assertEqual(result["decision"], "approval_required")
        self.assertEqual(result["reason"], "l0_side_effect_mismatch")

    def test_l1_constraints_escalate(self):
        cases = (
            {"reversible": False},
            {"environment": "production"},
            {"external_side_effect": True},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                result = self.decision(
                    action_type="workspace_branch_write",
                    risk_level="L1",
                    side_effecting=True,
                    **changes,
                )
                self.assertEqual(result["decision"], "approval_required")

    def test_secret_to_llm_and_secret_external_side_effect_are_denied(self):
        self.assertEqual(
            self.decision(data_classification="SECRET", uses_llm=True)["decision"],
            "deny",
        )
        self.assertEqual(
            self.decision(data_classification="SECRET", external_side_effect=True)["decision"],
            "deny",
        )

    def test_kill_switch_denies_even_l0(self):
        result = gate.decide(self.request(), self.policy, kill_switch_active=True)
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(result["reason"], "kill_switch_active")
        self.assertTrue(result["kill_switch_active"])

    def test_invalid_request_fails_closed(self):
        result = gate.decide({"request_id": "bad"}, self.policy)
        self.assertEqual(result["decision"], "deny")
        self.assertTrue(result["reason"].startswith("fail_closed:"))

    def test_decision_is_deterministic(self):
        request = self.request(
            action_type="workspace_branch_write",
            risk_level="L1",
            environment="isolated",
            side_effecting=True,
        )
        first = gate.decide(request, self.policy)
        second = gate.decide(json.loads(json.dumps(request)), self.policy)
        self.assertEqual(first, second)
        self.assertRegex(first["request_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(first["policy_sha256"], r"^[0-9a-f]{64}$")

    def test_invalid_policy_fails_to_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.json"
            path.write_text('{"schema_version":1,"risk_defaults":{}}', encoding="utf-8")
            with self.assertRaises(gate.GateError):
                gate.load_policy(path)


if __name__ == "__main__":
    unittest.main()

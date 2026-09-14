#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
policy_path = ROOT / "policy/action-gate.json"
engine = (ROOT / "scripts/action_gate/action_gate.py").read_text(encoding="utf-8")
server = (ROOT / "scripts/action_gate/server.py").read_text(encoding="utf-8")
client = (ROOT / "scripts/action_gate/client.py").read_text(encoding="utf-8")
installer = (ROOT / "scripts/action_gate/install.sh").read_text(encoding="utf-8")
verifier = (ROOT / "scripts/action_gate/verify.sh").read_text(encoding="utf-8")
unit = (ROOT / "deploy/systemd/fzh-action-gate.service").read_text(encoding="utf-8")
repo_policy = (ROOT / "policy/repository-protection.yaml").read_text(encoding="utf-8")
policy = json.loads(policy_path.read_text(encoding="utf-8"))

assert policy["schema_version"] == 1
assert policy["risk_defaults"] == {
    "L0": "allow",
    "L1": "allow",
    "L2": "approval_required",
    "L3": "approval_required",
}
for action in (
    "action_gate_policy_modify",
    "action_gate_executable_modify",
    "action_gate_kill_switch_modify",
    "credential_export",
    "secret_export",
    "secret_to_llm",
):
    assert action in policy["hard_deny_actions"], action

assert "policy/**" in repo_policy
assert "http" not in engine.lower()
assert "openai" not in engine.lower()
assert "anthropic" not in engine.lower()

for fragment in (
    "POLICY_PATH = Path('/etc/frankensteinzhermes/action-gate.json')",
    "KILL_SWITCH_PATH = Path('/etc/frankensteinzhermes/action-gate.kill')",
    "SOCKET_PATH = Path('/run/frankensteinzhermes-gate/action-gate.sock')",
    "AUDIT_PATH = Path('/var/log/frankensteinzhermes-gate/action-gate.jsonl')",
):
    assert fragment in server, fragment

# Runtime callers cannot select an alternate policy or socket via CLI/env.
assert "argparse" not in server
assert "FZH_" not in server
assert "argparse" not in client
assert "FZH_" not in client

for fragment in (
    "User=fzh-gate",
    "Group=fzh-gate-clients",
    "NoNewPrivileges=true",
    "ProtectSystem=strict",
    "ProtectHome=true",
    "RestrictAddressFamilies=AF_UNIX",
    "CapabilityBoundingSet=",
):
    assert fragment in unit, fragment

for fragment in (
    'HERMES_USER="${FZH_HERMES_USER:-fzh-hermes}"',
    'GATE_USER="fzh-gate"',
    'CLIENT_GROUP="fzh-gate-clients"',
    'install -o root -g root -m 0644',
    '/etc/frankensteinzhermes',
    '/usr/local/libexec/frankensteinzhermes',
    'systemctl enable --now fzh-action-gate.service',
):
    assert fragment in installer, fragment

assert "sudo|docker" in verifier
assert "--exercise-kill-switch" in verifier
assert "kill_switch_active" in verifier

print("action gate authority-boundary validation passed")

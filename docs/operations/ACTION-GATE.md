# B030 — Action Gate v1

The Action Gate is the deterministic authority boundary for autonomous side effects. It is deliberately **not an LLM** and does not call any model/provider.

## Runtime boundary

Production deployment uses:

- daemon user: `fzh-gate`;
- client socket group: `fzh-gate-clients`;
- Hermes runtime user: `fzh-hermes`, added only to the client group;
- policy: `/etc/frankensteinzhermes/action-gate.json`, root-owned `0644`;
- kill switch: `/etc/frankensteinzhermes/action-gate.kill`, root-controlled;
- daemon code: `/usr/local/libexec/frankensteinzhermes/`, root-owned;
- client: `/usr/local/bin/fzh-action-gate`, root-owned;
- Unix socket: `/run/frankensteinzhermes-gate/action-gate.sock`, group-connectable only;
- audit log: `/var/log/frankensteinzhermes-gate/action-gate.jsonl`, written by `fzh-gate`;
- service log: systemd journal for `fzh-action-gate.service`.

`fzh-hermes` remains outside the `sudo` and `docker` groups. It can ask the gate for a decision but cannot replace the installed policy, executable, kill switch or audit log through normal runtime credentials.

The gate intentionally has no database/provider credentials. B031 can reference/ingest gate audit hashes into the job ledger without broadening the safety daemon's authority.

## Risk defaults

| Risk | Default | Intended scope |
|---|---|---|
| L0 | allow | read-only observation, local reasoning, no side effect |
| L1 | allow | bounded reversible write in local/isolated/staging scope |
| L2 | approval required | material reconfiguration/external or production-capable change |
| L3 | approval required | high-impact/destructive/financial/security-sensitive action |

These are defaults, not bypasses. Explicit hard-deny and approval-required action types take precedence, and under-classified L0/L1 requests are escalated.

Hard-denied normal-runtime actions include gate/policy/kill-switch modification, credential export, secret export and SECRET-to-LLM transfer.

## Request contract

Workers send one JSON object to `/usr/local/bin/fzh-action-gate` on stdin:

```json
{
  "request_id": "job-123:step-4",
  "actor": "engineering-worker",
  "action_type": "workspace_branch_write",
  "risk_level": "L1",
  "environment": "isolated",
  "data_classification": "INTERNAL",
  "target": "fork/repo:feature-branch",
  "side_effecting": true,
  "reversible": true,
  "external_side_effect": false,
  "uses_llm": false,
  "metadata": {}
}
```

The target itself is not copied into the audit response/log; the gate records a target SHA-256 plus the canonical whole-request SHA-256.

## Response and exit codes

The client prints one JSON decision. Exit status is:

- `0` — `allow`;
- `20` — `approval_required`;
- `30` — `deny`, including unavailable/broken gate.

Callers must treat any unknown/non-zero status other than the explicitly handled approval state as denial. They must never call a tool directly because the gate is unavailable.

Every daemon decision records request ID, actor, action type, risk, environment, data classification, target hash, decision, reason, request hash, policy digest and kill-switch state. Raw secrets/targets are not copied into the gate audit record.

## Kill switch

Enable immediately:

```bash
sudo touch /etc/frankensteinzhermes/action-gate.kill
sudo chown root:root /etc/frankensteinzhermes/action-gate.kill
sudo chmod 0644 /etc/frankensteinzhermes/action-gate.kill
```

No service restart is required. The daemon checks existence for each request and returns `deny` with `reason=kill_switch_active`.

Disable only after the incident/maintenance condition is resolved:

```bash
sudo rm /etc/frankensteinzhermes/action-gate.kill
```

The installer never removes an existing kill switch.

## Installation

After the B020 Hermes account exists:

```bash
sudo bash scripts/action_gate/install.sh
sudo bash scripts/action_gate/verify.sh --exercise-kill-switch
```

The installer creates `fzh-gate` and `fzh-gate-clients`, installs authority files as root, adds `fzh-hermes` to the socket client group, and enables the hardened systemd service. Restart any long-lived Hermes process after first installation so its supplementary group membership is refreshed.

The verifier checks file ownership/modes, service/socket state, absence of `sudo`/`docker` membership, an allowed L0 request, a hard deny, audit growth, and—when requested—the live kill switch with automatic cleanup.

## Policy changes

`policy/action-gate.json` is a constitutional repository path. Runtime policy is copied to `/etc` by the root-only installer; the daemon does not accept a caller-selected policy path or environment override.

A policy change therefore follows the normal candidate/CI/owner-promotion path, followed by root deployment and gate restart. Do not make runtime self-editing of gate policy a future convenience feature.

## Audit inspection

```bash
sudo tail -n 50 /var/log/frankensteinzhermes-gate/action-gate.jsonl
sudo journalctl -u fzh-action-gate.service --since today
```

The append-only application log is owned by the gate service rather than Hermes. B031 should store the returned request/policy hashes with job state so side effects can be correlated to the exact decision without copying sensitive request payloads into multiple stores.

## Rollback

For a bad gate release, activate the kill switch **before** changing service files. Roll back the root-owned executable/policy/unit to the last known-good revision, restart `fzh-action-gate.service`, run verification while the kill switch remains active, then deliberately remove the switch and verify the normal L0/hard-deny contract again.

A gate failure is safer than bypassing the gate: the client fails closed when the service/socket is unavailable.

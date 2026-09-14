#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import grp
import json
import os
import signal
import socket
import sys
import uuid
from pathlib import Path

from action_gate import GateError, decide, load_policy

POLICY_PATH = Path('/etc/frankensteinzhermes/action-gate.json')
KILL_SWITCH_PATH = Path('/etc/frankensteinzhermes/action-gate.kill')
SOCKET_PATH = Path('/run/frankensteinzhermes-gate/action-gate.sock')
AUDIT_PATH = Path('/var/log/frankensteinzhermes-gate/action-gate.jsonl')
CLIENT_GROUP = 'fzh-gate-clients'
MAX_REQUEST_BYTES = 65536


def audit(decision: dict) -> dict:
    record = dict(decision)
    record['event'] = 'action_gate_decision'
    record['event_id'] = str(uuid.uuid4())
    record['observed_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n'
    fd = os.open(AUDIT_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(fd, payload.encode('utf-8'))
        os.fsync(fd)
    finally:
        os.close(fd)
    print(payload, end='', flush=True)
    return record


def recv_request(conn: socket.socket) -> object:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            raise ValueError('request_too_large')
        chunks.append(chunk)
        if b'\n' in chunk:
            break
    raw = b''.join(chunks).split(b'\n', 1)[0]
    if not raw:
        raise ValueError('empty_request')
    return json.loads(raw.decode('utf-8'))


def main() -> int:
    try:
        policy = load_policy(POLICY_PATH)
    except GateError as exc:
        print(f'action gate policy load failed: {exc}', file=sys.stderr)
        return 70

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_symlink():
        SOCKET_PATH.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o660)
    os.chown(SOCKET_PATH, -1, grp.getgrnam(CLIENT_GROUP).gr_gid)
    server.listen(32)
    server.settimeout(1.0)

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        while not stopping:
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            with conn:
                try:
                    request = recv_request(conn)
                    result = decide(request, policy, kill_switch_active=KILL_SWITCH_PATH.exists())
                except Exception as exc:  # transport/parsing failures fail closed
                    result = decide(
                        {'request_id': 'invalid', 'actor': 'unknown', 'action_type': 'invalid_request'},
                        policy,
                        kill_switch_active=KILL_SWITCH_PATH.exists(),
                    )
                    result['reason'] = f'fail_closed:transport:{exc.__class__.__name__}'
                audited = audit(result)
                conn.sendall((json.dumps(audited, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8'))
    finally:
        server.close()
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

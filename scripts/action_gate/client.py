#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

SOCKET_PATH = Path('/run/frankensteinzhermes-gate/action-gate.sock')
MAX_RESPONSE_BYTES = 65536
EXIT_CODES = {'allow': 0, 'approval_required': 20, 'deny': 30}


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except Exception as exc:
        print(json.dumps({'decision': 'deny', 'reason': f'client_invalid_json:{exc.__class__.__name__}'}))
        return 30

    payload = json.dumps(request, sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n'
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(SOCKET_PATH))
            sock.sendall(payload)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise RuntimeError('response_too_large')
                chunks.append(chunk)
                if b'\n' in chunk:
                    break
        response = json.loads(b''.join(chunks).split(b'\n', 1)[0].decode('utf-8'))
    except Exception as exc:
        # A missing/broken gate never degrades to implicit permission.
        print(json.dumps({'decision': 'deny', 'reason': f'gate_unavailable:{exc.__class__.__name__}'}))
        return 30

    print(json.dumps(response, sort_keys=True, separators=(',', ':')))
    return EXIT_CODES.get(response.get('decision'), 30)


if __name__ == '__main__':
    raise SystemExit(main())

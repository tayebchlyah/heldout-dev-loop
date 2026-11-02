#!/usr/bin/env python3
"""Read trusted policy through the installed Codex configuration resolver."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

from broker import stop


def load(codex, main):
    process = subprocess.Popen([codex, '-C', str(main), '-c', 'default_permissions="dev-workspace"', 'app-server', '--listen', 'stdio://'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, cwd=main, start_new_session=True)
    messages = queue.Queue()

    def receive():
        try:
            for line in process.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()

    def request(ident, method, params):
        process.stdin.write(json.dumps({'id': ident, 'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        deadline = time.monotonic() + 15
        while True:
            try:
                line = messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise ValueError('native policy response timed out') from error
            if line is None:
                raise ValueError('native policy resolver stopped')
            message = json.loads(line)
            if message.get('id') != ident:
                continue
            if 'error' in message:
                raise ValueError('native policy resolver rejected ' + method)
            return message['result']

    try:
        request(0, 'initialize', {'clientInfo': {'name': 'devloop-gate', 'version': '1'},
                                  'capabilities': {'experimentalApi': True}})
        process.stdin.write('{"method":"initialized"}\n')
        process.stdin.flush()
        config = request(1, 'config/read', {'includeLayers': False, 'cwd': str(main)})['config']
        requirements = request(2, 'configRequirements/read', {}).get('requirements') or {}
        if not isinstance(config.get('permissions'), dict) or not isinstance(requirements, dict):
            raise ValueError('invalid native policy response')
        return config, requirements
    except (KeyError, TypeError, json.JSONDecodeError, BrokenPipeError) as error:
        raise ValueError('invalid native policy response') from error
    finally:
        process.stdin.close()
        stop(process)
        process.stdout.close()
        reader.join(timeout=1)

#!/usr/bin/env python3
"""Launch nested gates as confined siblings under immutable parent ceilings."""
from __future__ import annotations

import base64
import json
import os
import secrets
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

_LIMIT = 4 * 1024 * 1024
_KEY = 'DEVLOOP_MACOS_BROKER'


def _send(stream, value):
    stream.write(json.dumps(value).encode() + b'\n')
    stream.flush()


def _read(stream):
    line = stream.readline(_LIMIT + 1)
    if not line or len(line) > _LIMIT or not line.endswith(b'\n'):
        raise ValueError('invalid macOS broker frame')
    return json.loads(line)


def request(rules, feature, command, network, local):
    """Forward only a confined command; an endpoint never grants host execution."""
    try:
        context = json.loads(os.environ[_KEY])
        port, token = context['port'], context['token']
        if type(port) is not int or not 0 < port < 65536 or not isinstance(token, str):
            raise ValueError('invalid endpoint')
        data = sys.stdin.buffer.read(_LIMIT + 1)
        if len(data) > _LIMIT // 2:
            raise ValueError('macOS broker input exceeds its frame limit')
        with socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
            connection.settimeout(None)
            with connection.makefile('rwb') as stream:
                _send(stream, {'token': token, 'rules': rules, 'cwd': str(feature), 'command': command,
                               'network': network, 'local': local, 'env': dict(os.environ),
                               'input': base64.b64encode(data).decode()})
                while True:
                    response = _read(stream)
                    if 'exit' in response:
                        if type(response['exit']) is not int:
                            raise ValueError('invalid status')
                        return response['exit']
                    if 'error' in response:
                        raise ValueError('macOS broker rejected confined launch')
                    output = {'stdout': sys.stdout.buffer, 'stderr': sys.stderr.buffer}.get(response.get('stream'))
                    if output is None:
                        raise ValueError('invalid stream')
                    output.write(base64.b64decode(response['data'], validate=True))
                    output.flush()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError('macOS broker startup or protocol failed') from error


class _TCP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False


class Server:
    """Own fixed policy contexts and never run caller code before Seatbelt."""
    def __init__(self, root, rules, proxy, ports, network, local, binary, ceilings=()):
        self.root, self.proxy, self.ports, self.binary = root, proxy, ports, binary
        self.closing = False
        self.lock = threading.Lock()
        self.contexts = {}
        self.processes = set()
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)
                try:
                    data = _read(self.rfile)
                    self.connection.settimeout(None)
                    owner._run(data, self.wfile)
                except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                    try:
                        _send(self.wfile, {'error': 'confined launch failed'})
                    except OSError:
                        pass

        self.server = _TCP(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        import macos
        self.context = self._context([macos._normalized(layer) for layer in [*ceilings, rules]], network, local)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _context(self, layers, network, local):
        token = secrets.token_hex(32)
        with self.lock:
            if self.closing or len(self.contexts) >= 128:
                raise ValueError('macOS broker context limit reached')
            self.contexts[token] = (layers, network, local)
        return json.dumps({'port': self.port, 'token': token})

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *error):
        with self.lock:
            self.closing = True
            processes = list(self.processes)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        with self.lock:
            self.contexts.clear()
        for process in processes:
            self._stop(process)

    def _stop(self, process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def _run(self, data, stream):
        import macos
        with self.lock:
            layers, network, local = self.contexts[data['token']]
        rules, command, env = data['rules'], data['command'], data['env']
        rules = macos._normalized(rules)
        if not isinstance(command, list) or not command or not all(isinstance(s, str) and '\0' not in s for s in command):
            raise ValueError('invalid command')
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) and '=' not in k and '\0' not in k + v for k, v in env.items()):
            raise ValueError('invalid environment')
        if type(data['network']) is not bool or type(data['local']) is not bool:
            raise ValueError('invalid network request')
        cwd = Path(data['cwd'])
        if not cwd.is_absolute():
            raise ValueError('invalid cwd')
        input_data = base64.b64decode(data['input'], validate=True)
        network, local = network and data['network'], local and data['local']
        child_context = None
        process = None
        with tempfile.TemporaryDirectory(prefix='child-', dir=self.root) as temporary:
            temp = Path(temporary)
            env = {k: v for k, v in env.items() if k not in macos._PROXY | macos._CA | {_KEY}}
            env.update(self.proxy)
            try:
                child_context = self._context([*layers, rules], network, local)
                env[_KEY] = child_context
                env.update({'PATH': str(self.binary) + os.pathsep + env.get('PATH', os.defpath),
                            'TMPDIR': str(temp), 'TMP': str(temp), 'TEMP': str(temp)})
                policy = macos._policy(rules, self.ports, network, local, layers, self.port, str(temp))
                with self.lock:
                    if self.closing:
                        raise ValueError('macOS broker is closing')
                    process = subprocess.Popen(macos._command(policy, temp, command), cwd=cwd, env=env,
                                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               start_new_session=True)
                    self.processes.add(process)
                output_lock = threading.Lock()

                def relay(pipe, name):
                    try:
                        while chunk := pipe.read(32768):
                            with output_lock:
                                _send(stream, {'stream': name, 'data': base64.b64encode(chunk).decode()})
                    except OSError:
                        self._stop(process)
                    finally:
                        pipe.close()

                threads = [threading.Thread(target=relay, args=(pipe, name), daemon=True)
                           for pipe, name in [(process.stdout, 'stdout'), (process.stderr, 'stderr')]]
                for thread in threads:
                    thread.start()
                try:
                    process.stdin.write(input_data)
                    process.stdin.close()
                except BrokenPipeError:
                    pass
                result = process.wait()
                self._stop(process)
                for thread in threads:
                    thread.join()
                if not (temp / 'started').is_file():
                    raise ValueError('macOS sibling launcher failed before command start')
            finally:
                if process is not None:
                    self._stop(process)
                    with self.lock:
                        self.processes.discard(process)
                if child_context is not None:
                    with self.lock:
                        self.contexts.pop(json.loads(child_context)['token'], None)
        _send(stream, {'exit': result})

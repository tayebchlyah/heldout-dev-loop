#!/usr/bin/env python3
"""Run gates in Seatbelt while native Codex owns their managed network proxy."""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import secrets
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import LineReader, METADATA, darwin_temp, require_started, start_marker, stop
from macos_policy import (SEATBELT_BASE_POLICY_SBPL, SEATBELT_NETWORK_POLICY_SBPL,
                          SEATBELT_READ_ONLY_PLATFORM_DEFAULTS_SBPL)

_PROXY = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
          'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
_PROTECTED = (*METADATA, '.aws')
_CA = {'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
       'NODE_EXTRA_CA_CERTS', 'GIT_SSL_CAINFO'}


def _quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def _filter(path):
    if any(c in '"^?[]{}\\' or not c.isprintable() for c in path):
        raise ValueError('unsupported macOS filesystem pattern: ' + path)
    if '*' not in path:
        return '(subpath ' + _quote(path) + ')'
    parent, _, name = path.rpartition('/')
    recursive = parent.endswith('/**')
    if recursive:
        parent = parent[:-3]
    if '*' in parent or not name or name in ('.', '..') or ('*' in name and (name.count('*') != 1 or not name.endswith('*'))):
        raise ValueError('unsupported macOS filesystem pattern: ' + path)
    escape = lambda value: ''.join('[' + c + ']' if c in '.^$+(){}|' else c for c in value)
    regex = '^' + escape(parent) + ('/([^/]+/)*' if recursive else '/')
    regex += escape(name).replace('*', '[^/]*') + '(/.*)?$'
    return '(regex #' + _quote(regex) + ')'


def _normalized(rules):
    if not isinstance(rules, dict) or rules.get('/') not in ('read', 'write'):
        raise ValueError('macOS adapter requires the native root-read baseline')
    normalized = {}
    rank = {'write': 0, 'read': 1, 'deny': 2}
    for raw, permission in rules.items():
        if not isinstance(raw, str) or not Path(raw).is_absolute() or permission not in rank:
            raise ValueError('invalid macOS filesystem rule')
        for path in {raw.rstrip('/') or '/', str(Path(raw).resolve())}:
            _filter(path)
            normalized[path] = max(normalized.get(path, permission), permission, key=rank.get)
    for path, mode in normalized.items():
        if mode == 'deny' and '*' not in path:
            if any(other != path and other.startswith(path.rstrip('/') + '/') and value != 'deny'
                   for other, value in normalized.items()):
                raise ValueError('unsupported permission exception below macOS deny rule: ' + path)
    return normalized


def _runtime_root(layers):
    roots = [Path('/tmp').resolve(), darwin_temp()]
    for root in roots:
        if all(max(((len(path), mode) for path, mode in layer.items() if path != '/' and
                    (str(root) == path or str(root).startswith(path.rstrip('/') + '/'))),
                   default=(0, 'write'))[1] == 'write' for layer in layers):
            return root
    raise ValueError('no writable macOS runtime temporary root')


def _write_mask(normalized, runtime=None):
    metadata = {str(Path(p) / name) for p, mode in normalized.items() if mode == 'write'
                for name in _PROTECTED}
    writers = []
    for path, permission in normalized.items():
        if permission != 'write':
            continue
        exclusions = []
        for child, child_permission in normalized.items():
            if child_permission != 'write' and child != path and child.startswith(path.rstrip('/') + '/'):
                nested = [p for p, v in normalized.items() if v == 'write' and p.startswith(child.rstrip('/') + '/')]
                denied = _filter(child)
                if nested and child_permission == 'read':
                    denied = '(require-all ' + denied + ''.join(' (require-not ' + _filter(p) + ')' for p in nested) + ')'
                exclusions.append(' (require-not ' + denied + ')')
        exclusions += [' (require-not (subpath ' + _quote(p) + '))' for p in metadata]
        writers.append('(require-all ' + _filter(path) + ''.join(exclusions) + ')')
    if runtime is not None:
        exclusions = [' (require-not ' + _filter(p) + ')' for p, mode in normalized.items()
                      if p != '/' and mode in ('read', 'deny')]
        exclusions += [' (require-not (subpath ' + _quote(str(Path(runtime) / name)) + '))'
                       for name in _PROTECTED]
        writers.append('(require-all ' + _filter(str(runtime)) + ''.join(exclusions) + ')')
    return '(require-any ' + ' '.join(writers) + ')' if writers else '(literal "/devloop-no-write-root")'


def _policy(rules, ports, network_enabled, allow_local_binding, ceilings=(), broker_port=None, runtime=None):
    parts = [SEATBELT_BASE_POLICY_SBPL, SEATBELT_READ_ONLY_PLATFORM_DEFAULTS_SBPL]
    if network_enabled:
        parts.append(SEATBELT_NETWORK_POLICY_SBPL)
    layers = [_normalized(layer) for layer in [*ceilings, rules]]
    parts.append('(allow file-read* file-map-executable (subpath "/"))')
    parts.append('(allow file-write* (require-all ' + ' '.join(_write_mask(layer, runtime) for layer in layers) + '))')
    if runtime is not None:
        parts.append('(deny file-write-unlink (literal ' + _quote(runtime) + '))')
    for normalized in layers:
        for path, permission in normalized.items():
            if permission == 'write':
                parts.append('(deny file-write-unlink (literal ' + _quote(path) + '))')
            if permission == 'write' or path == '/':
                continue
            if permission == 'deny':
                parts.append('(deny file-read* file-write* file-map-executable ' + _filter(path) + ')')
            ancestor = Path(path.split('*', 1)[0]).parent
            while str(ancestor) != '/':
                parts.append('(deny file-write-unlink (literal ' + _quote(ancestor) + '))')
                ancestor = ancestor.parent
            if '*' in path:
                base = path.split('/**/', 1)[0] if '/**/' in path else str(Path(path.split('*', 1)[0].rstrip('/')).parent)
                parts.append('(deny file-write-unlink (require-all (subpath ' + _quote(base) + ') (vnode-type DIRECTORY)))')
    parts.append('(allow sysctl-read (sysctl-name-prefix "kern.proc."))')
    parts.append('(allow mach-register mach-lookup (global-name-regex #"^org[.]chromium[.]Chromium[.]MachPortRendezvousServer[.][0-9]+$"))')
    if network_enabled:
        for port in ports:
            parts.append(f'(allow network-outbound (remote tcp "localhost:{port}"))')
        if allow_local_binding:
            parts += ['(allow network-bind (local ip "localhost:*"))',
                      '(allow network-inbound (local ip "localhost:*"))',
                      '(allow network-outbound (remote ip "localhost:*"))']
    if broker_port is not None:
        parts.append(f'(allow network-outbound (remote tcp "localhost:{broker_port}"))')
    parts.append('(deny network* (socket-domain AF_UNIX))')
    return '\n'.join(parts)


def _proxy_state(keeper, network_enabled):
    line = LineReader(keeper.stdout, 65536).line(time.monotonic() + 15, 'native proxy startup')
    try:
        env = json.loads(line)['env']
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError('invalid native proxy startup state') from error
    if keeper.poll() is not None:
        raise ValueError('native proxy exited after its startup state')
    ports = set()
    for key, value in env.items():
        if network_enabled and key in _PROXY and key.lower() != 'no_proxy':
            try:
                parsed = urlsplit(value)
                host = parsed.hostname
                local = host == 'localhost' or ipaddress.ip_address(host).is_loopback
                if not local or parsed.scheme not in ('http', 'https', 'socks5', 'socks5h') or not parsed.port or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
                    raise ValueError('proxy must use loopback')
                ports.add(parsed.port)
            except (TypeError, ValueError) as error:
                raise ValueError('invalid native loopback proxy URL') from error
    return env, ports


def _prepare(temp, env, work):
    binary = temp / 'bin'
    binary.mkdir()
    shutil.copyfile('/bin/ps', binary / 'ps')
    (binary / 'ps').chmod(0o755)
    signed = subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(binary / 'ps')], capture_output=True)
    if signed.returncode:
        raise ValueError('private process-list helper signing failed')
    return {**env, 'PATH': str(binary) + os.pathsep + env.get('PATH', os.defpath),
            'TMPDIR': str(work), 'TMP': str(work), 'TEMP': str(work)}


def _command(policy, work, command):
    wrapper = start_marker('"$1"') + '; shift; exec "$@"'
    return ['/usr/bin/sandbox-exec', '-p', policy, '/bin/sh', '-c', wrapper,
            'dev-loop-gate', str(work / 'started'), *command]


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
        data = sys.stdin.buffer.read(_LIMIT + 1)
        if len(data) > _LIMIT // 2:
            raise ValueError('macOS broker input exceeds its frame limit')
        with socket.create_connection(('127.0.0.1', context['port']), timeout=5) as connection:
            connection.settimeout(None)
            with connection.makefile('rwb') as stream:
                _send(stream, {'token': context['token'], 'rules': rules, 'cwd': str(feature), 'command': command,
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
    def __init__(self, rules, proxy, ports, network, local, binary, ceilings=()):
        self.proxy, self.ports, self.binary = proxy, ports, binary
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
        self.context = self._context([_normalized(layer) for layer in [*ceilings, rules]], network, local)
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
            stop(process)

    def _run(self, data, stream):
        with self.lock:
            layers, network, local = self.contexts[data['token']]
        rules, command, env = data['rules'], data['command'], data['env']
        rules = _normalized(rules)
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
        root = _runtime_root([*layers, rules])
        with tempfile.TemporaryDirectory(prefix='devloop-child-', dir=root) as temporary:
            temp = Path(temporary)
            env = {k: v for k, v in env.items() if k not in _PROXY | _CA | {_KEY}}
            env.update(self.proxy)
            try:
                child_context = self._context([*layers, rules], network, local)
                env[_KEY] = child_context
                env.update({'PATH': str(self.binary) + os.pathsep + env.get('PATH', os.defpath),
                            'TMPDIR': str(temp), 'TMP': str(temp), 'TEMP': str(temp)})
                policy = _policy(rules, self.ports, network, local, layers, self.port, str(temp))
                with self.lock:
                    if self.closing:
                        raise ValueError('macOS broker is closing')
                    process = subprocess.Popen(_command(policy, temp, command), cwd=cwd, env=env,
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
                        stop(process)
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
                stop(process)
                for thread in threads:
                    thread.join(timeout=3)
                    if thread.is_alive():
                        raise ValueError('macOS sibling stream cleanup timed out')
                require_started(temp / 'started', 'macOS sibling launcher', result)
            finally:
                if process is not None:
                    stop(process)
                    with self.lock:
                        self.processes.discard(process)
                if child_context is not None:
                    with self.lock:
                        self.contexts.pop(json.loads(child_context)['token'], None)
        _send(stream, {'exit': result})


def run(native_args: list[str], trusted: Path, rules: dict[str, str], feature: Path,
        command: list[str], network_enabled: bool, allow_local_binding: bool) -> int:
    """Preserve gate streams and exit status under native managed policy."""
    common = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(trusted),
                             'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')},
                            capture_output=True, text=True, check=True).stdout.strip()
    common = Path(common).resolve(strict=True)
    if not common.is_dir():
        raise ValueError('invalid trusted Git metadata directory')
    rules = _normalized(rules)
    rules[str(common)] = 'deny' if rules.get(str(common)) == 'deny' else 'read'
    if _KEY in os.environ:
        return request(rules, feature, command, network_enabled, allow_local_binding)
    keeper = subprocess.Popen([*native_args, '--', sys.executable, '-I', __file__, '--proxy-state'],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
    gate = None
    previous = {}
    try:
        proxy, ports = _proxy_state(keeper, network_enabled)
        if network_enabled and not ports:
            raise ValueError('native managed proxy is unavailable')
        ceiling = _normalized(rules)
        root = _runtime_root([ceiling])
        with tempfile.TemporaryDirectory(prefix='devloop-macos-', dir=root) as temporary, \
                tempfile.TemporaryDirectory(prefix='devloop-hook-', dir=common) as staged:
            temp = Path(temporary).resolve()
            rules = {**rules, str(temp): 'write'}
            env = dict(proxy)
            env.pop('GATE_SCOPE', None)
            if os.environ.get('GATE_SCOPE'):
                env['GATE_SCOPE'] = os.environ['GATE_SCOPE']
            private = Path(staged)
            env = _prepare(private, env, temp)
            with Server(rules, {k: v for k, v in proxy.items() if k in _PROXY | _CA}, ports, network_enabled, allow_local_binding,
                               private / 'bin', ceilings=[ceiling]) as server:
                env[_KEY] = server.context
                policy = _policy(rules, ports, network_enabled, allow_local_binding,
                                 ceilings=[ceiling], broker_port=server.port, runtime=str(temp))
                gate = subprocess.Popen(_command(policy, temp, command), cwd=feature, env=env, start_new_session=True)
                def forward(signum, frame):
                    if gate.poll() is None:
                        os.killpg(gate.pid, signum)
                for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    previous[signum] = signal.signal(signum, forward)
                result = gate.wait()
                require_started(temp / 'started', 'macOS launcher', result)
                return result
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        for process in (gate, keeper):
            if process is None:
                continue
            stop(process)
        keeper.stdin.close()
        keeper.stdout.close()


if __name__ == '__main__':
    if sys.argv[1:] != ['--proxy-state']:
        sys.exit(125)
    print(json.dumps({'env': dict(os.environ)}), flush=True)
    sys.stdin.buffer.read()

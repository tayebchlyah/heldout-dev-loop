#!/usr/bin/env python3
"""Run gates in Seatbelt while native Codex owns their managed network proxy."""
from __future__ import annotations

import ipaddress
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from macos_policy import (SEATBELT_BASE_POLICY_SBPL, SEATBELT_NETWORK_POLICY_SBPL,
                          SEATBELT_READ_ONLY_PLATFORM_DEFAULTS_SBPL)

_PROXY = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
          'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
_CA = {'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
       'NODE_EXTRA_CA_CERTS', 'GIT_SSL_CAINFO'}


def _quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def _filter(path):
    if any(c in path for c in '?[]{}\\\n\r'):
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
    darwin_temp = subprocess.run(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'],
                                 capture_output=True, text=True, check=True).stdout.strip()
    if not darwin_temp or not Path(darwin_temp).is_absolute():
        raise ValueError('invalid Darwin temporary root')
    roots = [Path('/tmp').resolve(), Path(darwin_temp).resolve()]
    for root in roots:
        if all(max(((len(path), mode) for path, mode in layer.items() if path != '/' and
                    (str(root) == path or str(root).startswith(path.rstrip('/') + '/'))),
                   default=(0, 'write'))[1] == 'write' for layer in layers):
            return root
    raise ValueError('no writable macOS runtime temporary root')


def _write_mask(normalized, runtime=None):
    metadata = {str(Path(p) / name) for p, mode in normalized.items() if mode == 'write'
                for name in ('.git', '.agents', '.codex', '.aws')}
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
                       for name in ('.git', '.agents', '.codex', '.aws')]
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


def _proxy_state(keeper):
    deadline = time.monotonic() + 15
    line = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(keeper.stdout, selectors.EVENT_READ)
        while b'\n' not in line:
            if not selector.select(max(0, deadline - time.monotonic())):
                raise ValueError('native proxy startup timed out')
            chunk = os.read(keeper.stdout.fileno(), 65537 - len(line))
            if not chunk or len(line) + len(chunk) > 65536:
                raise ValueError('native proxy exited or returned oversized startup state')
            line.extend(chunk)
    line = bytes(line).split(b'\n', 1)[0]
    try:
        state = json.loads(line)
    except (ValueError, UnicodeError) as error:
        raise ValueError('invalid native proxy startup state') from error
    if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('env'), dict) or keeper.poll() is not None:
        raise ValueError('native proxy exited or returned invalid startup state')
    env, ports = {}, set()
    for key, value in state['env'].items():
        if not isinstance(key, str) or not key or '=' in key or not isinstance(value, str) or '\0' in key + value:
            raise ValueError('invalid native proxy environment')
        if key in _PROXY and key.lower() != 'no_proxy':
            try:
                parsed = urlsplit(value)
                host = parsed.hostname
                local = host == 'localhost' or ipaddress.ip_address(host).is_loopback
                if not local or parsed.scheme not in ('http', 'https', 'socks5', 'socks5h') or not parsed.port or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
                    raise ValueError('proxy must use loopback')
                ports.add(parsed.port)
            except (TypeError, ValueError) as error:
                raise ValueError('invalid native loopback proxy URL') from error
        env[key] = value
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
    wrapper = 'printf started >"$1" || exit 125; shift; exec "$@"'
    return ['/usr/bin/sandbox-exec', '-p', policy, '/bin/sh', '-c', wrapper,
            'dev-loop-gate', str(work / 'started'), *command]


def run(native_args: list[str], rules: dict[str, str], feature: Path,
        command: list[str], network_enabled: bool, allow_local_binding: bool) -> int:
    """Preserve gate streams and exit status under native managed policy."""
    import broker
    if '-C' in native_args:
        trusted = Path(native_args[native_args.index('-C') + 1]).resolve(strict=True)
        common = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(trusted),
                                 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')},
                                capture_output=True, text=True, check=True).stdout.strip()
        common = Path(common).resolve(strict=True)
        if not common.is_dir():
            raise ValueError('invalid trusted Git metadata directory')
        rules = _normalized(rules)
        rules[str(common)] = 'deny' if rules.get(str(common)) == 'deny' else 'read'
    if 'DEVLOOP_MACOS_BROKER' in os.environ:
        return broker.request(rules, feature, command, network_enabled, allow_local_binding)
    if '-C' not in native_args:
        raise ValueError('trusted main directory is required')
    keeper = subprocess.Popen([*native_args, '--', sys.executable, '-I', __file__, '--proxy-state'],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
    gate = None
    previous = {}
    try:
        proxy, ports = _proxy_state(keeper)
        if network_enabled and not ports:
            raise ValueError('native managed proxy is unavailable')
        ceiling = _normalized(rules)
        root = _runtime_root([ceiling])
        with tempfile.TemporaryDirectory(prefix='devloop-macos-', dir=root) as temporary, \
                tempfile.TemporaryDirectory(prefix='devloop-hook-', dir=common) as staged:
            temp = Path(temporary).resolve()
            rules = {**rules, str(temp): 'write'}
            env = dict(proxy)
            env['GATE_SCOPE'] = 'changed'
            private = Path(staged)
            env = _prepare(private, env, temp)
            with broker.Server(rules, {k: v for k, v in proxy.items() if k in _PROXY | _CA}, ports, network_enabled, allow_local_binding,
                               private / 'bin', ceilings=[ceiling]) as server:
                env['DEVLOOP_MACOS_BROKER'] = server.context
                policy = _policy(rules, ports, network_enabled, allow_local_binding,
                                 ceilings=[ceiling], broker_port=server.port, runtime=str(temp))
                gate = subprocess.Popen(_command(policy, temp, command), cwd=feature, env=env, start_new_session=True)
                def forward(signum, frame):
                    if gate.poll() is None:
                        os.killpg(gate.pid, signum)
                for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    previous[signum] = signal.signal(signum, forward)
                result = gate.wait()
                if not (temp / 'started').is_file():
                    raise ValueError('macOS launcher exited before command start (exit ' + str(result) + ')')
                return result
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        for process in (gate, keeper):
            if process is None:
                continue
            broker.stop(process)
        keeper.stdin.close()
        keeper.stdout.close()


if __name__ == '__main__':
    if sys.argv[1:] != ['--proxy-state']:
        sys.exit(125)
    print(json.dumps({'version': 1, 'env': dict(os.environ)}), flush=True)
    sys.stdin.buffer.read()

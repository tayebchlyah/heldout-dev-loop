#!/usr/bin/env python3
"""Run a feature gate with trusted policy and native Codex confinement."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import macos
from common import LineReader, METADATA, darwin_temp, require_started, start_marker, stop


def merge(left, right):
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            merge(left[key], value)
        else:
            left[key] = value
    return left


def native_policy(codex, main):
    process = subprocess.Popen([codex, '-C', str(main), '-c', 'default_permissions="dev-workspace"', 'app-server', '--listen', 'stdio://'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               cwd=main, start_new_session=True)
    reader = LineReader(process.stdout)

    def send(message):
        process.stdin.write((json.dumps(message) + '\n').encode())
        process.stdin.flush()

    def request(ident, method, params):
        send({'id': ident, 'method': method, 'params': params})
        deadline = time.monotonic() + 15
        while True:
            message = json.loads(reader.line(deadline, 'native policy resolver'))
            if message.get('id') != ident:
                continue
            if 'error' in message:
                raise ValueError('native policy resolver rejected ' + method)
            return message['result']

    try:
        request(0, 'initialize', {'clientInfo': {'name': 'devloop-gate', 'version': '1'},
                                  'capabilities': {'experimentalApi': True}})
        send({'method': 'initialized'})
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


def trusted_profile(codex, main):
    config, requirements = native_policy(codex, main)
    allowed = requirements.get('allowedPermissionProfiles')
    if allowed is not None and allowed.get('dev-workspace') is not True:
        raise ValueError('administrator disallows dev-workspace')
    if sys.platform == 'darwin' and requirements.get('allowBrowserAndComputerUse') is False:
        raise ValueError('administrator disallows browser compatibility')
    profiles = config['permissions']

    def inherit(name, stack):
        if name in (':workspace', ':read-only'):
            return {}
        if name in stack or name not in profiles:
            raise ValueError('unsupported or cyclic permission profile: ' + name)
        own = profiles[name]
        if not isinstance(own, dict):
            raise ValueError('invalid permission profile')
        own = without_nulls(own)
        return merge(inherit(own.get('extends') or ':read-only', stack | {name}), own)

    return inherit('dev-workspace', set()), without_nulls(profiles['dev-workspace']), requirements


def filesystem(profile, main, feature):
    rules = {'/': 'read', str(Path('/tmp').resolve()): 'write'}
    if sys.platform == 'darwin':
        rules[str(darwin_temp())] = 'write'
    rules.update({str(main): 'read', str(feature): 'write'})
    for root, value in (profile.get('filesystem') or {}).items():
        if root == 'glob_scan_max_depth':
            if value is not None:
                raise ValueError('unsupported filesystem scan limit')
            continue
        if root == ':workspace_roots':
            bases = [main, feature]
        elif root == ':root':
            bases = [Path('/')]
        elif root.startswith(':'):
            raise ValueError('unsupported filesystem scope: ' + root)
        else:
            expanded = Path(os.path.expanduser(root)) if root == '~' or root.startswith('~/') else Path(root)
            bases = [expanded if expanded.is_absolute() else main / root]
        entries = list(value.items()) if isinstance(value, dict) else [('', value)]
        for pattern, permission in entries:
            if permission not in ('read', 'write', 'deny'):
                raise ValueError('unsupported filesystem permission')
            if permission != 'write':
                for base in bases:
                    rules[str(base / pattern)] = permission
    for root in {main, feature}:
        for pattern in ('.env', '.env.*', '**/.env', '**/.env.*'):
            rules[str(root / pattern)] = 'deny'
    return rules


def neutralised(own, computed, roots):
    """Native table: Codex key-merges it over the trusted one, so no trusted grant survives."""
    demote = lambda value: 'read' if value == 'write' else value
    table = dict(computed)
    for root in roots:
        for name in METADATA:
            table.setdefault(str(root / name), 'read')
    for key, value in (own.get('filesystem') or {}).items():
        if key in computed or key == 'glob_scan_max_depth':
            continue
        table[key] = {k: demote(v) for k, v in value.items()} if isinstance(value, dict) else demote(value)
    sockets = {key: 'deny' for key in ((own.get('network') or {}).get('unix_sockets') or {})}
    return table, sockets


def without_nulls(value):
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    return value


def toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
    return json.dumps(value)


def native_args(codex, main, overrides):
    args = [codex, 'sandbox', '-C', str(main), '-P', 'dev-workspace', '--include-managed-config']
    for key, value in overrides.items():
        args += ['-c', key + '=' + toml(value)]
    return args


def main():
    try:
        if len(sys.argv) < 5 or sys.argv[3] != '--':
            raise ValueError('expected main, feature, --, command arguments')
        trusted, feature = (Path(x).resolve(strict=True) for x in sys.argv[1:3])
        codex = shutil.which('codex')
        if not codex:
            raise ValueError('codex-cli is unavailable; require 0.166.0 or later')
        version = subprocess.run([codex, '--version'], capture_output=True, text=True, check=True)
        found = re.search(r'codex-cli (\d+)\.(\d+)\.(\d+)', version.stdout)
        if not found or tuple(map(int, found.groups())) < (0, 166, 0):
            raise ValueError('require codex-cli 0.166.0 or later')
        profile, own, requirements = trusted_profile(codex, trusted)
        network = without_nulls(profile.get('network') or {})
        if not isinstance(network, dict):
            raise ValueError('invalid network policy')
        if (requirements.get('network') or {}).get('allowLocalBinding') is False:
            network['allow_local_binding'] = False
        rules = filesystem(profile, trusted, feature)
        table, sockets = neutralised(own, rules, (trusted, feature))
        network |= {'unix_sockets': sockets, 'dangerously_allow_all_unix_sockets': False}
        overrides = {'features.network_proxy': True,
                     'permissions.dev-workspace.extends': ':read-only',
                     'permissions.dev-workspace.filesystem': table,
                     'permissions.dev-workspace.network': network}
        args = native_args(codex, trusted, overrides)
        if sys.platform == 'darwin':
            return macos.run(args, trusted, rules, feature,
                             sys.argv[4:], network.get('enabled') is True,
                             network.get('allow_local_binding') is True)
        with tempfile.TemporaryDirectory(prefix='.devloop-hook-', dir='/tmp') as temp:
            started = Path(temp) / 'started'
            wrapper = 'export TMPDIR="${2%/*}" TMP="${2%/*}" TEMP="${2%/*}"; cd "$1" || exit 125; ' + start_marker('"$2"') + '; if [ -n "$3" ]; then export GATE_SCOPE="$3"; else unset GATE_SCOPE; fi; shift 3; exec "$@"'
            result = subprocess.run([*args, '--', '/bin/sh', '-c', wrapper, 'dev-loop-gate',
                                     str(feature), str(started), os.environ.get('GATE_SCOPE', ''), *sys.argv[4:]])
            require_started(started, 'native launcher', result.returncode)
            return result.returncode
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        print('Sandbox startup failed: ' + str(error), file=sys.stderr)
        return 125


if __name__ == '__main__':
    sys.exit(main())

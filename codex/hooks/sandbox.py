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
import tomllib
from pathlib import Path


def merge(left, right):
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            merge(left[key], value)
        else:
            left[key] = value
    return left


def trusted_profile(main):
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    paths = [Path('/etc/codex/config.toml'), home / 'config.toml']
    paths += [p / '.codex/config.toml' for p in [*reversed(main.parents), main]]
    config, seen = {}, set()
    for path in paths:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        merge(config, tomllib.loads(path.read_text()))
    profiles = config.get('permissions', {})

    def inherit(name, stack):
        if name in (':workspace', ':read-only'):
            return {}
        if name in stack or name not in profiles:
            raise ValueError('unsupported or cyclic permission profile: ' + name)
        own = profiles[name]
        if not isinstance(own, dict):
            raise ValueError('invalid permission profile')
        return merge(inherit(own.get('extends', ':read-only'), stack | {name}), own)

    return inherit('dev-workspace', set())


def filesystem(profile, main, feature):
    rules = {str(Path('/tmp').resolve()): 'write'}
    if sys.platform == 'darwin':
        darwin_temp = subprocess.run(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'],
                                     capture_output=True, text=True, check=True).stdout.strip()
        if not darwin_temp or not Path(darwin_temp).is_absolute():
            raise ValueError('invalid Darwin temporary root')
        rules[str(Path(darwin_temp).resolve())] = 'write'
    rules.update({str(main): 'read', str(feature): 'write'})
    for root, value in profile.get('filesystem', {}).items():
        if root == ':workspace_roots':
            bases = [main, feature]
        elif root == ':root':
            bases = [Path('/')]
        elif root.startswith(':'):
            raise ValueError('unsupported filesystem scope: ' + root)
        else:
            bases = [Path(root) if Path(root).is_absolute() else main / root]
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


def toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
    return json.dumps(value)


def main():
    try:
        trusted, feature = (Path(x).resolve(strict=True) for x in sys.argv[1:3])
        if sys.argv[3] != '--' or len(sys.argv) < 5:
            raise ValueError('expected main, feature, --, command arguments')
        codex = shutil.which('codex')
        if not codex:
            raise ValueError('codex-cli is unavailable; require 0.166.0 or later')
        version = subprocess.run([codex, '--version'], capture_output=True, text=True, check=True)
        found = re.search(r'codex-cli (\d+)\.(\d+)\.(\d+)', version.stdout)
        if not found or tuple(map(int, found.groups())) < (0, 166, 0):
            raise ValueError('require codex-cli 0.166.0 or later')
        profile = trusted_profile(trusted)
        network = profile.get('network', {})
        if not isinstance(network, dict):
            raise ValueError('invalid network policy')
        network |= {'unix_sockets': {}, 'dangerously_allow_all_unix_sockets': False}
        if network.get('enabled') is True:
            network['mode'] = 'full'
        overrides = {'features.network_proxy': True,
                     'permissions.dev-workspace.extends': ':read-only',
                     'permissions.dev-workspace.filesystem': filesystem(profile, trusted, feature),
                     'permissions.dev-workspace.network': network}
        args = [codex, 'sandbox', '-C', str(trusted), '-P', 'dev-workspace', '--include-managed-config']
        for key, value in overrides.items():
            args += ['-c', key + '=' + toml(value)]
        with tempfile.TemporaryDirectory(prefix='.devloop-hook-', dir='/tmp') as temp:
            started = Path(temp) / 'started'
            wrapper = 'export TMPDIR="${2%/*}" TMP="${2%/*}" TEMP="${2%/*}"; cd "$1" || exit 125; printf started >"$2" || exit 125; shift 2; exec "$@"'
            result = subprocess.run([*args, '--', '/bin/sh', '-c', wrapper, 'dev-loop-gate',
                                     str(feature), str(started), *sys.argv[4:]])
            if not started.is_file():
                raise ValueError('native launcher exited before command start (exit ' + str(result.returncode) + ')')
            return result.returncode
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        print('Sandbox startup failed: ' + str(error), file=sys.stderr)
        return 125


if __name__ == '__main__':
    sys.exit(main())

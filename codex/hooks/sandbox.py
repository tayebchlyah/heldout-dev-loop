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
from pathlib import Path


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import policy


def merge(left, right):
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            merge(left[key], value)
        else:
            left[key] = value
    return left


def trusted_profile(codex, main):
    config, requirements = policy.load(codex, main)
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

    return inherit('dev-workspace', set()), requirements


def filesystem(profile, main, feature):
    rules = {'/': 'read', str(Path('/tmp').resolve()): 'write'}
    if sys.platform == 'darwin':
        darwin_temp = subprocess.run(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'],
                                     capture_output=True, text=True, check=True).stdout.strip()
        if not darwin_temp or not Path(darwin_temp).is_absolute():
            raise ValueError('invalid Darwin temporary root')
        rules[str(Path(darwin_temp).resolve())] = 'write'
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


def without_nulls(value):
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    return value


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
        profile, requirements = trusted_profile(codex, trusted)
        network = without_nulls(profile.get('network') or {})
        if not isinstance(network, dict):
            raise ValueError('invalid network policy')
        if (requirements.get('network') or {}).get('allowLocalBinding') is False:
            network['allow_local_binding'] = False
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
        if sys.platform == 'darwin':
            import macos
            return macos.run(args, overrides['permissions.dev-workspace.filesystem'], feature,
                             sys.argv[4:], network.get('enabled') is True,
                             network.get('allow_local_binding') is True)
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

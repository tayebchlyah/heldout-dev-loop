"""Automatic Codex gate launch through the public hook and native CLI boundary."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import CODEX_ASSETS, CODEX_GATE_FIXTURE, write


class CodexHookSandboxTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.main = self.root / 'main with spaces'
        self.feature = self.root / "feature 'quoted';$d"
        self.main.mkdir()
        self.git(self.main, 'init', '-q')
        write(self.main / 'tracked', 'base')
        self.config = self.main / '.codex/config.toml'
        write(self.config, '[permissions.dev-workspace]\nextends=":workspace"\n')
        self.git(self.main, 'add', '.')
        self.git(self.main, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'base')
        self.git(self.main, 'worktree', 'add', '-qb', 'fix/probe', str(self.feature))
        self.bin = self.root / 'bin'
        self.log = self.root / 'argv.json'
        write(self.bin / 'codex', '#!' + sys.executable + '\n' + CODEX_GATE_FIXTURE)
        (self.bin / 'codex').chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.bin)+os.pathsep+os.environ['PATH'],
                    'CODEX_HOME': str(self.root / 'home/.codex'), 'PROBE_ARGV': str(self.log)}

    def git(self, cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True)

    def gate(self, command='echo feature-failed; exit 7', event='Stop', **payload):
        write(self.feature / 'dev-loop.json', json.dumps({'commands': {'gate': command}}))
        return subprocess.run(['sh', str(CODEX_ASSETS / 'hooks/gate.sh')],
                              input=json.dumps({'cwd': str(self.feature), 'hook_event_name': event, **payload}),
                              text=True, capture_output=True, env=self.env)

    def launch(self, *command, data='hook stdin'):
        return subprocess.run([sys.executable, '-I', str(CODEX_ASSETS / 'hooks/sandbox.py'),
                               str(self.main), str(self.feature), '--', *command],
                              input=data, text=True, capture_output=True, env=self.env)

    def test_gate_routes_keep_feature_cwd_scope_and_failure(self):
        for event in ['Stop', 'SubagentStop']:
            for mapped in [True, False]:
                with self.subTest(event=event, mapped=mapped):
                    command = 'printf "%s scope=%s\\n" "$PWD" "$GATE_SCOPE"; exit 7'
                    if not mapped:
                        write(self.feature / 'scripts/gate.sh', '#!/bin/sh\n'+command+'\n')
                        (self.feature / 'scripts/gate.sh').chmod(0o755)
                    r = self.gate(command if mapped else None, event)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertIn(str(self.feature)+' scope=changed', json.loads(r.stdout)['reason'])
                    args = json.loads(self.log.read_text())
                    self.assertEqual(args[args.index('-C')+1], str(self.main))
                    if os.environ.get('DEVLOOP_MACOS_BROKER'):
                        self.assertIn('app-server', args)
                        self.assertIn('default_permissions="dev-workspace"', args)
                    else:
                        self.assertIn('--include-managed-config', args)

    def test_gate_success_and_existing_skips_are_silent(self):
        for command, active in [('exit 0', False), ('exit 9', True)]:
            with self.subTest(command=command):
                self.assertEqual(self.gate(command, stop_hook_active=active).stdout, '')
        (self.feature / 'dev-loop.json').unlink()
        self.assertEqual(self.gate('exit 9', stop_hook_active=True).stdout, '')
        self.git(self.feature, 'add', 'dev-loop.json')
        self.git(self.feature, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'gate')
        self.log.unlink(missing_ok=True)
        r = subprocess.run(['sh', str(CODEX_ASSETS/'hooks/gate.sh')], input=json.dumps({'cwd':str(self.feature)}),
                           text=True,capture_output=True,env=self.env)
        self.assertEqual(r.stdout, '')
        self.assertFalse(self.log.exists())

    def test_launch_preserves_arguments_stdin_and_exit_status(self):
        arg = 'a b;$(must-not-run)'
        r = self.launch(sys.executable, '-I', '-c',
                        'import os,sys,json; print(json.dumps([os.getcwd(),sys.argv[1],sys.stdin.read()])); sys.exit(19)', arg)
        self.assertEqual(r.returncode, 19, r.stderr)
        self.assertEqual(json.loads(r.stdout), [str(self.feature), arg, 'hook stdin'])
        self.assertNotIn('Sandbox startup failed', r.stderr)
        r = self.launch('/bin/sh', '-c', 'dir=$(mktemp -d) || exit 1; printf "%s" "$dir"; rmdir "$dir"')
        self.assertEqual(r.returncode, 0, r.stderr)
        roots = [Path('/tmp').resolve(), Path(tempfile.gettempdir()).resolve()]
        if sys.platform == 'darwin':
            roots.append(Path(subprocess.check_output(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'], text=True).strip()).resolve())
        self.assertTrue(any(Path(r.stdout).resolve().is_relative_to(root) for root in roots), r.stdout)

    def test_early_launch_failure_blocks_without_host_fallback(self):
        self.env['PROBE_EARLY'] = '1'
        marker = self.feature / 'host-marker'
        r = self.gate('touch host-marker')
        self.assertIn('Sandbox startup failed', json.loads(r.stdout)['reason'])
        self.assertFalse(marker.exists())

    def test_missing_or_old_launcher_blocks(self):
        for version in ['0.165.0', 'missing']:
            with self.subTest(version=version):
                self.env['PROBE_VERSION'] = version
                if version == 'missing':
                    self.env['PATH'] = '/nonexistent'
                    r = self.launch('/usr/bin/touch', 'host-marker')
                    self.assertIn('Sandbox startup failed', r.stderr)
                else:
                    r = self.gate('touch host-marker')
                    self.assertIn('Sandbox startup failed', json.loads(r.stdout)['reason'])
                self.assertFalse((self.feature/'host-marker').exists())

    def test_policy_keeps_trusted_denies_but_removes_write_grants(self):
        self.env['PROBE_NATIVE_NULLS'] = '1'
        self.config.write_text('[permissions.parent]\nextends=":workspace"\n'
                               '[permissions.parent.filesystem]\n'+json.dumps(str(self.root/'outside'))+'="write"\n'
                               +json.dumps(str(self.main/'secret'))+'="deny"\n'
                               '[permissions.parent.filesystem.":workspace_roots"]\n"**/.private"="deny"\n'
                               '[permissions.dev-workspace]\nextends="parent"\n')
        write(self.feature/'.codex/config.toml', '[permissions.dev-workspace]\nextends=":full-access"\n')
        write(self.main/'secret', 'trusted-secret')
        write(self.feature/'.private', 'feature-secret')
        r = self.launch(sys.executable, '-I', '-c',
                        'from pathlib import Path; import sys; '
                        '\nfor path in sys.argv[1:]:\n'
                        ' try: Path(path).read_text()\n except PermissionError: pass\n else: raise AssertionError(path)\n',
                        str(self.main/'secret'), str(self.feature/'.private'))
        self.assertEqual(r.returncode, 0, r.stderr)
        args = json.loads(self.log.read_text())
        if os.environ.get('DEVLOOP_MACOS_BROKER'):
            self.assertIn('app-server', args)
            self.assertEqual(args[args.index('-C')+1], str(self.main))
            return
        tables = [x for x in args if x.startswith('permissions.dev-workspace.filesystem=')]
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertIn(json.dumps(str(self.main/'secret'))+'="deny"', table)
        self.assertIn(json.dumps(str(self.main/'**/.private'))+'="deny"', table)
        self.assertIn(json.dumps(str(self.feature/'**/.private'))+'="deny"', table)
        self.assertNotIn(str(self.root/'outside'), table)
        self.assertIn(json.dumps(str(self.feature))+'="write"', table)
        self.assertIn(json.dumps(str(self.main))+'="read"', table)
        network = next(x for x in args if x.startswith('permissions.dev-workspace.network='))
        self.assertIn('\"unix_sockets\"={}', network)
        self.assertIn('\"dangerously_allow_all_unix_sockets\"=false', network)

    def test_invalid_policy_blocks_before_native_or_gate(self):
        for config in ['not toml [', '[permissions.dev-workspace]\nextends="missing"\n',
                       '[permissions.dev-workspace]\nextends="dev-workspace"\n']:
            with self.subTest(config=config):
                self.config.write_text(config)
                self.log.unlink(missing_ok=True)
                r = self.gate('touch host-marker')
                self.assertIn('Sandbox startup failed', json.loads(r.stdout)['reason'])
                self.assertNotIn('sandbox', json.loads(self.log.read_text()) if self.log.exists() else [])
                self.assertFalse((self.feature/'host-marker').exists())

    def test_native_resolver_keeps_managed_filesystem_rules(self):
        denied = self.main / 'managed-secret'
        self.env['PROBE_MDM_DENY'] = str(denied)
        write(denied, 'managed-secret')
        result = self.launch(sys.executable, '-I', '-c',
                             'from pathlib import Path; import sys; '
                             '\ntry: Path(sys.argv[1]).read_text()\nexcept PermissionError: pass\nelse: raise AssertionError("managed deny lost")', str(denied))
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text())
        if os.environ.get('DEVLOOP_MACOS_BROKER'):
            self.assertIn('app-server', args)
            self.assertEqual(args[args.index('-C')+1], str(self.main))
            return
        table = next(x for x in args if x.startswith('permissions.dev-workspace.filesystem='))
        self.assertIn(json.dumps(str(denied))+'="deny"', table)

    def test_native_policy_errors_and_admin_rejection_block(self):
        for key in ['PROBE_POLICY_BAD', 'PROBE_ADMIN_DENY']:
            with self.subTest(key=key):
                self.env[key] = '1'
                result = self.gate('touch host-marker')
                self.assertIn('Sandbox startup failed', json.loads(result.stdout)['reason'])
                self.assertFalse((self.feature/'host-marker').exists())
                del self.env[key]

    def test_launch_cleans_start_marker_after_success_or_failure(self):
        for early in ['', '1']:
            with self.subTest(early=early):
                self.env['PROBE_EARLY'] = early
                result = self.launch('/bin/sh', '-c', 'printf "%s" "$TMPDIR"')
                self.assertEqual(result.returncode, 125 if early else 0, result.stderr)
                if not early:
                    self.assertFalse(Path(result.stdout).exists())
                else:
                    self.assertEqual(result.stdout, '')

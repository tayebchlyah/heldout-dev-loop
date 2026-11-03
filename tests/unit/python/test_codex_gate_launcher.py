"""Automatic Codex gate launch through the public hook and native CLI boundary."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from support import CODEX_ASSETS, CODEX_GATE_FIXTURE, darwin_temp_dir, write

sys.dont_write_bytecode = True
sys.path.insert(0, str(CODEX_ASSETS / 'hooks'))
import sandbox


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

    def test_gate_output_reaches_the_reason_through_no_path_the_gate_can_swap(self):
        secret = self.root / 'secret'
        secret.write_text('DUMMY=canary')
        write(self.feature / 'swap.py', 'import fcntl, os, sys\n'
              'try: path = os.readlink("/proc/self/fd/1") if sys.platform == "linux" else fcntl.fcntl(1, 50, bytes(1024)).split(b"\\0")[0].decode()\n'
              'except OSError: path = ""\n'
              'if os.path.isfile(path) and not os.path.islink(path): os.unlink(path); os.symlink(sys.argv[1], path)\n'
              'print("gate-out")\nsys.exit(1)\n')
        reason = json.loads(self.gate(shlex.join([sys.executable, '-I', 'swap.py', str(secret)])).stdout)['reason']
        self.assertNotIn('canary', reason)
        self.assertIn('gate-out', reason)

    def test_kit_metadata_stays_read_only_when_the_hook_cwd_is_the_main_checkout(self):
        table, _ = sandbox.neutralised({}, sandbox.filesystem({}, self.main, self.main), (self.main, self.main))
        for name in ('.git', '.codex', '.agents', '.dev-loop'):
            with self.subTest(name=name):
                self.assertEqual(table.get(str(self.main / name)), 'read')

    def test_gate_command_reads_end_of_input(self):
        r = self.gate('echo "stdin-bytes=$(cat | wc -c | tr -d " ")"; exit 7')
        self.assertIn('stdin-bytes=0', json.loads(r.stdout)['reason'])

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
            roots.append(darwin_temp_dir())
        self.assertTrue(any(Path(r.stdout).resolve().is_relative_to(root) for root in roots), r.stdout)

    def test_short_argument_lists_block_without_a_traceback(self):
        for argv in [[], [str(self.main)], [str(self.main), str(self.feature)], [str(self.main), str(self.feature), '--']]:
            with self.subTest(argv=argv):
                r = subprocess.run([sys.executable, '-I', str(CODEX_ASSETS / 'hooks/sandbox.py'), *argv],
                                   input='', text=True, capture_output=True, env=self.env)
                self.assertEqual(r.returncode, 125, r.stderr)
                self.assertIn('Sandbox startup failed', r.stderr)
                self.assertNotIn('Traceback', r.stderr)

    def test_early_launch_failure_blocks_without_host_fallback(self):
        self.env['PROBE_EARLY'] = '1'
        marker = self.feature / 'host-marker'
        r = self.gate('touch host-marker')
        self.assertIn('Sandbox startup failed', json.loads(r.stdout)['reason'])
        self.assertFalse(marker.exists())

    def test_startup_failure_reason_differs_from_failed_gate(self):
        self.env['PROBE_EARLY'] = '1'
        startup = json.loads(self.gate('exit 0').stdout)['reason']
        self.assertIn('sandbox could not start', startup)
        self.assertIn('Sandbox startup failed', startup)
        self.assertNotIn('Gate failed', startup)
        del self.env['PROBE_EARLY']
        failed = json.loads(self.gate('exit 7').stdout)['reason']
        self.assertTrue(failed.startswith('Gate failed. Fix it. Do not weaken or skip tests.'))

    def test_launch_loads_project_only_profile_from_trusted_main_proof(self):
        bootstrap = ('import os,sys\nfrom pathlib import Path\n'
                     'if "app-server" in sys.argv and Path.cwd()!=Path(os.environ["PROBE_TRUSTED_CWD"]):\n'
                     ' print("untrusted startup directory",file=sys.stderr);sys.exit(86)\n')
        write(self.bin / 'codex', '#!' + sys.executable + '\n' + bootstrap + CODEX_GATE_FIXTURE)
        self.env['PROBE_TRUSTED_CWD'] = str(self.main)
        result = subprocess.run([sys.executable, '-I', str(CODEX_ASSETS / 'hooks/sandbox.py'),
                                 str(self.main), str(self.feature), '--', sys.executable, '-I', '-c',
                                 'import json,os,sys; print(json.dumps([os.getcwd(),sys.stdin.read()])); sys.exit(19)'],
                                cwd=self.feature, input='feature stdin', env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(self.feature), 'feature stdin'])

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
        self.env['PROBE_PROXY'] = 'http://127.0.0.1:9'
        self.config.write_text('[permissions.parent]\nextends=":workspace"\n'
                               '[permissions.parent.filesystem]\n'+json.dumps(str(self.root/'outside'))+'="write"\n'
                               +json.dumps(str(self.main/'secret'))+'="deny"\n'
                               '[permissions.parent.filesystem.":workspace_roots"]\n"**/.private"="deny"\n'
                               '[permissions.dev-workspace]\nextends="parent"\n'
                               '[permissions.dev-workspace.network]\nenabled=true\nmode="limited"\nallowed_domains=["example.invalid"]\n')
        write(self.feature/'.codex/config.toml', '[permissions.dev-workspace]\nextends=":full-access"\n')
        write(self.main/'secret', 'trusted-secret')
        write(self.feature/'.private', 'feature-secret')
        probe = ('from pathlib import Path; import sys; '
                 '\nfor path in sys.argv[1:]:\n'
                 ' try: Path(path).read_text()\n except PermissionError: pass\n else: raise AssertionError(path)\n'
                 ) if sys.platform == 'darwin' else 'pass'
        r = self.launch(sys.executable, '-I', '-c', probe,
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
        self.assertIn('\"mode\"=\"limited\"', network)
        self.assertIn('\"allowed_domains\"=[\"example.invalid\"]', network)
        self.assertIn('\"unix_sockets\"={}', network)
        self.assertIn('\"dangerously_allow_all_unix_sockets\"=false', network)

    def test_launch_neutralises_trusted_write_grants_and_socket_allows(self):
        outside = str(self.root / 'outside')
        self.config.write_text('[permissions.dev-workspace]\nextends=":workspace"\n'
                               '[permissions.dev-workspace.filesystem]\n'+json.dumps(outside)+'="write"\n'
                               '[permissions.dev-workspace.filesystem.":workspace_roots"]\n".git"="write"\n"**/.private"="deny"\n'
                               '[permissions.dev-workspace.network.unix_sockets]\n"/var/run/docker.sock"="allow"\n')
        r = self.launch(sys.executable, '-I', '-c', 'pass')
        self.assertEqual(r.returncode, 0, r.stderr)
        args = json.loads(self.log.read_text())
        if os.environ.get('DEVLOOP_MACOS_BROKER'):
            self.assertIn('app-server', args)
            return
        values = {x.split('=', 1)[0]: tomllib.loads('v=' + x.split('=', 1)[1])['v']
                  for x in args if x.startswith('permissions.dev-workspace.')}
        table = values['permissions.dev-workspace.filesystem']
        self.assertEqual(table[outside], 'read')
        self.assertEqual(table[':workspace_roots'], {'.git': 'read', '**/.private': 'deny'})
        self.assertEqual(values['permissions.dev-workspace.network']['unix_sockets'], {'/var/run/docker.sock': 'deny'})

    def native_route(self, *command, env=None):
        source = ('import sys; sys.dont_write_bytecode=True; sys.platform="linux"; sys.argv=["sandbox.py"]+sys.argv[1:]; '
                  'sys.path.insert(0,sys.argv[1]); import sandbox; sys.argv.pop(1); sys.exit(sandbox.main())')
        return subprocess.run([sys.executable, '-I', '-c', source, str(CODEX_ASSETS / 'hooks'),
                               str(self.main), str(self.feature), '--', *command],
                              input='', text=True, capture_output=True, env={**self.env, **(env or {})})

    def test_native_route_sets_gate_scope_under_core_environment(self):
        bootstrap = 'import os,sys\nif "sandbox" in sys.argv: os.environ.pop("GATE_SCOPE",None)\n'
        write(self.bin / 'codex', '#!' + sys.executable + '\n' + bootstrap + CODEX_GATE_FIXTURE)
        for scope in ('changed', 'full'):
            with self.subTest(scope=scope):
                r = self.native_route('/bin/sh', '-c', 'printf "%s" "$GATE_SCOPE"', env={'GATE_SCOPE': scope})
                self.assertEqual((r.returncode, r.stdout), (0, scope), r.stderr)

    @unittest.skipUnless(sys.platform == 'darwin', 'Seatbelt path quoting')
    def test_unquotable_feature_paths_never_expose_env_files(self):
        for index, name in enumerate(['tab\there', 'quote"here', 'caret^here', 'ctl\x01here']):
            with self.subTest(name=name):
                self.feature = self.root / name
                self.git(self.main, 'worktree', 'add', '-qb', 'fix/q%d' % index, str(self.feature))
                for secret in ('.env', '.env.local'):
                    write(self.feature / secret, 'SECRET')
                r = self.launch('/bin/sh', '-c', 'cat .env .env.local')
                self.assertNotIn('SECRET', r.stdout)
                self.assertEqual(r.returncode, 125, r.stderr)
                self.assertIn('Sandbox startup failed', r.stderr)

    def test_launch_keeps_trusted_home_deny_rules(self):
        decoy = self.root / 'home-like/x/secret'
        write(decoy, 'decoy')
        self.env['HOME'] = str(self.root / 'home-like')
        self.config.write_text('[permissions.dev-workspace]\nextends=":workspace"\n'
                               '[permissions.dev-workspace.filesystem]\n"~/x/secret"="deny"\n')
        probe = ('from pathlib import Path; import sys; '
                 '\ntry: Path(sys.argv[1]).read_text()\nexcept PermissionError: pass\nelse: raise AssertionError("home deny lost")'
                 ) if sys.platform == 'darwin' else 'pass'
        r = self.launch(sys.executable, '-I', '-c', probe, str(decoy))
        self.assertEqual(r.returncode, 0, r.stderr)
        args = json.loads(self.log.read_text())
        if os.environ.get('DEVLOOP_MACOS_BROKER'):
            return
        table = next(x for x in args if x.startswith('permissions.dev-workspace.filesystem='))
        self.assertIn(json.dumps(str(decoy))+'="deny"', table)

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
        probe = ('from pathlib import Path; import sys; '
                 '\ntry: Path(sys.argv[1]).read_text()\nexcept PermissionError: pass\nelse: raise AssertionError("managed deny lost")'
                 ) if sys.platform == 'darwin' else 'pass'
        result = self.launch(sys.executable, '-I', '-c', probe, str(denied))
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


def native_codex():
    found = shutil.which('codex')
    if found:
        version = subprocess.run([found, '--version'], capture_output=True, text=True).stdout
        match = re.search(r'codex-cli (\d+)\.(\d+)\.(\d+)', version)
        if match and tuple(map(int, match.groups())) >= (0, 166, 0):
            return found


@unittest.skipUnless(native_codex(), 'native codex-cli 0.166.0 or later required')
@unittest.skipIf(os.environ.get('DEVLOOP_MACOS_BROKER'), 'macOS refuses a nested sandbox_apply inside the hook sandbox')
class NativeCodexPolicyTests(unittest.TestCase):
    """The table that sandbox.py hands to the real `codex sandbox` is the whole effective policy."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='devloop-native-', dir='/tmp')).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.main, self.feature, self.outside = (self.root / n for n in ('main', 'feature', 'outside'))
        for path in (self.main, self.feature, self.outside, self.root / 'home'):
            path.mkdir()
        subprocess.run(['git', '-C', str(self.main), 'init', '-q'], check=True)

    def confined(self, profile, trusted_toml, *command):
        (self.root / 'home/config.toml').write_text('[permissions.dev-workspace]\nextends=":workspace"\n' + trusted_toml)
        table, sockets = sandbox.neutralised(profile, sandbox.filesystem(profile, self.main, self.feature),
                                            (self.main, self.feature))
        args = sandbox.native_args(native_codex(), self.main, {
            'permissions.dev-workspace.extends': ':read-only',
            'permissions.dev-workspace.filesystem': table,
            'permissions.dev-workspace.network': {'unix_sockets': sockets}})
        return subprocess.run([*args, '--', *command], capture_output=True, text=True, timeout=60,
                              env={**os.environ, 'CODEX_HOME': str(self.root / 'home')})

    def test_feature_metadata_is_not_writable_under_a_write_root(self):
        for name in ('.git', '.codex', '.agents'):
            (self.feature / name).mkdir()
            (self.feature / name / 'config').write_text('base')
        for target, allowed in [(self.feature / '.git/config', False), (self.feature / '.codex/config', False),
                                (self.feature / '.agents/config', False), (self.main / '.git/config', False),
                                (self.feature / 'ok', True)]:
            with self.subTest(target=str(target)):
                result = self.confined({}, '', '/bin/sh', '-c', 'echo changed > "$1"', 'probe', str(target))
                self.assertEqual(result.returncode, 0 if allowed else 1, result.stderr)
                self.assertEqual(target.read_text().strip() == 'changed' if target.exists() else False, allowed)

    def test_trusted_write_grants_do_not_survive(self):
        profile = {'filesystem': {str(self.outside): 'write', ':workspace_roots': {'.git': 'write'}}}
        trusted = ('[permissions.dev-workspace.filesystem]\n' + json.dumps(str(self.outside)) + '="write"\n'
                   '[permissions.dev-workspace.filesystem.":workspace_roots"]\n".git"="write"\n')
        for target, allowed in [(self.outside / 'probe', False), (self.main / '.git/probe', False),
                                (self.feature / 'ok', True)]:
            with self.subTest(target=str(target)):
                result = self.confined(profile, trusted, 'touch', str(target))
                self.assertEqual(result.returncode, 0 if allowed else 1, result.stderr)
                self.assertEqual(target.exists(), allowed)

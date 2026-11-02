"""macOS gate adapter proofs through the public run interface."""
from __future__ import annotations

import base64
import errno
import json
import os
import subprocess
import socket
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from support import CODEX_ASSETS, darwin_temp_dir

sys.dont_write_bytecode = True
sys.path.insert(0, str(CODEX_ASSETS / 'hooks'))
import macos


BROKER = 'DEVLOOP_MACOS_BROKER'


@unittest.skipUnless(sys.platform == 'darwin', 'macOS Seatbelt proof')
class MacosHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.feature = self.root / 'feature'
        self.feature.mkdir()
        self.main = self.root / 'trusted'
        self.common = self.root / 'common-metadata'
        subprocess.run(['git', 'init', '-q', '--separate-git-dir', str(self.common), str(self.main)], check=True)
        self.fixture = self.root / 'native.py'
        self.fixture.write_text('import subprocess,sys\na=sys.argv[1:]; sys.exit(subprocess.call(a[a.index("--")+1:]))\n')
        self.rules = {'/': 'read', str(self.feature): 'write'}

    def launch(self, code, rules=None, keeper=None, network=False, local=False, setup="", top_level=False):
        source = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0,sys.argv[1]); import macos; '
                  + setup + '\n'
                  'sys.exit(macos.run(__import__("json").loads(sys.argv[2]),__import__("pathlib").Path(sys.argv[8]),'
                  '__import__("json").loads(sys.argv[3]),__import__("pathlib").Path(sys.argv[4]),'
                  '[sys.executable,"-I","-c",sys.argv[5]],sys.argv[6]=="1",sys.argv[7]=="1"))')
        return subprocess.run([sys.executable, '-I', '-c', source, str(CODEX_ASSETS / 'hooks'),
                               json.dumps(keeper or [sys.executable, '-I', str(self.fixture), '-C', str(self.main)]),
                               json.dumps(rules or self.rules), str(self.feature), code, str(int(network)), str(int(local)), str(self.main)],
                              input='gate input', capture_output=True, text=True, timeout=15,
                              env={k: v for k, v in os.environ.items() if not (top_level and k == BROKER)})

    def test_run_filesystem_and_exit_proof(self):
        secret = self.root / '.env'
        secret.write_text('secret')
        rules = {**self.rules, str(secret): 'deny'}
        code = ('import pathlib,sys; p=pathlib.Path; p("allowed").write_text("yes"); '
                f'\nfor name in {list(map(str, [secret, self.root / "outside"]))!r}:\n'
                ' try:\n  p(name).write_text("escape")\n except PermissionError: pass\n else: raise AssertionError(name)\n'
                'print(sys.stdin.read()); sys.exit(19)')
        result = self.launch(code, rules)
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual(result.stdout, 'gate input\n')
        self.assertEqual((self.feature / 'allowed').read_text(), 'yes')
        self.assertEqual(secret.read_text(), 'secret')

        temporary_root = Path(tempfile.gettempdir()).resolve()
        alias = self.root / 'temporary-alias'
        alias.symlink_to(temporary_root, target_is_directory=True)
        target = temporary_root / ('devloop-readonly-' + str(os.getpid()))
        self.addCleanup(target.unlink, missing_ok=True)
        darwin_temp = darwin_temp_dir()
        for extra, allowed, blocked in [({str(temporary_root): 'write', str(alias): 'read'}, False, False),
                               ({str(alias): 'read', str(temporary_root): 'write'}, False, False),
                               ({str(temporary_root): 'write', str(alias): 'deny'}, False, True),
                               ({str(temporary_root): 'read'}, False, False),
                               ({str(temporary_root.parent): 'read', str(temporary_root): 'write'}, True, False),
                               ({str(Path('/tmp').resolve()): 'read', str(darwin_temp): 'read'}, False, True)]:
            with self.subTest(rules=extra, allowed=allowed):
                target.unlink(missing_ok=True)
                code = ('from pathlib import Path; Path("nested-allowed").write_text("yes")\n'
                        f'try: Path({str(target)!r}).write_text("probe")\n'
                        f'except PermissionError: assert not {allowed!r}\n'
                        f'else: assert {allowed!r}, "readonly alias writable"')
                result = self.launch(code, {**self.rules, **extra})
                if blocked:
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(target.exists(), allowed)

    def test_run_globs_metadata_and_nested_read_proof(self):
        main = self.root / 'main'
        main.mkdir()
        nested = main / 'nested'
        nested.mkdir()
        for name in ('.git', '.agents', '.codex', '.aws'):
            (nested / name).mkdir()
            (nested / name / 'config').write_text('base')
        (nested / '.env.private').write_text('secret')
        (nested / '.private').write_text('secret')
        protected = self.common / 'concurrent-helper'
        protected.write_text('trusted')
        metadata_alias = self.feature / 'metadata-alias'
        metadata_alias.symlink_to(self.common, target_is_directory=True)
        child = nested / 'child'
        child.mkdir()
        (child / '.env.secret').write_text('secret')
        (main / 'base').write_text('base')
        alias = nested / 'alias'
        alias.symlink_to(nested / '.env.private')
        rules = {'/': 'read', str(self.root): 'write', str(main): 'read',
                 str(nested): 'write', str(nested / '.env.*'): 'deny',
                 str(main / '**/.private'): 'deny', str(main / '**/.env.*'): 'deny'}
        code = ('from pathlib import Path; '
                f'n=Path({str(nested)!r}); (n/"allowed").write_text("yes")\n'
                f'for name in {[str(main / "base"), *[str(nested / name / "config") for name in (".git", ".agents", ".codex", ".aws")], str(alias), str(nested / ".private"), str(child / ".env.secret"), str(protected), str(metadata_alias / "concurrent-helper")]!r}:\n'
                ' try:\n  Path(name).write_text("escape")\n except PermissionError: pass\n else: raise AssertionError(name)\n')
        result = self.launch(code, rules)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(protected.read_text(), 'trusted')
        for denied_path in (self.common, metadata_alias):
            with self.subTest(denied_metadata=str(denied_path)):
                (self.feature / 'metadata-disclosed').unlink(missing_ok=True)
                code = ('from pathlib import Path\n'
                        f'try: Path({str(protected)!r}).read_text()\n'
                        'except PermissionError: pass\n'
                        'else: Path("metadata-disclosed").touch()')
                result = self.launch(code, {**self.rules, str(denied_path): 'deny'})
                self.assertFalse((self.feature / 'metadata-disclosed').exists(), result.stderr)

    def test_run_rejects_keeper_failure_and_nonlocal_proxy_proof(self):
        for body in ['raise SystemExit(19)',
                     *[('import json,time; print(json.dumps({"version":1,"env":' + repr(env) + '}),flush=True); time.sleep(30)')
                       for env in [{'HTTP_PROXY': 'http://example.com:80'}, {'HTTP_PROXY': 'http://127.0.0.1:80/path'},
                                   {'HTTP_PROXY': 'http://127.0.0.1:80?query'}, {'x': 'x' * 70000}]]]:
            with self.subTest(body=body):
                self.fixture.write_text(body)
                result = self.launch('from pathlib import Path; Path("escaped").touch()', network=True, top_level=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.feature / 'escaped').exists())

    @unittest.skipIf(BROKER in os.environ, 'the gate needs a top-level Seatbelt launch')
    def test_run_checks_the_proxy_only_when_the_network_is_enabled_proof(self):
        self.fixture.write_text('import json,time; print(json.dumps({"version":1,"env":{"HTTP_PROXY":"http://example.com:80"}}),flush=True); time.sleep(30)')
        for network, started in [(False, True), (True, False)]:
            with self.subTest(network=network):
                (self.feature / 'ran').unlink(missing_ok=True)
                result = self.launch('from pathlib import Path; Path("ran").touch()', network=network)
                self.assertEqual(result.returncode == 0, started, result.stderr)
                self.assertEqual((self.feature / 'ran').exists(), started)

    def test_run_native_environment_snapshot_proof(self):
        self.fixture.write_text('import os,subprocess,sys\n'
                                'os.environ.pop("DEVLOOP_DUMMY_EXCLUDED",None)\n'
                                'os.environ["DEVLOOP_DUMMY_SET"]="native-value"\n'
                                'a=sys.argv[1:]; sys.exit(subprocess.call(a[a.index("--")+1:]))\n')
        native = BROKER not in os.environ
        code = ('import os; '
                f'assert ("DEVLOOP_DUMMY_EXCLUDED" not in os.environ) is {native}; '
                f'assert os.environ["DEVLOOP_DUMMY_SET"]=="{"native" if native else "host"}-value"')
        with patch.dict(os.environ, {'DEVLOOP_DUMMY_EXCLUDED': 'host-value', 'DEVLOOP_DUMMY_SET': 'host-value'}):
            result = self.launch(code)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_passes_the_received_gate_scope_through_proof(self):
        self.fixture.write_text('import os,subprocess,sys\n'
                                'os.environ.pop("GATE_SCOPE",None)\n'
                                'a=sys.argv[1:]; sys.exit(subprocess.call(a[a.index("--")+1:]))\n')
        for scope in ('changed', 'full', None):
            with self.subTest(scope=scope), patch.dict(os.environ):
                os.environ.pop('GATE_SCOPE', None)
                if scope:
                    os.environ['GATE_SCOPE'] = scope
                result = self.launch(f'import os; assert os.environ.get("GATE_SCOPE") == {scope!r}')
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_process_listing_hides_environment_proof(self):
        result = self.launch('import os,subprocess; marker="DEVLOOP_"+"ENV_SECRET"; '
                             'os.environ[marker]="secret"; '
                             'child=subprocess.Popen(["/bin/sleep","20"]); '
                             'p=subprocess.run(["ps","eww","-p",str(child.pid)],capture_output=True,text=True); '
                             'child.terminate(); child.wait(); assert marker not in p.stdout; '
                             'p=subprocess.run(["ps","-axo","pid=,command="],capture_output=True,text=True); '
                             'assert p.returncode==0,p.stderr; assert str(os.getpid()) in p.stdout')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_nested_ceiling_and_streams_proof(self):
        secret = self.feature / '.env'
        secret.write_text('secret')
        readonly = self.feature / 'readonly'
        readonly.mkdir()
        denied = self.feature / 'child-secret'
        denied.write_text('child secret')
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        checks = ('import pathlib,shutil,socket,sys; p=pathlib.Path; '
                  'p("allowed").write_text("nested"); '
                  f'\nfor name,read in {[(str(secret), True), (str(denied), True), (str(self.root / "outside"), False), (str(readonly / "escape"), False)]!r}:\n'
                  ' try:\n  p(name).read_text() if read else p(name).write_text("escape")\n'
                  ' except PermissionError: pass\n else: raise AssertionError(name)\n'
                  'try: p(shutil.which("ps")).write_text("replace trusted helper")\n'
                  'except PermissionError: pass\nelse: raise AssertionError("signed ps mutable")\n'
                  's=socket.socket(socket.AF_UNIX)\n'
                  'try: s.bind("nested.sock")\n'
                  'except PermissionError: pass\nelse: raise AssertionError("unix socket")\n'
                  f'try: socket.create_connection(("127.0.0.1",{port}),timeout=1)\n'
                  'except PermissionError: pass\nelse: raise AssertionError("network ceiling")\n'
                  'print(sys.stdin.read()); print("nested stderr",file=sys.stderr); sys.exit(19)')
        descendant = ('import sys; sys.dont_write_bytecode=True; '
                      f'sys.path.insert(0,{str(CODEX_ASSETS / "hooks")!r}); import macos; '
                      f'sys.exit(macos.run([], __import__("pathlib").Path({str(self.main)!r}), {{"/":"write"}}, __import__("pathlib").Path({str(self.feature)!r}), '
                      f'[sys.executable,"-I","-c",{checks!r}],True,True))')
        request = ('import sys; sys.dont_write_bytecode=True; '
                   f'sys.path.insert(0,{str(CODEX_ASSETS / "hooks")!r}); import macos; '
                   f'sys.exit(macos.run([], __import__("pathlib").Path({str(self.main)!r}), { {"/": "write", str(readonly): "read", str(denied): "deny"}!r}, '
                   f'__import__("pathlib").Path({str(self.feature)!r}), '
                   f'[sys.executable,"-I","-c",{descendant!r}],False,False))')
        result = self.launch(request, {**self.rules, str(secret): 'deny'})
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual(result.stdout, 'gate input\n')
        self.assertEqual(result.stderr, 'nested stderr\n')

    def test_run_cleans_descendants_after_leader_exit_proof(self):
        marker = self.feature / 'descendant-group'
        child = ('import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                 f'open({str(marker)!r},"w").write(str(os.getpgrp())); time.sleep(60)')
        code = ('import subprocess,sys,time; '
                f'subprocess.Popen([sys.executable,"-I","-c",{child!r}]); '
                f'\nwhile not __import__("pathlib").Path({str(marker)!r}).exists(): time.sleep(.01)\n')
        nested = ('import sys; sys.dont_write_bytecode=True; '
                  f'sys.path.insert(0,{str(CODEX_ASSETS / "hooks")!r}); import macos; '
                  f'sys.exit(macos.run([],__import__("pathlib").Path({str(self.main)!r}),{{"/":"write"}},__import__("pathlib").Path({str(self.feature)!r}),'
                  f'[sys.executable,"-I","-c",{code!r}],False,False))')
        for command, permission_failure in [(code, 0), (nested, 0), (code, 1), (code, 2)]:
            with self.subTest(nested=command == nested, permission_failure=permission_failure):
                marker.unlink(missing_ok=True)
                setup = ('\nimport os,signal\nfrom unittest.mock import patch\n'
                         'original=os.killpg\ncalls=0\n'
                         'def group_signal(pid,signum):\n'
                         ' global calls\n'
                         ' if signum==signal.SIGTERM:\n'
                         '  calls+=1\n'
                         f'  if calls=={permission_failure}: raise PermissionError(1,"Darwin group permission")\n'
                         ' return original(pid,signum)\n'
                         'patch("os.killpg",group_signal).start()\n') if permission_failure else ''
                try:
                    result = self.launch(command, setup=setup)
                    if permission_failure == 2 and 'DEVLOOP_MACOS_BROKER' not in os.environ:
                        self.assertNotEqual(result.returncode, 0, result.stderr)
                        self.assertIn('PermissionError', result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                finally:
                    if marker.exists():
                        try:
                            os.killpg(int(marker.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_run_network_proxy_local_binding_and_unix_proof(self):
        with socket.socket() as proxy, socket.socket() as other, socket.socket(socket.AF_UNIX) as unix:
            for listener in (proxy, other):
                listener.bind(('127.0.0.1', 0))
                listener.listen()
            proxy_port, other_port = proxy.getsockname()[1], other.getsockname()[1]
            unix_temp = tempfile.TemporaryDirectory(prefix='dl-', dir='/tmp')
            self.addCleanup(unix_temp.cleanup)
            unix_path = str(Path(unix_temp.name) / 'docker.sock')
            # allow-comment: dependency: macOS can return ENOENT for a missing socket; child bind below proves denial.
            positive_unix = True
            try:
                unix.bind(unix_path)
                unix.listen()
            except PermissionError as error:
                self.assertIn(error.errno, (errno.EPERM, errno.EACCES))
                positive_unix = False
            if 'DEVLOOP_MACOS_BROKER' in os.environ:
                proxy_port = urlsplit(os.environ['HTTP_PROXY']).port
            self.fixture.write_text('import os,subprocess,sys\n'
                                    f'os.environ["HTTP_PROXY"]="http://127.0.0.1:{proxy_port}"\n'
                                    'a=sys.argv[1:]; sys.exit(subprocess.call(a[a.index("--")+1:]))\n')
            for enabled, local in [(False, False), (True, False), (True, True)]:
                with self.subTest(enabled=enabled, local=local):
                    code = ('import socket\n'
                            f'for port,allowed in {[(proxy_port, enabled), (other_port, enabled and local)]!r}:\n'
                            ' try:\n  s=socket.create_connection(("127.0.0.1",port),timeout=1); s.close()\n'
                            ' except OSError:\n  assert not allowed,port\n'
                            ' else: assert allowed,port\n'
                            's=socket.socket(socket.AF_UNIX)\n'
                            f'try: s.connect({unix_path!r})\n'
                            f'except OSError as error: assert error.errno in {([errno.EPERM, errno.EACCES] if positive_unix else [errno.EPERM, errno.EACCES, errno.ENOENT])!r}\n'
                            'else: raise AssertionError("unix socket")\n'
                            's=socket.socket(socket.AF_UNIX)\n'
                            'try: s.bind("forbidden.sock")\n'
                            'except PermissionError: pass\nelse: raise AssertionError("unix bind")\n'
                            's=socket.socket()\n'
                            'try: s.bind(("127.0.0.1",0))\n'
                            f'except OSError: assert not {enabled and local!r}\n'
                            f'else: assert {enabled and local!r}\n')
                    result = self.launch(code, network=enabled, local=local)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_local_binding_denies_remote_connect_proof(self):
        with socket.socket() as proxy:
            proxy.bind(('127.0.0.1', 0))
            proxy.listen()
            port = proxy.getsockname()[1]
            if 'DEVLOOP_MACOS_BROKER' in os.environ:
                port = urlsplit(os.environ['HTTP_PROXY']).port
            self.fixture.write_text('import os,subprocess,sys\n'
                                    f'os.environ["HTTP_PROXY"]="http://127.0.0.1:{port}"\n'
                                    'a=sys.argv[1:]; sys.exit(subprocess.call(a[a.index("--")+1:]))\n')
            code = ('import errno,socket\n'
                    'try: socket.create_connection(("192.0.2.1",9),timeout=2)\n'
                    'except OSError as error: assert error.errno==errno.EPERM,error\n'
                    'else: raise AssertionError("remote connect")\n'
                    'server=socket.socket(); server.bind(("127.0.0.1",0)); server.listen()\n'
                    'socket.create_connection(server.getsockname(),timeout=2).close()\n')
            result = self.launch(code, network=True, local=True)
            self.assertEqual(result.returncode, 0, result.stderr)


    def ask(self, server, token, command):
        frame = {'token': token, 'rules': {'/': 'read'}, 'cwd': str(self.feature), 'command': command,
                 'network': False, 'local': False, 'env': {'PATH': os.defpath}, 'input': ''}
        with socket.create_connection(('127.0.0.1', server.port), timeout=10) as connection, \
                connection.makefile('rwb') as stream:
            stream.write(json.dumps(frame).encode() + b'\n')
            stream.flush()
            return [json.loads(line) for line in stream]

    def serve(self):
        self.root.joinpath('bin').mkdir(exist_ok=True)
        return macos.Server({'/': 'read'}, {}, set(), False, False, self.root / 'bin')

    def test_server_rejects_an_unknown_token_proof(self):
        with self.serve() as server:
            self.assertEqual(self.ask(server, '0' * 64, ['/usr/bin/true']), [{'error': 'confined launch failed'}])

    @unittest.skipIf(BROKER in os.environ, 'the sibling needs a top-level Seatbelt launch')
    def test_server_rejects_a_closed_token_proof(self):
        with self.serve() as server:
            granted = json.loads(server.context)['token']
            first = self.ask(server, granted, ['/bin/sh', '-c', 'printf %s "$' + BROKER + '"'])
            self.assertEqual(first[-1], {'exit': 0}, first)
            child = json.loads(base64.b64decode(first[0]['data']))['token']
            self.assertNotEqual(child, granted)
            self.assertEqual(self.ask(server, child, ['/usr/bin/true']), [{'error': 'confined launch failed'}])


if __name__ == '__main__':
    unittest.main()

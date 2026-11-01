"""macOS gate adapter proofs through the public run interface."""
from __future__ import annotations

import errno
import json
import os
import subprocess
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from support import CODEX_ASSETS


@unittest.skipUnless(sys.platform == 'darwin', 'macOS Seatbelt proof')
class MacosHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.feature = self.root / 'feature'
        self.feature.mkdir()
        self.fixture = self.root / 'native.py'
        self.fixture.write_text('import subprocess,sys\na=sys.argv[1:]; sys.exit(subprocess.call(a[a.index("--")+1:]))\n')
        self.rules = {'/': 'read', str(self.feature): 'write'}

    def launch(self, code, rules=None, keeper=None, network=False, local=False):
        source = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0,sys.argv[1]); import macos; '
                  'sys.exit(macos.run(__import__("json").loads(sys.argv[2]),'
                  '__import__("json").loads(sys.argv[3]),__import__("pathlib").Path(sys.argv[4]),'
                  '[sys.executable,"-I","-c",sys.argv[5]],sys.argv[6]=="1",sys.argv[7]=="1"))')
        return subprocess.run([sys.executable, '-I', '-c', source, str(CODEX_ASSETS / 'hooks'),
                               json.dumps(keeper or [sys.executable, '-I', str(self.fixture)]),
                               json.dumps(rules or self.rules), str(self.feature), code, str(int(network)), str(int(local))],
                              input='gate input', capture_output=True, text=True)

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
        marker = self.feature / 'must-not-start'
        for extra, allowed in [({str(temporary_root): 'read'}, False),
                               ({str(alias): 'read'}, False),
                               ({str(temporary_root): 'deny'}, False),
                               ({str(temporary_root.parent): 'read', str(temporary_root): 'write'}, True)]:
            with self.subTest(rules=extra, allowed=allowed):
                marker.unlink(missing_ok=True)
                result = self.launch('from pathlib import Path; Path("must-not-start").touch()',
                                     {**self.rules, **extra})
                self.assertEqual(result.returncode == 0, allowed, result.stderr)
                self.assertEqual(marker.exists(), allowed, 'project command did not respect the temporary-root limit')

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
                f'for name in {[str(main / "base"), *[str(nested / name / "config") for name in (".git", ".agents", ".codex", ".aws")], str(alias), str(nested / ".private"), str(child / ".env.secret")]!r}:\n'
                ' try:\n  Path(name).write_text("escape")\n except PermissionError: pass\n else: raise AssertionError(name)\n')
        result = self.launch(code, rules)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_rejects_keeper_failure_and_nonlocal_proxy_proof(self):
        for body in ['raise SystemExit(19)',
                     'import json,time; print(json.dumps({"version":1,"env":{"HTTP_PROXY":"http://example.com:80"}}),flush=True); time.sleep(30)']:
            with self.subTest(body=body):
                self.fixture.write_text(body)
                if 'DEVLOOP_MACOS_BROKER' in os.environ:
                    with patch.dict(os.environ, {'DEVLOOP_MACOS_BROKER': '{}'}):
                        result = self.launch('from pathlib import Path; Path("escaped").touch()')
                else:
                    result = self.launch('from pathlib import Path; Path("escaped").touch()')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.feature / 'escaped').exists())

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
                      f'sys.exit(macos.run([], {{"/":"write"}}, __import__("pathlib").Path({str(self.feature)!r}), '
                      f'[sys.executable,"-I","-c",{checks!r}],True,True))')
        request = ('import sys; sys.dont_write_bytecode=True; '
                   f'sys.path.insert(0,{str(CODEX_ASSETS / "hooks")!r}); import macos; '
                   f'sys.exit(macos.run([], { {"/": "write", str(readonly): "read", str(denied): "deny"}!r}, '
                   f'__import__("pathlib").Path({str(self.feature)!r}), '
                   f'[sys.executable,"-I","-c",{descendant!r}],False,False))')
        result = self.launch(request, {**self.rules, str(secret): 'deny'})
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual(result.stdout, 'gate input\n')
        self.assertEqual(result.stderr, 'nested stderr\n')

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


if __name__ == '__main__':
    unittest.main()

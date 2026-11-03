"""Trusted checker execution through Codex hooks with linked feature inputs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import CODEX_ASSETS, SKILL_ROOT, write


class CodexTrustedChecksTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.main, self.feature = self.root/'main', self.root/'feature'
        self.main.mkdir()
        self.git(self.main, 'init', '-qb', 'main')
        write(self.main/'dev-loop.json', '{"version":"0.0.0"}')
        write(self.main/'.gitignore', '.agents/\n.dev-loop/\njson.py\n')
        self.git(self.main, 'add', '.')
        self.git(self.main, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'base')
        self.git(self.main, 'worktree', 'add', '-qb', 'fix/feature', str(self.feature))
        self.log, self.marker = self.root/'calls.jsonl', self.root/'host-marker'
        self.scripts = self.main/'.agents/skills/dev-loop/scripts'
        fake = '''import json,os,sys
with open(os.environ['CHECK_LOG'],'a') as f:
 f.write(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),'isolated':sys.flags.isolated})+'\\n')
if sys.argv[1]=='search-wall': print(json.dumps({'deny':True,'reason':'trusted search denial'}))
elif sys.argv[1]=='seal-check': print(json.dumps({'violations':[],'taint':[]}))
elif sys.argv[1]=='release-check': print(json.dumps({'ok':True}))
'''
        write(self.scripts/'devloop.py', fake)
        write(self.scripts/'check_setup.py', fake.replace("if sys.argv[1]=='search-wall':", "if sys.argv[1]=='--harness': print('stop')\nelif sys.argv[1]=='search-wall':"))
        planted = "from pathlib import Path\nPath("+repr(str(self.marker))+").write_text('feature code')\nraise SystemExit\n"
        for name in ['devloop.py','check_setup.py','json.py']:
            write(self.feature/'.agents/skills/dev-loop/scripts'/name, planted)
        write(self.feature/'json.py', planted)
        write(self.root/'codex/config.toml', f'[hooks.state."{self.main}/.codex/hooks.json:stop:0:0"]\ntrusted_hash = "sha256:0"\n')
        self.env = {**os.environ, 'CHECK_LOG':str(self.log), 'PYTHONPATH':str(self.feature), 'CODEX_HOME':str(self.root/'codex')}
        self.env.pop('DEV_LOOP_NOTES', None)

    def git(self, cwd, *args):
        return subprocess.check_output(['git','-C',str(cwd),*args],text=True,stderr=subprocess.DEVNULL)

    def hook(self, script, *args, **extra):
        payload={'cwd':str(self.feature), **extra}
        return subprocess.run(['bash' if script == 'git-guard.sh' else 'sh',str(CODEX_ASSETS/'hooks'/script),*args],cwd=self.feature,
                              input=json.dumps(payload),capture_output=True,text=True,env=self.env)

    def calls(self, expected=1):
        end=time.monotonic()+5
        while time.monotonic()<end:
            rows=[json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []
            if len(rows)>=expected: break
            time.sleep(.02)
        self.assertEqual(len(rows), expected)
        self.assertFalse(self.marker.exists())
        for row in rows:
            self.assertEqual(row['cwd'],str(self.feature))
            self.assertEqual(row['isolated'],1)
        return rows

    def test_seal_uses_main_code_and_checks_the_feature_commit(self):
        shutil.rmtree(self.scripts)
        shutil.copytree(SKILL_ROOT/'scripts',self.scripts,ignore=shutil.ignore_patterns('__pycache__'))
        write(self.feature/'tests/acceptance/probe.py','def test_probe(): pass\n')
        self.git(self.feature,'add','tests/acceptance/probe.py')
        self.git(self.feature,'-c','user.name=Test','-c','user.email=test@example.com','commit','-qm','unsealed')
        sha=self.git(self.feature,'rev-parse','HEAD').strip()
        for mode in ['turn','stop']:
            with self.subTest(mode=mode):
                r=self.hook('seal.sh',mode,agent_id='probe',agent_type='implementer')
                self.assertIn('unsealed '+sha,json.loads(r.stdout)['reason'])
                self.assertFalse(self.marker.exists())

    def test_search_uses_main_checker_with_feature_payload(self):
        r=self.hook('acceptance-wall.sh',agent_id='probe',agent_type='implementer',tool_name='Bash',
                    tool_input={'command':'rg needle .'})
        self.assertEqual(r.returncode,2)
        self.assertIn('trusted search denial',r.stderr)
        self.assertEqual(self.calls()[0]['argv'],['search-wall'])

    def test_session_start_uses_main_setup_checker(self):
        r=self.hook('session-start.sh')
        self.assertIn('untrusted dev-loop hooks: stop',r.stdout)
        self.assertEqual(self.calls()[0]['argv'],['--harness','codex','--repo',str(self.feature),'--hooks-trust'])

    def test_board_uses_main_code_with_isolated_python(self):
        r=self.hook('board.sh','prompt')
        self.assertEqual((r.returncode,r.stdout),(0,''))
        self.assertEqual(self.calls()[0]['argv'],['board','--event','prompt'])

    def plant(self, repo):
        planted = (self.feature/'.agents/skills/dev-loop/scripts/devloop.py').read_text()
        for name in ['devloop.py','check_setup.py']:
            write(repo/'.agents/skills/dev-loop/scripts'/name, planted)
        (repo/'.dev-loop/hooks/codex').mkdir(parents=True)
        write(repo/'dev-loop.json', '{"version":"0.0.0"}')
        self.git(repo.parent, 'init', '-q', str(repo))

    def assert_hooks_use_main_code(self, cwd):
        runs = [('seal.sh',('turn',),{}), ('session-start.sh',(),{}), ('board.sh',('prompt',),{}),
                ('acceptance-wall.sh',(),{'agent_id':'probe','agent_type':'implementer','tool_name':'Bash',
                                          'tool_input':{'command':'rg needle .'}})]
        for script, args, extra in runs:
            subprocess.run(['sh',str(CODEX_ASSETS/'hooks'/script),*args],cwd=cwd,input=json.dumps({'cwd':str(cwd),**extra}),
                           capture_output=True,text=True,env=self.env)
        end=time.monotonic()+5
        while time.monotonic()<end and len(self.log.read_text().splitlines() if self.log.exists() else [])<4:
            time.sleep(.02)
        self.assertFalse(self.marker.exists())
        argv=[json.loads(x)['argv'][0] for x in self.log.read_text().splitlines()]
        self.assertEqual(sorted(argv),['--harness','board','seal-check','search-wall'])

    def test_hooks_under_an_agent_made_repo_use_main_code(self):
        """#212, #222: a repo the agent creates, or a gitfile it writes, never selects the checker code."""
        (self.main/'.dev-loop/hooks/codex').mkdir(parents=True)
        link = Path(tempfile.mkdtemp(prefix='dev-loop-link-'))
        self.addCleanup(shutil.rmtree, link, True)
        (link/'root').symlink_to(self.root)
        config = self.root/'codex/config.toml'
        trust = lambda path: f'[hooks.state."{path}/.codex/hooks.json:stop:0:0"]\ntrusted_hash = "sha256:0"\n'
        feature = self.main/'.claude/worktrees/probe'
        self.git(self.main, 'worktree', 'add', '-qb', 'fix/probe', str(feature))
        nested = feature/'packages/web'
        other = self.root/'other'
        def redirect():
            self.plant(other)
            write(self.root/'.git', f'gitdir: {other}/.git\n')
            config.write_text(trust(self.main) + trust(other))
        rows = [
            ('nested repo in the feature', lambda: self.plant(nested), nested),
            ('nested repo, trust key through a symlink', lambda: (self.plant(nested), config.write_text(trust(link/'root/main'))), nested),
            ('untrusted outer repo', lambda: self.plant(self.root), feature),
            ('gitfile redirect to a trusted repo', redirect, feature),
        ]
        for name, arrange, cwd in rows:
            with self.subTest(name):
                shutil.rmtree(self.root/'.git', ignore_errors=True)
                shutil.rmtree(nested, ignore_errors=True)
                shutil.rmtree(other, ignore_errors=True)
                (self.root/'.git').unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                self.marker.unlink(missing_ok=True)
                config.write_text(trust(self.main))
                arrange()
                self.assert_hooks_use_main_code(cwd)

    def test_board_start_preserves_parent_python_isolation(self):
        harness = '''import json,os,socket,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from board import server
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0))
 port=sock.getsockname()[1]
os.environ['PYTHONPATH']=sys.argv[2]
pid=None
try:
 started=server.start(port,'1.0.0',[Path(sys.argv[2])],'machine','owner','started')
 kind,health=server.probe(port)
 pid=health.get('pid')
 result={'start':started['state'],'health':kind,'roots':health.get('roots'),
         'loaded':Path(sys.argv[3]).exists()}
finally:
 stopped=server.stop(port)
 if pid is not None: os.waitpid(pid,0)
print(json.dumps({**result,'stop':stopped['state']}))
'''
        injection = self.feature/'startup'
        write(injection/'sitecustomize.py',
              'from pathlib import Path\nPath('+repr(str(self.marker))+').write_text("child startup")\n')
        env = {**self.env, 'HOME':str(self.root/'home'), 'TMPDIR':str(self.root)}
        env.pop('PYTHONPATH', None)
        for isolated, loaded in ((True, False), (False, True)):
            with self.subTest(isolated=isolated):
                self.marker.unlink(missing_ok=True)
                result = subprocess.run([sys.executable, *(['-I'] if isolated else []), '-c', harness,
                                         str(SKILL_ROOT/'scripts'), str(injection), str(self.marker)],
                                        cwd=self.root, env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 {'start':'started', 'health':'board', 'roots':[str(injection)],
                                  'loaded':loaded, 'stop':'stopped'})

    def test_release_check_uses_main_code_with_isolated_python(self):
        r=self.hook('git-guard.sh',tool_input={'command':'git push origin HEAD:refs/tags/v1.0.0'})
        self.assertIn('release-check: ok',json.loads(r.stdout)['hookSpecificOutput']['permissionDecisionReason'])
        self.assertEqual(self.calls()[0]['argv'][:4],['release-check','--no-build','--repo',str(self.feature)])

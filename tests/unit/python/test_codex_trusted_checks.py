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
        self.env = {**os.environ, 'CHECK_LOG':str(self.log), 'PYTHONPATH':str(self.feature), 'CODEX_HOME':str(self.root/'codex'), 'KIT_MAIN':str(self.main)}
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

    def test_hooks_run_the_code_of_the_main_the_hook_command_names(self):
        """#212, #222, #224: a repo or a gitfile the agent writes never selects the checker code, whatever the stdin cwd."""
        nested = self.feature/'packages/web'
        for name, arrange, cwd in (('nested repo in a sibling feature worktree', lambda: self.plant(nested), nested),
                                   ('repo planted above the project', lambda: self.plant(self.root/'above'), self.feature)):
            with self.subTest(name):
                shutil.rmtree(nested, ignore_errors=True)
                shutil.rmtree(self.root/'above', ignore_errors=True)
                self.log.unlink(missing_ok=True)
                self.marker.unlink(missing_ok=True)
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

    def test_release_check_ignores_a_repo_planted_in_a_feature_subfolder(self):
        """#224: the release-check CLI comes from the main the hook command names, not from the first worktree of the hook cwd."""
        planted = self.feature/'packages/web'
        self.plant(planted)
        self.git(planted, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-qm', 'x')
        for command, expected in ((f'git -C {self.feature} push origin HEAD:refs/tags/v1.0.0', 'release-check: ok'),
                                  ('git push origin HEAD:refs/tags/v1.0.0', 'release-check failed: another repository')):
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                r = subprocess.run(['bash', str(CODEX_ASSETS/'hooks/git-guard.sh')], cwd=planted, env=self.env, capture_output=True, text=True,
                                   input=json.dumps({'cwd': str(planted), 'tool_input': {'command': command}}))
                self.assertIn(expected, json.loads(r.stdout)['hookSpecificOutput']['permissionDecisionReason'])
                self.assertFalse(self.marker.exists())
                self.assertEqual(self.log.exists(), expected == 'release-check: ok')

    def test_release_check_without_a_main_has_no_cli(self):
        env = {k: v for k, v in self.env.items() if k != 'KIT_MAIN'}
        r = subprocess.run(['bash', str(CODEX_ASSETS/'hooks/git-guard.sh')], cwd=self.feature, env=env, capture_output=True, text=True,
                           input=json.dumps({'cwd': str(self.feature), 'tool_input': {'command': 'git push origin HEAD:refs/tags/v1.0.0'}}))
        self.assertIn('release-check failed: no CLI', json.loads(r.stdout)['hookSpecificOutput']['permissionDecisionReason'])
        self.assertFalse(self.log.exists())

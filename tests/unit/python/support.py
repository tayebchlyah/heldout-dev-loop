"""Shared constants and helpers of the skill suite: kit paths, the loaded kit modules, file and git helpers."""

from __future__ import annotations

import ast
import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
# The override wins over every config file, appends to the caller's GIT_CONFIG_* entries, skips if already set.
_GIT_CONFIG_COUNT = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
if not any(
    os.environ.get(f"GIT_CONFIG_KEY_{i}") == "core.fsmonitor" and os.environ.get(f"GIT_CONFIG_VALUE_{i}") == "false"
    for i in range(_GIT_CONFIG_COUNT)
):
    os.environ.update({
        f"GIT_CONFIG_KEY_{_GIT_CONFIG_COUNT}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{_GIT_CONFIG_COUNT}": "false",
        "GIT_CONFIG_COUNT": str(_GIT_CONFIG_COUNT + 1),
    })
# A Codex hook command exports KIT_ROOT and KIT_MAIN, and its Stop gate runs this suite (#227): no test hook sees them.
for _name in ("KIT_ROOT", "KIT_MAIN"):
    os.environ.pop(_name, None)

ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skill"
# The board tests import `board` from skill/scripts and their fixtures from board_fixtures beside this file,
# never from tests/acceptance (held out, run by the acceptance runner, not by this suite).
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(1, str(Path(__file__).resolve().parent))

SKILL = SKILL_ROOT / "SKILL.md"
CHECKER = SKILL_ROOT / "scripts" / "check_setup.py"
CODEX_ASSETS = SKILL_ROOT.parent / "codex"
CLAUDE_HOOKS = SKILL_ROOT.parent / "claude" / "hooks"
SELECTOR = SKILL_ROOT / "scripts" / "select_mutants.py"
NOTES_PATH = SKILL_ROOT / "scripts" / "notes_path.py"
GUARD_ASKS = Path(__file__).resolve().parent / "fixtures" / "git-guard-asks.json"

_spec = importlib.util.spec_from_file_location("check_setup", CHECKER)
if _spec is None or _spec.loader is None:
    # spec_from_file_location does not itself validate that CHECKER exists,
    # so this guard is here only to satisfy the type checker (_spec.loader
    # is Optional[Loader]); a missing CHECKER surfaces below instead, as
    # FileNotFoundError, once exec_module actually tries to read it.
    raise ImportError(f"cannot load check_setup module: {CHECKER}")
check_setup = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(check_setup)
except FileNotFoundError as exc:
    raise FileNotFoundError(f"cannot load check_setup module: missing {CHECKER}") from exc


_selector_spec = importlib.util.spec_from_file_location("select_mutants", SELECTOR)
if _selector_spec is None or _selector_spec.loader is None:
    raise ImportError(f"cannot load select_mutants module: {SELECTOR}")
select_mutants = importlib.util.module_from_spec(_selector_spec)
sys.modules["select_mutants"] = select_mutants
_selector_spec.loader.exec_module(select_mutants)

_notes_spec = importlib.util.spec_from_file_location("notes_path", NOTES_PATH)
if _notes_spec is None or _notes_spec.loader is None:
    raise ImportError(f"cannot load notes_path module: {NOTES_PATH}")
notes_path = importlib.util.module_from_spec(_notes_spec)
sys.modules["notes_path"] = notes_path
_notes_spec.loader.exec_module(notes_path)


def all_instructions() -> str:
    parts = [SKILL.read_text()]
    refs = SKILL_ROOT / "references"
    if refs.exists():
        parts.extend(path.read_text() for path in sorted(refs.glob("*.md")))
    return "\n".join(parts)


MIN_PINNED_PHRASE = 12


def budget_report(path: Path, limit: int) -> str | None:
    size = len(path.read_bytes())
    if size <= limit:
        return None
    text = path.read_text()
    pinned = sorted({node.value for test in Path(__file__).resolve().parent.glob("test_*.py")
                     for node in ast.walk(ast.parse(test.read_text()))
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)
                     and len(node.value) >= MIN_PINNED_PHRASE and node.value in text})
    return "\n".join([f"{path.name}: {size} bytes, {size - limit} over {limit}. Trim it; the suite pins these phrases in it (pins under {MIN_PINNED_PHRASE} characters are not listed):",
                      *pinned])


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def checker_env(bins: Path, claude: str | None = "2.1.309 (Claude Code)", codex: str | None = "codex-cli 0.166.0",
                **extra: str) -> dict[str, str]:
    """An env whose PATH holds git and fake claude and codex binaries printing the given version text, and a clean SHELL."""
    bins.mkdir(parents=True, exist_ok=True)
    if not (bins / "git").exists():
        (bins / "git").symlink_to(shutil.which("git") or "git")
    for name, text in (("claude", claude), ("codex", codex)):
        script = bins / name
        script.unlink(missing_ok=True)
        if text is not None:
            script.write_text(f"#!/bin/sh\n[ \"$1\" = --version ] || exit 2\necho '{text}'\n" if text != "FAIL" else "#!/bin/sh\nexit 1\n")
            script.chmod(0o755)
    shell = bins / "agent-shell"
    shell.write_text("#!/bin/sh\necho flags=hBi\n")
    shell.chmod(0o755)
    ambient = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
    return {**ambient, "PATH": str(bins), "SHELL": str(shell), **extra}


PACKAGE_ROOT = SKILL_ROOT.parent


def git_in(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_git_repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        write(repo / path, text)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "base")
    return repo


STUBS = {
    "ok": 'print(json.dumps({"ok": True, "reasons": []}))',
    "fail": 'print(json.dumps({"ok": False, "reasons": [{"reason": "no-audit"}, {"reason": "open-security", "items": []}]})); sys.exit(1)',
    "slow": "time.sleep(30)",
    "quote": 'sys.stderr.write("bad \\"quote\\" and \\\\ here\\nsecond line\\n"); sys.exit(1)',
}


class GuardRepo:
    """The git guard fixture: a scratch repo (main A B C, tags v1.0.0 and release-1 at A, origin/main at C, feat/x checked out,
    remote origin), a stub release-check CLI, and both git-guard.sh copies run on hook JSON."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.a = self.commit("a")
        self.git("tag", "-a", "v1.0.0", "-m", "v1.0.0")
        self.git("tag", "release-1")
        self.b = self.commit("b")
        self.c = self.commit("c")
        self.git("update-ref", "refs/remotes/origin/main", self.c)
        self.git("checkout", "-q", "-b", "feat/x")
        self.git("remote", "add", "origin", "https://example.invalid/o/r.git")
        self.cli = self.repo / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"
        self.calls = Path(tmp.name) / "calls.txt"
        self.tmp = Path(tmp.name)

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()

    def commit(self, name: str) -> str:
        (self.repo / name).write_text(name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def stub(self, mode: str | None) -> None:
        if mode is None:
            self.cli.unlink(missing_ok=True)
            return
        self.cli.parent.mkdir(parents=True, exist_ok=True)
        self.cli.write_text("import json, sys, time\n"
                            f"open({str(self.calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n" + STUBS[mode] + "\n")

    def guard(self, harness: str, command: str, env: dict[str, str] | None = None, **extra: str) -> tuple[str, str, float]:
        """(decision, reason, seconds). Claude exit 2 is a deny with the stderr text."""
        payload = json.dumps({"cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": command}, **extra})
        start = time.monotonic()
        done = subprocess.run([shutil.which({"claude": "bash", "codex": "sh"}[harness]), str(PACKAGE_ROOT / harness / "hooks" / "git-guard.sh")],
                              input=payload, capture_output=True, text=True, timeout=60, env=env)
        seconds = time.monotonic() - start
        if done.returncode == 2:
            return "deny", done.stderr, seconds
        if not done.stdout:
            return "allow", "", seconds
        out = json.loads(done.stdout)["hookSpecificOutput"]
        return out["permissionDecision"], out["permissionDecisionReason"], seconds

    def runs(self, command: str, env: dict[str, str] | None) -> list[tuple[str, dict, tuple[str, str, float]]]:
        cases = [(h, extra) for h in ("claude", "codex") for extra in ({}, {"agent_id": "a1"})]
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda case: self.guard(case[0], command, env, **case[1]), cases))
        return [(h, extra, r) for (h, extra), r in zip(cases, results)]

    def assert_refused(self, commands: list[str], *parts: str, env: dict[str, str] | None = None) -> None:
        """Claude main asks with every part in the reason, a Claude subagent is denied, Codex denies both."""
        for command in commands:
            for harness, extra, (decision, reason, _) in self.runs(command, env):
                with self.subTest(command=command, harness=harness, **extra):
                    if harness == "claude" and not extra:
                        self.assertEqual(decision, "ask", reason)
                        for part in parts:
                            self.assertIn(part, reason)
                    else:
                        self.assertEqual(decision, "deny", reason)
                        if harness == "claude":
                            self.assertIn("a subagent cannot ask for it", reason)

    def assert_allowed(self, commands: list[str], env: dict[str, str] | None = None) -> None:
        for command in commands:
            for harness, extra, (decision, reason, _) in self.runs(command, env):
                with self.subTest(command=command, harness=harness, **extra):
                    self.assertEqual(decision, "allow", reason)


MODULE_BASE = """LIMIT = 1


def changed(x):
    return x + 1


def untouched(x):
    return x - 1


class Box:
    size = 2

    def grow(self):
        return self.size * 2

    @staticmethod
    def make():
        return 1


@register
def routed():
    return 2
"""


CODEX_GATE_FIXTURE = r'''import json,os,subprocess,sys,tomllib
from pathlib import Path
args=sys.argv[1:]
if args==['--version']:
 print('codex-cli '+os.environ.get('PROBE_VERSION','0.166.0'));sys.exit(0)
if 'app-server' in args:
 if os.environ.get('DEVLOOP_MACOS_BROKER'):
  if 'PROBE_ARGV' in os.environ:Path(os.environ['PROBE_ARGV']).write_text(json.dumps(args))
  if os.environ.get('PROBE_EARLY'):sys.exit(19)
 main=Path(args[args.index('-C')+1])
 config_file=Path(os.environ['PROBE_CONFIG']) if 'PROBE_CONFIG' in os.environ else main/'.codex/config.toml'
 for line in sys.stdin:
  request=json.loads(line);ident=request.get('id');method=request['method']
  if ident is None:continue
  try:
   if os.environ.get('PROBE_POLICY_BAD'): raise ValueError('invalid')
   result={}
   if method=='config/read':
    config=tomllib.loads(config_file.read_text())
    if os.environ.get('PROBE_NATIVE_NULLS'):
     for profile in config['permissions'].values():
      profile.setdefault('filesystem',None);profile.setdefault('network',None)
    denied=os.environ.get('PROBE_MDM_DENY')
    if denied:config['permissions']['dev-workspace'].setdefault('filesystem',{})[denied]='deny'
    result={'config':config}
   elif method=='configRequirements/read':
    result={'requirements':{'allowedPermissionProfiles':{'dev-workspace':False}} if os.environ.get('PROBE_ADMIN_DENY') else {}}
   print(json.dumps({'id':ident,'result':result}),flush=True)
  except Exception:print(json.dumps({'id':ident,'error':{'code':-1}}),flush=True)
 sys.exit(0)
if 'PROBE_ARGV' in os.environ:Path(os.environ['PROBE_ARGV']).write_text(json.dumps(args))
if os.environ.get('PROBE_EARLY'):sys.exit(19)
for key in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
 os.environ.pop(key,None)
if os.environ.get('PROBE_PROXY'):os.environ['HTTP_PROXY']=os.environ['PROBE_PROXY']
sys.exit(subprocess.run(args[args.index('--')+1:]).returncode)
'''


def codex_gate_env():
    """A fake codex and its config in a folder of their own, so the repo under test stays as it is."""
    folder = Path(tempfile.mkdtemp(prefix='devloop-codex-env-'))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    fake = folder / 'bin/codex'
    write(fake, '#!' + sys.executable + '\n' + CODEX_GATE_FIXTURE)
    fake.chmod(0o755)
    write(folder / 'config.toml', '[permissions.dev-workspace]\nextends=":workspace"\n')
    return {'PATH': str(fake.parent) + os.pathsep + os.environ['PATH'], 'CODEX_HOME': str(folder / 'home'),
            'PROBE_CONFIG': str(folder / 'config.toml')}

def darwin_temp_dir():
    return Path(subprocess.check_output(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'], text=True).strip()).resolve()

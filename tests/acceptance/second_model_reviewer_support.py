#!/usr/bin/env python3
"""Shared fixtures of the held-out second-model reviewer tests (spec: Second-Model Reviewer).

One test per `## Acceptance oracle` line (O<n>), grouped by `## Acceptance flows` item (F<n>); the
variants a line lists are subtests. The tests use only the declared seams: the stdout JSON and exit
code of `devloop.py second-review`, the record folder and `taint.json` it writes, `check_setup.py`
output, `bin/install.mjs init`, and the kit text. The `codex` and `claude` CLIs are stub Python
scripts in a folder outside every repo: each records its argv, environment, working folder, stdin,
the file list of its working folder and the `.second-review.diff` text, then prints or writes a canned
table. Every name in the temp repos is invented. HOME and TMPDIR are always temporary folders, and the
command's PATH holds only the stub folder, a tools folder (git, python3), `/usr/bin` and `/bin`.

Fixture choices the spec leaves open:
- The base case: a bare `origin`, a main checkout on `main` (pushed), `dev-loop.json` with
  `"second_review": true`, a main-worktree `dev-loop.local.json` `{"second_review": {"codex": {}}}`,
  a feature worktree on `feat/<slug>` with one commit after `origin/main` that changes `src/app.py`.
- The held-out suite of the fixture is `tests/acceptance/test_widget.py` on `main`, with two
  distinctive lines (`HELD_1`, `HELD_2`) a leaking stub quotes, each on its own line.
- macOS adds `__CF_USER_TEXT_ENCODING` to every process environment; O14 ignores that one name.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
SKILL = REPO_ROOT / "skill" / "SKILL.md"
CLAUDE_REF = REPO_ROOT / "skill" / "references" / "claude.md"
CODEX_REF = REPO_ROOT / "skill" / "references" / "codex.md"
PROMPT_REF = REPO_ROOT / "skill" / "references" / "second-review.md"

SLUG = "widget-colour"
BASE_CONFIG = {"version": "2.1.1", "harnesses": ["claude", "codex"]}
ON_LOCAL = {"second_review": {"codex": {}}}

TABLE = ("| file | line | finding | class | verdict |\n"
         "| --- | --- | --- | --- | --- |\n"
         "| src/app.py | 2 | colour() ignores the theme argument | correctness | PLAUSIBLE |\n")
APP_BASE = "def colour(theme):\n    return 'red'\n"
APP_FEATURE = "def colour(theme):\n    return 'blue'\n"
HELD_1 = '        self.assertEqual(frobnicate_widget("quartz-7731"), 4096)'
HELD_2 = '        self.assertIn("zebra-lantern-0042", render_banner(mode="night"))'
HELD_FILE = ("import unittest\n\n\nclass WidgetTests(unittest.TestCase):\n"
             "    def test_frobnicate(self):\n" + HELD_1 + "\n\n"
             "    def test_banner(self):\n" + HELD_2 + "\n")

D8_NAMES = {"HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
            "PATH"}
OS_ADDED = {"__CF_USER_TEXT_ENCODING"}
CODEX_PERMISSIONS = ('permissions.devloop-review.filesystem={":minimal"="read", ":project_roots"="read", '
                     '":tmpdir"="none", ":slash_tmp"="none"}')

STUB = r'''#!{python}
import json, os, subprocess, sys, time
NAME = {name!r}
CFG = json.loads({cfg!r})
LOG = {log!r}
argv = sys.argv[1:]
cwd = os.getcwd()
stdin = sys.stdin.read()
files = []
for folder, dirs, names in os.walk(cwd):
    for entry in names + [d for d in dirs if os.path.islink(os.path.join(folder, d))]:
        files.append(os.path.relpath(os.path.join(folder, entry), cwd))
diff = None
diff_path = os.path.join(cwd, ".second-review.diff")
if os.path.isfile(diff_path):
    with open(diff_path, encoding="utf-8", errors="replace") as handle:
        diff = handle.read()
record = {{"argv": argv, "env": dict(os.environ), "cwd": cwd, "stdin": stdin, "files": sorted(files),
          "diff": diff, "pid": os.getpid(), "child": None}}
if CFG["child"]:
    record["child"] = subprocess.Popen(["/bin/sleep", "120"]).pid
with open(LOG + ".tmp", "w") as handle:
    json.dump(record, handle)
os.replace(LOG + ".tmp", LOG)
if CFG["sleep"]:
    time.sleep(CFG["sleep"])
if CFG["stderr"]:
    sys.stderr.write(CFG["stderr"])
    sys.stderr.flush()
if NAME == "codex":
    if CFG["output"] is not None and "-o" in argv:
        with open(argv[argv.index("-o") + 1], "w") as handle:
            handle.write(CFG["output"])
else:
    print(json.dumps({{"type": "result", "result": CFG["output"], "is_error": CFG["is_error"],
                      "usage": {{"input_tokens": 1200, "output_tokens": 300}}}}))
sys.exit(CFG["exit"])
'''


def put(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)


def config_text(**extra: object) -> str:
    return json.dumps({**BASE_CONFIG, **extra}, indent=2) + "\n"


def inside(path: str | Path, folder: Path) -> bool:
    try:
        Path(os.path.realpath(path)).relative_to(os.path.realpath(folder))
        return True
    except ValueError:
        return False


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def base_env(home: Path, tmp: Path) -> dict[str, str]:
    """A minimal environment: no user config, no harness variables."""
    return {"HOME": str(home), "TMPDIR": str(tmp), "USER": os.environ.get("USER", "robin"),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false"}


class Project:
    """A bare origin, a main checkout, a main-worktree local file, stubs, and one feature worktree."""

    def __init__(self, root: Path, main_config: str | None = None, local: object = ON_LOCAL,
                 branch: str = "main", origin: bool = True, base_files: dict[str, str] | None = None) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.home = root / "home"
        self.tmp = root / "tmp"
        self.notes = root / "notes"
        self.stubs = root / "stubs"
        self.tools = root / "tools"
        for folder in (self.home, self.tmp, self.notes / "specs", self.stubs, self.tools):
            folder.mkdir(parents=True, exist_ok=True)
        for tool in ("git", "python3"):
            found = shutil.which(tool) if tool == "git" else sys.executable
            os.symlink(found, self.tools / tool)
        self.log = root / "stub-log.json"
        self.env = base_env(self.home, self.tmp)
        self.env["DEV_LOOP_NOTES"] = str(self.notes)
        self.repo = root / "acme-widgets"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", branch)
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git(self.repo, "config", key, value)
        files = {
            "dev-loop.json": config_text(second_review=True) if main_config is None else main_config,
            ".gitignore": "dev-loop.local.json\n",
            "README.md": "# Acme widgets\n",
            "AGENTS.md": "# Agents\n\nKeep functions small.\n",
            "src/app.py": APP_BASE,
            "tests/acceptance/test_widget.py": HELD_FILE,
        }
        files.update(base_files or {})
        self.base = self.commit(self.repo, files, "Base")
        self.origin = None
        if origin:
            self.origin = root / "origin.git"
            self.git(root, "init", "-q", "--bare", "-b", branch, str(self.origin))
            self.git(self.repo, "remote", "add", "origin", str(self.origin))
            self.git(self.repo, "push", "-q", "-u", "origin", branch)
        self.set_local(local)
        self.wt: Path | None = None

    def git(self, cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, env=env or self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def blob(self, cwd: Path, data: bytes) -> str:
        result = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=cwd, env=self.env, input=data,
                                capture_output=True)
        if result.returncode != 0:
            raise AssertionError(result.stderr.decode())
        return result.stdout.decode().strip()

    def commit(self, cwd: Path, files: dict[str, str | bytes], message: str) -> str:
        for path, text in files.items():
            put(cwd / path, text)
        self.git(cwd, "add", "-A", "--", *files)
        self.git(cwd, "commit", "-q", "-m", message)
        return self.git(cwd, "rev-parse", "HEAD")

    def commit_tree(self, cwd: Path, files: dict[str, bytes], links: dict[str, str], message: str) -> str:
        """A commit built from blobs, never checked out: paths that collide on a case-blind disk."""
        index = self.root / "plumbing.index"
        env = {**self.env, "GIT_INDEX_FILE": str(index)}
        self.git(cwd, "read-tree", "HEAD", env=env)
        for path, data in files.items():
            self.git(cwd, "update-index", "--add", "--cacheinfo", f"100644,{self.blob(cwd, data)},{path}", env=env)
        for path, target in links.items():
            self.git(cwd, "update-index", "--add", "--cacheinfo",
                     f"120000,{self.blob(cwd, target.encode())},{path}", env=env)
        tree = self.git(cwd, "write-tree", env=env)
        sha = self.git(cwd, "commit-tree", tree, "-p", "HEAD", "-m", message)
        self.git(cwd, "update-ref", "HEAD", sha)
        self.git(cwd, "read-tree", "HEAD")
        return sha

    def set_local(self, local: object) -> None:
        path = self.repo / "dev-loop.local.json"
        if local is None:
            path.unlink(missing_ok=True)
        elif isinstance(local, str):
            path.write_text(local)
        else:
            path.write_text(json.dumps(local, indent=2) + "\n")

    def feature(self, files: dict[str, str | bytes] | None = None, prefix: str = "feat") -> Path:
        start = "refs/remotes/origin/main" if self.origin else "HEAD"
        if self.origin:
            self.git(self.repo, "fetch", "-q", "origin")
        wt = self.root / "worktrees" / f"acme-widgets-{SLUG}"
        self.git(self.repo, "worktree", "add", "-q", "-b", f"{prefix}/{SLUG}", str(wt), start)
        self.wt = wt.resolve()
        if files is None:
            files = {"src/app.py": APP_FEATURE}
        if files:
            self.commit(self.wt, files, "Build the feature")
        return self.wt

    def stub(self, name: str = "codex", output: str | None = TABLE, stderr: str = "", exit: int = 0,
             sleep: float = 0, child: bool = False, is_error: bool = False, folder: Path | None = None) -> Path:
        folder = folder or self.stubs
        folder.mkdir(parents=True, exist_ok=True)
        cfg = json.dumps({"output": output, "stderr": stderr, "exit": exit, "sleep": sleep, "child": child,
                          "is_error": is_error})
        path = folder / name
        path.write_text(STUB.format(python=sys.executable, name=name, cfg=cfg, log=str(self.log)))
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def stub_log(self) -> dict | None:
        return json.loads(self.log.read_text()) if self.log.exists() else None

    def run(self, harness: str = "claude", *extra: str, path: list[Path] | None = None,
            env: dict[str, str] | None = None, cwd: Path | None = None) -> tuple[int, dict, float]:
        self.log.unlink(missing_ok=True)
        folders = [str(p) for p in (path if path is not None else [self.stubs])] + [str(self.tools), "/usr/bin",
                                                                                     "/bin"]
        run_env = {**self.env, "PATH": os.pathsep.join(folders), **(env or {})}
        start = time.monotonic()
        result = subprocess.run([sys.executable, str(DEVLOOP), "second-review", "--harness", harness, *extra],
                                cwd=cwd or self.wt or self.repo, env=run_env, capture_output=True, text=True,
                                timeout=120)
        seconds = time.monotonic() - start
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {"unparsed_stdout": result.stdout, "stderr": result.stderr}
        if not isinstance(payload, dict):
            payload = {"unparsed_stdout": result.stdout}
        payload.setdefault("_stderr", result.stderr)
        return result.returncode, payload, seconds

    def common_dir(self) -> Path:
        cwd = self.wt or self.repo
        return (cwd / self.git(cwd, "rev-parse", "--git-common-dir")).resolve()


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.count = 0

    def project(self, **kwargs: object) -> Project:
        self.count += 1
        return Project(self.root / f"p{self.count}", **kwargs)

    def ready(self, harness_stub: str = "codex", **kwargs: object) -> Project:
        """The base case of the oracle: both locks on, one commit after origin/main, a stub on PATH."""
        project = self.project(**kwargs)
        project.feature()
        project.stub(harness_stub)
        return project

    def assert_result(self, code: int, payload: dict, state: str, reason: str | None, exit: int | None = None) -> None:
        self.assertEqual(payload.get("state"), state, payload)
        self.assertEqual(payload.get("reason"), reason, payload)
        if exit is not None:
            self.assertEqual(code, exit, payload)

    def taint_records(self, project: Project) -> list[dict]:
        found: list[dict] = []

        def walk(node: object) -> None:
            if isinstance(node, dict):
                if node.get("agent_type") == "second-review":
                    found.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for path in project.common_dir().rglob("taint.json"):
            walk(json.loads(path.read_text()))
        return found

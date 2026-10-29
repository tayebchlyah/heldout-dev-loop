#!/usr/bin/env python3
"""Shared fixtures of the held-out merge-check binding tests (spec: Merge Check Binding).

Seams: `devloop.py merge-check`, `devloop.py acceptance`, `devloop.py status`, `notes_path.py` (the kit source under
`skill/scripts/`), each run as a process on scratch git repos with linked worktrees. Every name in the temp repos is
invented. HOME and TMPDIR are temporary folders; no `DEV_LOOP_*`, `DEVLOOP_*` or `GIT_*` variable leaks in.

Fixture choices the spec leaves open:
- `Project`: a bare `origin`, a main checkout on `main`, worktrees under `<root>/worktrees/`, a `.gitignore` that
  ignores `dev-loop.local.json`, `.superpowers/` and the in-repo notes folders. The notes folder comes from the main
  worktree `dev-loop.local.json` (`{"notes": "<absolute path>"}`) unless a case says otherwise.
- Track M or L: a spec `Features/20261029 <Title>/Spec.md` with `feature` and `track`, `Plan.md` beside it, the
  ledger with `Feature gate: green at <sha>` in the feature worktree (Feature Folders layout). Track S: no spec,
  `<git-dir>/dev-loop/review-done` holds the sha.
- `GateRepo`: a repo on `main` with no `origin`, whose `dev-loop.json` maps `commands.acceptance` (and
  `commands.acceptance_failed`) to a shell script outside the repo that appends a line to `ran.log` (the marker),
  copies a fixture JUnit file into `$DEVLOOP_REPORT_DIR` and exits with a fixture code; a linked worktree on
  `feat/demo`; a fake `docker` first in `PATH`.
- `notes_path.py` prints the resolved folder on stdout; the tests only look for the path text in it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from feature_folders_support import write_ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
NOTES_PATH = REPO_ROOT / "skill" / "scripts" / "notes_path.py"

BASE_CONFIG = {"version": "2.1.1", "harnesses": ["claude", "codex"]}
SLUG = "demo"
APP_BASE = "def colour():\n    return 'red'\n"
APP_FEATURE = "def colour():\n    return 'blue'\n"
TEST_BASE = "def test_x():\n    assert 1 == 1\n"
IGNORED = "dev-loop.local.json\n.superpowers/\nreal-notes/\nfake-notes/\njson-notes/\nvault-n/\nvault-x/\n"


def put(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)


def config_text(**extra: object) -> str:
    return json.dumps({**BASE_CONFIG, **extra}, indent=2) + "\n"


def clean_env(root: Path) -> dict[str, str]:
    home, tmp = root / "home", root / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_", "COMPOSE_", "DOCKER_"))}
    env.update({"HOME": str(home), "TMPDIR": str(tmp), "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"), "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false"})
    return env


def git(cwd: Path, env: dict[str, str], *args: str, when: str | None = None) -> str:
    if when:
        env = {**env, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result.stdout.strip()


def init_repo(repo: Path, env: dict[str, str]) -> None:
    repo.mkdir(parents=True)
    git(repo, env, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                       ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        git(repo, env, "config", key, value)


def run_json(args: list[str], cwd: Path, env: dict[str, str], timeout: float = 180) -> tuple[int, dict, str]:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    tail = f"{result.stdout[-600:]!r} {result.stderr[-600:]!r}"
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise AssertionError(f"{Path(args[1]).name} {' '.join(args[2:])}: stdout is not one JSON object "
                             f"(exit {result.returncode}): {tail}")
    if not isinstance(payload, dict):
        raise AssertionError(f"{' '.join(args[2:])}: stdout is not a JSON object: {tail}")
    return result.returncode, payload, result.stderr


def reasons(payload: dict, name: str) -> list[dict]:
    return [item for item in payload.get("reasons") or [] if isinstance(item, dict) and item.get("reason") == name]


def section(text: str, number: int) -> str:
    match = re.search(rf"^## {number} .*?(?=^## |\Z)", text, re.M | re.S)
    assert match, f"no `## {number}` section"
    return match.group(0)


class Project:
    """A bare origin, a main checkout `acme-shop` on `main`, a notes folder, and feature worktrees."""

    def __init__(self, root: Path, config: str | None = None, local_notes: bool = True) -> None:
        self.root = root
        self.env = clean_env(root)
        self.notes = root / "notes"
        (self.notes / "Features").mkdir(parents=True)
        self.origin = root / "origin.git"
        git(root, self.env, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo = root / "acme-shop"
        init_repo(self.repo, self.env)
        git(self.repo, self.env, "remote", "add", "origin", str(self.origin))
        self.base = self.commit(self.repo, {
            "dev-loop.json": config if config is not None else config_text(),
            ".gitignore": IGNORED,
            "README.md": "# Acme shop\n",
            "src/app.py": APP_BASE,
            "tests/unit/test_x.py": TEST_BASE,
        }, "Base")
        git(self.repo, self.env, "push", "-q", "-u", "origin", "main")
        if local_notes:
            put(self.local_file, json.dumps({"notes": str(self.notes)}) + "\n")

    @property
    def local_file(self) -> Path:
        return self.repo / "dev-loop.local.json"

    def git(self, cwd: Path, *args: str) -> str:
        return git(cwd, self.env, *args)

    def head(self, cwd: Path) -> str:
        return self.git(cwd, "rev-parse", "HEAD")

    def commit(self, cwd: Path, files: dict[str, str | bytes], message: str) -> str:
        for path, text in files.items():
            put(cwd / path, text)
        self.git(cwd, "add", "-A", "--", *files)
        self.git(cwd, "commit", "-q", "-m", message)
        return self.head(cwd)

    def advance_main(self, files: dict[str, str], message: str) -> str:
        sha = self.commit(self.repo, files, message)
        self.git(self.repo, "push", "-q", "origin", "main")
        return sha

    def worktree(self, branch: str, name: str | None = None) -> Path:
        self.git(self.repo, "fetch", "-q", "origin")
        wt = self.root / "worktrees" / (name or "acme-shop-" + branch.replace("/", "-"))
        self.git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt), "refs/remotes/origin/main")
        return wt.resolve()

    def rebase(self, wt: Path) -> None:
        self.git(wt, "fetch", "-q", "origin")
        self.git(wt, "rebase", "-q", "refs/remotes/origin/main")

    def folder(self, notes: Path | None, slug: str) -> Path:
        return (notes or self.notes) / "Features" / f"20261029 {slug.replace('-', ' ').title()}"

    def spec(self, slug: str, track: str, notes: Path | None = None) -> Path:
        path = self.folder(notes, slug) / "Spec.md"
        put(path, f"---\ntype: spec\nfeature: {slug}\ntrack: {track}\nreview_status: approved\nacceptance:\n"
                  f"shipped:\n---\n\n# {slug.title()}\n")
        return path

    def review(self, wt: Path, sha: str, track: str, slug: str = SLUG, notes: Path | None = None) -> None:
        """Track S: the `review-done` marker of the worktree; else the plan and the ledger green line."""
        if track == "S":
            put(Path(self.git(wt, "rev-parse", "--absolute-git-dir")) / "dev-loop" / "review-done", sha + "\n")
            return
        plan = self.folder(notes, slug) / "Plan.md"
        put(plan, "---\ntype: plan\nspec: Spec.md\n---\n\n# Plan\n\n### Task 1: Build it\n")
        write_ledger(wt, plan, f"# Ledger\nTask 1: complete (tests green)\nFeature gate: green at {sha}\n")

    def devloop(self, cwd: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, dict, str]:
        return run_json([sys.executable, str(DEVLOOP), *args], cwd, {**self.env, **(env or {})})

    def merge_check(self, cwd: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
        code, payload, _ = self.devloop(cwd, "merge-check", *args, env=env)
        return code, payload

    def notes_path(self, repo: Path, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        result = subprocess.run([sys.executable, str(NOTES_PATH), "--repo", str(repo)], cwd=repo,
                                env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=60)
        return result.returncode, result.stdout, result.stderr


RUNNER = """#!/bin/sh
F='{fix}'
echo ran >> "$F/ran.log"
mkdir -p "$DEVLOOP_REPORT_DIR"
if [ -f "$F/report.xml" ]; then cp "$F/report.xml" "$DEVLOOP_REPORT_DIR/fixture.xml"; fi
exit "$(cat "$F/exit")"
"""

FAKE_DOCKER = r'''import json
import pathlib
import sys

fix = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
state = json.loads((fix / "docker-state.json").read_text())
words = [a for a in args if not a.startswith("-")]
if args and args[0] == "compose":
    if "config" in words:
        print(json.dumps({"name": "acme", "services": state["services"]}))
    elif "ps" in words:
        for cid in state["containers"].get(words[-1], []):
            print(cid[:12])
    else:
        sys.exit(1)
elif args and args[0] == "inspect":
    out = []
    for word in words[1:]:
        for cid, created in state["created"].items():
            if cid.startswith(word):
                out.append({"Id": cid, "Created": created, "Name": "/acme-" + cid[:4]})
    print(json.dumps(out, indent=4))
else:
    sys.exit(1)
'''


def junit(failing: tuple[str, ...] = (), passing: int = 0, skipped: int = 0) -> str:
    """A JUnit file: a `testcase` with a `failure` child per failing name, `passing` passing cases, `skipped`
    cases with a `skipped` child. No argument: one `testsuite` with no `testcase`."""
    cases = [f'<testcase classname="acme.Widgets" name="{name}"><failure message="no"/></testcase>'
             for name in failing]
    cases += [f'<testcase classname="acme.Widgets" name="test_ok_{n}"/>' for n in range(passing)]
    cases += [f'<testcase classname="acme.Widgets" name="test_skip_{n}"><skipped message="later"/></testcase>'
              for n in range(skipped)]
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites><testsuite name="fixture" tests="{len(cases)}">'
            + "".join(cases) + "</testsuite></testsuites>\n")


class GateRepo:
    """A repo `acme-widgets` on `main` (no origin) and a linked worktree on `feat/demo`."""

    def __init__(self, root: Path, files: dict[str, str] | None = None, when: str | None = None) -> None:
        self.root = root
        self.env = clean_env(root)
        self.tmp = root / "tmp"
        self.fix = root / "fixture"
        self.fix.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env.get("PATH", "")
        (self.fix / "fake_docker.py").write_text(FAKE_DOCKER)
        docker = bin_dir / "docker"
        docker.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{self.fix / 'fake_docker.py'}' '{self.fix}' \"$@\"\n")
        docker.chmod(0o755)
        runner = self.fix / "run.sh"
        runner.write_text(RUNNER.format(fix=self.fix))
        runner.chmod(0o755)
        self.commands = {"acceptance": f"sh '{runner}'", "acceptance_failed": f"sh '{runner}' --failed"}
        self.repo = root / "acme-widgets"
        init_repo(self.repo, self.env)
        self.commit(self.repo, {"dev-loop.json": self.config(self.commands), ".gitignore": ".superpowers/\n",
                                "app.py": "def widget():\n    return 1\n", **(files or {})}, "Initial", when=when)
        self.wt = root / "worktrees" / "acme-widgets-demo"
        git(self.repo, self.env, "worktree", "add", "-q", "-b", "feat/demo", str(self.wt), "main")
        self.wt = self.wt.resolve()

    @staticmethod
    def config(commands: dict | None) -> str:
        return config_text(commands=commands) if commands is not None else config_text()

    def git(self, cwd: Path, *args: str, when: str | None = None) -> str:
        return git(cwd, self.env, *args, when=when)

    def commit(self, cwd: Path, files: dict[str, str], message: str, when: str | None = None) -> str:
        for path, text in files.items():
            put(cwd / path, text)
        self.git(cwd, "add", "-A", "--", *files)
        self.git(cwd, "commit", "-q", "-m", message, when=when)
        return self.git(cwd, "rev-parse", "HEAD")

    def fixture(self, xml: str | None, exit_code: int) -> None:
        report = self.fix / "report.xml"
        if xml is None:
            report.unlink(missing_ok=True)
        else:
            report.write_text(xml)
        (self.fix / "exit").write_text(f"{exit_code}\n")

    def ran(self) -> int:
        log = self.fix / "ran.log"
        return len(log.read_text().splitlines()) if log.exists() else 0

    def acceptance(self, *args: str) -> tuple[int, dict, str]:
        return run_json([sys.executable, str(DEVLOOP), "acceptance", *args], self.wt, self.env)

    def snapshot(self) -> dict[str, bytes | None]:
        """Every file and folder under a `dev-loop` folder of the git dirs, and everything in TMPDIR."""
        found: dict[str, bytes | None] = {}
        for base, all_paths in ((self.repo / ".git", False), (self.tmp, True)):
            for path in sorted(base.rglob("*")):
                if all_paths or "dev-loop" in path.relative_to(base).parts:
                    found[str(path)] = path.read_bytes() if path.is_file() else None
        return found

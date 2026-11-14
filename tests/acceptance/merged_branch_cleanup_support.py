#!/usr/bin/env python3
"""Shared fixture of the held-out merged branch cleanup tests (spec: 20261114 Merged Branch Cleanup, issue #320).

Seam 1: the `devloop.py cleanup` process of the kit source (`skill/scripts/devloop.py`), its stdout JSON and exit
code, on a scratch repository with a local bare `origin`, a main checkout and linked worktrees. Every name in the
scratch repos is invented. HOME and TMPDIR are folders in the scratch root; no `DEV_LOOP_*`, `DEVLOOP_*`, `CLAUDE*`,
`CODEX*` or `GIT_*` variable leaks in.

Fixture choices the spec leaves open:
- `Shop`: a bare `origin.git`, a main checkout `acme-shop` on `main` (pushed, with upstream), worktrees under
  `<root>/worktrees/`, a `.gitignore` that ignores `build/`. A second clone `github` stands for GitHub: a "merge" there
  cherry-picks the branch commits onto `main` with a GitHub committer and a later date (a rebase merge: new shas, the
  same patches), pushes `main`, then deletes the branch on `origin` (the `delete_branch_on_merge` setting).
- A feature branch gets one commit that adds `src/<name>.py`, pushed with `-u`, so the main checkout holds a
  remote-tracking ref of it. A feature with no worktree is made the same way, then its worktree is removed.
- After a merge the main checkout is behind `origin/main` (its remote-tracking ref is not fetched by the fixture).
- "Nothing changes": the refs of the main checkout, its worktree list, `HEAD` and `git status --porcelain` output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
DEVLOOP = SKILL / "scripts" / "devloop.py"
GIT_GUARD = REPO_ROOT / "claude" / "hooks" / "git-guard.sh"
MERGE_DATE = "2026-11-14T12:00:00Z"


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def clean_env(root: Path) -> dict[str, str]:
    home, tmp = root / "home", root / "tmp"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    tmp.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))}
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "TMPDIR": str(tmp),
                "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"), "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false"})
    return env


def git(cwd: Path, env: dict[str, str], *args: str, extra: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env={**env, **(extra or {})}, capture_output=True, text=True,
                            timeout=60)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result.stdout.strip()


def identity(repo: Path, env: dict[str, str]) -> None:
    for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                       ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        git(repo, env, "config", key, value)


def run_json(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, dict]:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    tail = f"{result.stdout[-800:]!r} {result.stderr[-800:]!r}"
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise AssertionError(f"{' '.join(args[2:])}: stdout is not one JSON object (exit {result.returncode}): {tail}")
    if not isinstance(payload, dict):
        raise AssertionError(f"{' '.join(args[2:])}: stdout is not a JSON object: {tail}")
    return result.returncode, payload


class Shop:
    """A bare origin, the main checkout `acme-shop` on `main`, the `github` clone, and feature worktrees."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.env = clean_env(root)
        self.origin = root / "origin.git"
        git(root, self.env, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo = root / "acme-shop"
        self.repo.mkdir()
        git(self.repo, self.env, "init", "-q", "-b", "main")
        identity(self.repo, self.env)
        git(self.repo, self.env, "remote", "add", "origin", str(self.origin))
        self.commit(self.repo, {".gitignore": "build/\n", "README.md": "# Acme shop\n",
                                "src/app.py": "def shop():\n    return 'open'\n"}, "Base")
        git(self.repo, self.env, "push", "-q", "-u", "origin", "main")
        self.github = root / "github"
        git(root, self.env, "clone", "-q", str(self.origin), str(self.github))
        identity(self.github, self.env)

    def git(self, cwd: Path, *args: str) -> str:
        return git(cwd, self.env, *args)

    def commit(self, cwd: Path, files: dict[str, str], message: str) -> str:
        for rel, text in files.items():
            put(cwd / rel, text)
        self.git(cwd, "add", "-A", "--", *files)
        self.git(cwd, "commit", "-q", "-m", message)
        return self.git(cwd, "rev-parse", "HEAD")

    def feature(self, branch: str, keep_worktree: bool = True) -> Path | None:
        """A branch from `origin/main` with one commit, pushed with upstream; its worktree, or None when removed."""
        name = branch.rsplit("/", 1)[-1]
        wt = self.root / "worktrees" / f"acme-shop-{name}"
        self.git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt), "refs/remotes/origin/main")
        self.commit(wt, {f"src/{name.replace('-', '_')}.py": f"def {name.replace('-', '_')}():\n    return 1\n"},
                    f"Add {name}")
        self.git(wt, "push", "-q", "-u", "origin", branch)
        if not keep_worktree:
            self.git(self.repo, "worktree", "remove", str(wt))
            return None
        return wt.resolve()

    def merge(self, branch: str) -> str:
        """Rebase-merge `branch` on the `github` clone, push `main`, delete the branch on `origin`."""
        g = self.github
        self.git(g, "fetch", "-q", "origin")
        self.git(g, "checkout", "-q", "main")
        self.git(g, "merge", "-q", "--ff-only", "refs/remotes/origin/main")
        picks = self.git(g, "rev-list", "--reverse", f"refs/remotes/origin/main..refs/remotes/origin/{branch}")
        for sha in picks.split():
            git(g, self.env, "cherry-pick", sha, extra={"GIT_COMMITTER_NAME": "GitHub",
                                                         "GIT_COMMITTER_EMAIL": "noreply@github.invalid",
                                                         "GIT_COMMITTER_DATE": MERGE_DATE})
        self.git(g, "push", "-q", "origin", "main")
        self.git(g, "push", "-q", "origin", "--delete", branch)
        return self.git(g, "rev-parse", "HEAD")

    def origin_main(self) -> str:
        return self.git(self.github, "ls-remote", str(self.origin), "refs/heads/main").split()[0]

    def head(self, cwd: Path | None = None) -> str:
        return self.git(cwd or self.repo, "rev-parse", "HEAD")

    def has_ref(self, ref: str) -> bool:
        result = subprocess.run(["git", "show-ref", "--verify", "-q", ref], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=60)
        return result.returncode == 0

    def has_branch(self, branch: str) -> bool:
        return self.has_ref(f"refs/heads/{branch}")

    def git_dir(self, wt: Path) -> Path:
        return Path(self.git(wt, "rev-parse", "--absolute-git-dir"))

    def snapshot(self) -> dict[str, str]:
        """What "nothing changes" compares: refs, worktree list, HEAD and status of the main checkout."""
        return {"refs": self.git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)"),
                "worktrees": self.git(self.repo, "worktree", "list", "--porcelain"),
                "head": self.head(), "status": self.git(self.repo, "status", "--porcelain")}

    def cleanup(self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, dict]:
        return run_json([sys.executable, str(DEVLOOP), "cleanup", *args], cwd or self.repo, {**self.env, **(env or {})})


class ScratchCase(unittest.TestCase):
    """A scratch folder, removed after the test."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="branch-cleanup-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)

    def shop(self, name: str) -> Shop:
        return Shop(self.root / name)

    def assert_parts(self, payload: dict, worktree: str, branch: str, main: str) -> None:
        self.assertEqual((payload.get("worktree"), payload.get("branch"), payload.get("main")),
                         (worktree, branch, main), payload)

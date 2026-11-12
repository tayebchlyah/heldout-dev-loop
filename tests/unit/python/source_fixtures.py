"""Fixtures of the `source_dirs` tests: an oracle repo and plumbing writes of `dev-loop.json`."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from support import git_in, write

FILES = ("internal/x.go", "cmd/app/main.go", "src/app.py", "pkg/src/y.py", "api/spec.go", "docs/guide.md",
         "scripts/run.sh", "tests/" + "acceptance" + "/test_x.py")


def source_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    git_in(repo, "init", "-q", "-b", "main")
    for name in FILES:
        write(repo / name, "x\n")
    write(repo / "dev-loop.json", json.dumps({"version": "0.0.0"}))
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "init")
    git_in(repo, "checkout", "-q", "-b", "feat/x")
    return repo


def set_main(repo: Path, text: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "blob"
        scratch.write_text(text)
        blob = git_in(repo, "hash-object", "-w", str(scratch)).strip()
        parent = git_in(repo, "rev-parse", "refs/heads/main").strip()
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))

        def plumb(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True,
                                  text=True).stdout.strip()

        plumb("read-tree", parent)
        plumb("update-index", "--add", "--cacheinfo", f"100644,{blob},dev-loop.json")
        tree = plumb("write-tree")
        commit = git_in(repo, "commit-tree", tree, "-p", parent, "-m", "main key").strip()
        git_in(repo, "update-ref", "refs/heads/main", commit)


def set_branch(repo: Path, text: str) -> None:
    write(repo / "dev-loop.json", text)
    git_in(repo, "add", "dev-loop.json")
    git_in(repo, "commit", "-q", "-m", "branch key")


def key(value: object) -> str:
    return json.dumps({"version": "0.0.0", "source_dirs": value})

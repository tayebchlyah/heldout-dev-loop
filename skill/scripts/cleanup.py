"""`cleanup`: after a merge, fetch with prune, remove the merged worktree, delete the branch, rebase the main checkout."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from devloop import emit


OPERATIONS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")


def run(cwd: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def first_line(result: subprocess.CompletedProcess) -> str:
    for text in (result.stderr, result.stdout):
        for line in text.splitlines():
            if line.strip():
                return line.strip()
    return f"exit {result.returncode}"


def worktree_entries(repo: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in run(repo, "worktree", "list", "--porcelain").stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "worktree":
            entries.append({})
        if entries:
            entries[-1][key] = value
    return entries


def merged(main: str, branch: str) -> bool:
    result = run(main, "cherry", "origin/main", branch)
    return result.returncode == 0 and not any(line.startswith("+") for line in result.stdout.splitlines())


def in_use(path: str) -> bool:
    cwd, target = Path.cwd().resolve(), Path(path).resolve()
    return cwd == target or target in cwd.parents


def dirty(cwd: str, *options: str) -> bool:
    result = run(cwd, "status", "--porcelain", *options)
    return result.returncode != 0 or bool(result.stdout.strip())


def paused(path: str) -> bool:
    result = run(path, "rev-parse", "--absolute-git-dir")
    return result.returncode == 0 and (Path(result.stdout.strip()) / "dev-loop" / "pause.json").exists()


def worktree_part(main: str, entry: dict[str, str] | None, is_main: bool, is_merged: bool) -> tuple[str, str | None]:
    if entry is None:
        return "absent", None
    path = entry["worktree"]
    if is_main:
        return "skipped", "main-checkout"
    if in_use(path):
        return "skipped", "in-use"
    if not is_merged:
        return "skipped", "not-merged"
    if dirty(path):
        return "skipped", "dirty"
    if paused(path):
        return "skipped", "paused"
    result = run(main, "worktree", "remove", path)
    if result.returncode != 0:
        return "skipped", f"remove-failed: {first_line(result)}"
    return "removed", None


def branch_part(main: str, branch: str, worktree: str, is_merged: bool) -> tuple[str, str | None]:
    if worktree == "skipped":
        return "skipped", "worktree-kept"
    if run(main, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode != 0:
        return "absent", None
    if not is_merged:
        return "skipped", "not-merged"
    result = run(main, "branch", "-D", branch)
    if result.returncode != 0:
        return "skipped", f"delete-failed: {first_line(result)}"
    return "deleted", None


def main_part(main: str) -> tuple[str, str | None]:
    if run(main, "symbolic-ref", "-q", "HEAD").stdout.strip() != "refs/heads/main":
        return "skipped", "not-on-main"
    git_dir = Path(run(main, "rev-parse", "--absolute-git-dir").stdout.strip())
    if any((git_dir / name).exists() for name in OPERATIONS):
        return "skipped", "operation-in-progress"
    if dirty(main, "--untracked-files=no"):
        return "skipped", "dirty"
    ahead = run(main, "rev-list", "--count", "origin/main..HEAD")
    if ahead.returncode != 0 or ahead.stdout.strip() != "0":
        return "skipped", "local-commits"
    if run(main, "rev-parse", "HEAD").stdout == run(main, "rev-parse", "origin/main").stdout:
        return "current", None
    result = run(main, "rebase", "origin/main")
    if result.returncode != 0:
        run(main, "rebase", "--abort")
        return "skipped", f"rebase-failed: {first_line(result)}"
    return "rebased", None


def cmd_cleanup(args: argparse.Namespace) -> int:
    branch = args.branch
    if branch in ("main", "master"):
        return emit({"error": f"cleanup never deletes {branch}"}, 2)
    if run(args.repo, "rev-parse", "--show-toplevel").returncode != 0:
        return emit({"error": f"not a git repository: {args.repo}"}, 2)
    entries = worktree_entries(args.repo)
    main = entries[0]["worktree"]
    found = next((i for i, e in enumerate(entries) if e.get("branch") == f"refs/heads/{branch}"), None)
    entry = None if found is None else entries[found]
    payload: dict = {"target": branch, "path": entry and entry["worktree"]}

    fetch = run(main, "fetch", "origin", "--prune")
    if fetch.returncode != 0:
        payload.update(worktree="skipped", branch="skipped", main="skipped", reasons=[f"fetch: {first_line(fetch)}"])
        return emit(payload, 1)

    is_merged = merged(main, branch)
    reasons: list[str] = []
    worktree, reason = worktree_part(main, entry, found == 0, is_merged)
    reasons += [f"worktree: {reason}"] if reason else []
    branch_state, reason = branch_part(main, branch, worktree, is_merged)
    reasons += [f"branch: {reason}"] if reason else []
    main_state, reason = main_part(main)
    reasons += [f"main: {reason}"] if reason else []
    payload.update(worktree=worktree, branch=branch_state, main=main_state, reasons=reasons)
    return emit(payload, 1 if reasons else 0)

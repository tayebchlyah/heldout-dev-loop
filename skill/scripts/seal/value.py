"""The seal value of a change under ACCEPTANCE_DIR, `devloop.py seal` and `seal --rebase-continue`."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from seal import ACCEPTANCE_DIR, PATCH_TRAILER, SEAL_TRAILER, git_dir, run_git

SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
TRAILER_LINE = re.compile(rf"^{SEAL_TRAILER}:.*$", re.MULTILINE)


def seal_value(entries: list[tuple[str, str]]) -> str:
    text = "".join(f"{path} {blob}\n" for path, blob in sorted(entries))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patch_value(repo: Path, *against: str) -> str:
    """sha256 of the `git patch-id --stable` of the `-U0` diff under ACCEPTANCE_DIR: survives a clean rebase (D1)."""
    # Plumbing (`diff-index`, `diff-tree`): the `git diff -U0 --no-color --no-renames` text with no user diff
    # config (noprefix, textconv, external diff); `--full-index`: a binary change hashes full blob ids.
    git = ["git", "-C", str(repo), "-c", "core.quotepath=off"]
    patch = subprocess.run([*git, *against, "-p", "-U0", "--no-renames", "--full-index", "--",
                            f":(top){ACCEPTANCE_DIR}/"], capture_output=True).stdout
    out = subprocess.run([*git, "patch-id", "--stable"], input=patch, capture_output=True).stdout.decode()
    return hashlib.sha256(out.split(" ", 1)[0].strip().encode()).hexdigest()


def empty_tree(repo: Path) -> str:
    return run_git(repo, "hash-object", "-t", "tree", "--stdin", stdin="").stdout.strip()


def staged_patch_value(repo: Path) -> str:
    head = run_git(repo, "rev-parse", "--verify", "-q", "HEAD").stdout.strip()
    return patch_value(repo, "diff-index", "--cached", head or empty_tree(repo))


def commit_patch_value(repo: Path, sha: str) -> str:
    parent = run_git(repo, "rev-parse", "--verify", "-q", f"{sha}^1").stdout.strip()
    return patch_value(repo, "diff-tree", "-r", "--no-commit-id", parent or empty_tree(repo), sha)


def split_raw(raw: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse `--raw -z --no-renames --no-abbrev` output: entries under ACCEPTANCE_DIR, and the paths outside it."""
    entries, outside = [], []
    fields = raw.split("\0")
    for meta, path in zip(fields[0::2], fields[1::2]):
        _, _, _, new_blob, status = meta.split(" ")
        if not path.startswith(ACCEPTANCE_DIR + "/"):
            outside.append(path)
        else:
            entries.append((path, "deleted" if status == "D" else new_blob))
    return entries, outside


def staged_entries(repo: Path) -> tuple[list[tuple[str, str]], list[str]]:
    return split_raw(run_git(repo, "diff", "--cached", "--raw", "-z", "--no-renames", "--no-abbrev").stdout)


def commit_entries(repo: Path, sha: str) -> tuple[list[tuple[str, str]], list[str]]:
    parent = run_git(repo, "rev-parse", "--verify", "-q", f"{sha}^1")
    against = [parent.stdout.strip(), sha] if parent.returncode == 0 else ["--root", sha]
    raw = run_git(repo, "diff-tree", "-r", "--raw", "-z", "--no-renames", "--no-abbrev", "--no-commit-id", *against)
    return split_raw(raw.stdout)


def checked_index(repo: Path) -> tuple[list[tuple[str, str]], str | None]:
    entries, outside = staged_entries(repo)
    if outside:
        return entries, f"staged change outside {ACCEPTANCE_DIR}/: {', '.join(outside)}"
    return entries, None


def seal(repo: Path, slug: str, message: str, reverts: list[str]) -> dict:
    if not SLUG.fullmatch(slug):
        return {"error": f"slug is not lower case words joined by -: {slug}"}
    entries, error = checked_index(repo)
    if error:
        return {"error": error}
    if not entries:
        return {"error": f"no staged change under {ACCEPTANCE_DIR}/"}
    full = []
    for sha in reverts:
        result = run_git(repo, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}")
        if result.returncode != 0:
            return {"error": f"--reverts is not a commit: {sha}"}
        full.append(result.stdout.strip())
    value = seal_value(entries)
    body = "".join(f"This reverts commit {sha}.\n" for sha in full)
    text = f"{message}\n\n{body}Acceptance: {slug}\n{SEAL_TRAILER}: {value}\n{PATCH_TRAILER}: {staged_patch_value(repo)}\n"
    result = run_git(repo, "commit", "-q", "--cleanup=verbatim", "-F", "-", stdin=text)
    if result.returncode != 0:
        return {"error": f"git commit failed: {(result.stderr or result.stdout).strip()}"}
    commit = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    return {"commit": commit, "seal": value, "files": sorted(path for path, _ in entries)}


def rebase_continue(repo: Path) -> dict:
    state = git_dir(repo) / "rebase-merge"
    if not state.is_dir():
        return {"error": "no rebase in progress"}
    entries, error = checked_index(repo)
    if error:
        return {"error": error}
    value = seal_value(entries)
    message_file = state / "message"
    old = message_file.read_text(encoding="utf-8") if message_file.exists() else ""
    line = f"{SEAL_TRAILER}: {value}"
    new = TRAILER_LINE.sub(line, old, count=1) if TRAILER_LINE.search(old) else f"{old.rstrip()}\n{line}\n"
    message_file.write_text(new, encoding="utf-8")
    result = run_git(repo, "-c", "core.editor=true", "rebase", "--continue", env={**os.environ, "GIT_EDITOR": "true"})
    files = sorted(path for path, _ in entries)
    if result.returncode == 0:
        return {"seal": value, "files": files, "rebase": "continued"}
    if not state.is_dir():
        return {"error": f"git rebase --continue failed: {(result.stderr or result.stdout).strip()}"}
    unmerged = run_git(repo, "diff", "--name-only", "--diff-filter=U", "-z").stdout.split("\0")
    return {"seal": value, "files": files, "rebase": "stopped", "conflicts": [p for p in unmerged if p]}

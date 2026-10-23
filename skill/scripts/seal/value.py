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
TRAILERS = re.compile(rf"^({SEAL_TRAILER}|{PATCH_TRAILER}):")
PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def split_last(message: str) -> tuple[str, str]:
    """The message up to and including the break before its last paragraph, and that last paragraph."""
    text = message.rstrip()
    breaks = list(PARAGRAPH_BREAK.finditer(text))
    return (text[:breaks[-1].end()], text[breaks[-1].end():]) if breaks else ("", text)


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
    body = "".join(f"This reverts commit {sha}.\n" for sha in full)

    def text(value: str, patch: str) -> str:
        return f"{message}\n\n{body}Acceptance: {slug}\n{SEAL_TRAILER}: {value}\n{PATCH_TRAILER}: {patch}\n"

    expected = text(seal_value(entries), staged_patch_value(repo))
    result = run_git(repo, "commit", "-q", "--cleanup=verbatim", "-F", "-", stdin=expected)
    if result.returncode != 0:
        return {"error": f"git commit failed: {(result.stderr or result.stdout).strip()}"}
    # D3: a pre-commit hook can rewrite the staged blobs, a commit-msg hook can add a paragraph after the trailers.
    entries, _ = commit_entries(repo, "HEAD")
    final = text(seal_value(entries), commit_patch_value(repo, "HEAD"))
    if run_git(repo, "log", "-1", "--format=%B", "HEAD").stdout.rstrip("\n") != final.rstrip("\n"):
        result = run_git(repo, "commit", "-q", "--amend", "--no-verify", "--cleanup=verbatim", "-F", "-", stdin=final)
        if result.returncode != 0:
            return {"error": f"git commit --amend failed: {(result.stderr or result.stdout).strip()}"}
    commit = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    return {"commit": commit, "seal": seal_value(entries), "files": sorted(path for path, _ in entries)}


def rebase_continue(repo: Path) -> dict:
    state = git_dir(repo) / "rebase-merge"
    if not state.is_dir():
        return {"error": "no rebase in progress"}
    entries, error = checked_index(repo)
    if error:
        return {"error": error}
    message_file = state / "message"
    old = message_file.read_text(encoding="utf-8") if message_file.exists() else ""
    # The `# Conflicts:` paragraph git appends: comment lines, stripped at the commit anyway.
    # shortcut: `#` only, read core.commentChar if a project sets another one.
    head, last = split_last("".join(line for line in old.splitlines(True) if not line.startswith("#")))
    if not TRAILER_LINE.search(last):
        return {"error": "the stopped commit is not a seal commit"}
    if not entries:
        return {"error": f"no staged change under {ACCEPTANCE_DIR}/"}
    value = seal_value(entries)
    trailers = f"{SEAL_TRAILER}: {value}\n{PATCH_TRAILER}: {staged_patch_value(repo)}"
    kept = "\n".join(line for line in last.splitlines() if not TRAILERS.match(line))
    message_file.write_text(f"{head}{kept}\n{trailers}\n".lstrip("\n"), encoding="utf-8")
    result = run_git(repo, "-c", "core.editor=true", "rebase", "--continue", env={**os.environ, "GIT_EDITOR": "true"})
    files = sorted(path for path, _ in entries)
    if result.returncode == 0:
        return {"seal": value, "files": files, "rebase": "continued"}
    if not state.is_dir():
        return {"error": f"git rebase --continue failed: {(result.stderr or result.stdout).strip()}"}
    unmerged = run_git(repo, "diff", "--name-only", "--diff-filter=U", "-z").stdout.split("\0")
    return {"seal": value, "files": files, "rebase": "stopped", "conflicts": [p for p in unmerged if p]}

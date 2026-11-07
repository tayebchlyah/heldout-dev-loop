"""The seal value of a change under ACCEPTANCE_DIR, `devloop.py seal` and `seal --rebase-continue`."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
from pathlib import Path

from devloop import emit
from seal import ACCEPTANCE_DIR, PARAGRAPH_BREAK, PATCH, PATCH_TRAILER, SEAL_TRAILER, git_dir, run_git
from seal.record import add_seal, patch_carriers, record_branch, replace_patch
import size

SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
TRAILER_LINE = re.compile(rf"^{SEAL_TRAILER}:.*$", re.MULTILINE)
TRAILERS = re.compile(rf"^({SEAL_TRAILER}|{PATCH_TRAILER}):")
ACCEPTANCE_LINE = re.compile(r"^Acceptance:[ \t]*(\S+)[ \t]*$", re.MULTILINE)


def split_last(message: str) -> tuple[str, str]:
    """The message up to and including the break before its last paragraph, and that last paragraph."""
    text = message.rstrip()
    breaks = list(PARAGRAPH_BREAK.finditer(text))
    return (text[:breaks[-1].end()], text[breaks[-1].end():]) if breaks else ("", text)


def seal_value(entries: list[tuple[str, str]]) -> str:
    text = "".join(f"{path} {blob}\n" for path, blob in sorted(entries))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PatchIdFailed(Exception):
    pass


def patch_id(repo: Path, *against: str) -> str:
    # allow-comment: dependency: plumbing (`diff-index`, `diff-tree`): the `git diff -U1 --no-color --no-renames` text with no user diff
    # config (noprefix, textconv, external diff); `--full-index`: a binary change hashes full blob ids.
    git = ["git", "-C", str(repo), "-c", "core.quotepath=off"]
    patch = subprocess.run([*git, *against, "-p", "-U1", "--no-renames", "--full-index", "--",
                            f":(top){ACCEPTANCE_DIR}/"], capture_output=True).stdout
    result = subprocess.run([*git, "patch-id", "--verbatim"], input=patch, capture_output=True)
    first = result.stdout.decode().split(" ", 1)[0].strip()
    if result.returncode != 0 or not first:
        raise PatchIdFailed(f"git patch-id failed: {result.stderr.decode(errors='replace').strip() or 'no output'}")
    return hashlib.sha256(first.encode()).hexdigest()


def patch_value(repo: Path, *against: str) -> str | None:
    try:
        return patch_id(repo, *against)
    except PatchIdFailed:
        return None


def empty_tree(repo: Path) -> str:
    return run_git(repo, "hash-object", "-t", "tree", "--stdin", stdin="").stdout.strip()


def staged_diff(repo: Path) -> list[str]:
    head = run_git(repo, "rev-parse", "--verify", "-q", "HEAD").stdout.strip()
    return ["diff-index", "--cached", head or empty_tree(repo)]


def commit_diff(repo: Path, sha: str) -> list[str]:
    parent = run_git(repo, "rev-parse", "--verify", "-q", f"{sha}^1").stdout.strip()
    return ["diff-tree", "-r", "--no-commit-id", parent or empty_tree(repo), sha]


def commit_patch_value(repo: Path, sha: str) -> str | None:
    return patch_value(repo, *commit_diff(repo, sha))


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
    try:
        over = size.staged(repo, entries)
    except size.GitError as error:
        return {"error": str(error)}
    if over:
        return {"error": "file over the size limit", "size": over}
    full = []
    for sha in reverts:
        result = run_git(repo, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}")
        if result.returncode != 0:
            return {"error": f"--reverts is not a commit: {sha}"}
        full.append(result.stdout.strip())
    body = "".join(f"This reverts commit {sha}.\n" for sha in full)

    def text(value: str, patch: str) -> str:
        return f"{message}\n\n{body}Acceptance: {slug}\n{SEAL_TRAILER}: {value}\n{PATCH_TRAILER}: {patch}\n"

    try:
        expected = text(seal_value(entries), patch_id(repo, *staged_diff(repo)))
    except PatchIdFailed as error:
        return {"error": str(error)}
    result = run_git(repo, "commit", "-q", "--cleanup=verbatim", "-F", "-", stdin=expected)
    if result.returncode != 0:
        return {"error": f"git commit failed: {(result.stderr or result.stdout).strip()}"}
    # D3: a pre-commit hook can rewrite the staged blobs, a commit-msg hook can add a paragraph after the trailers.
    entries, _ = commit_entries(repo, "HEAD")
    try:
        patch = patch_id(repo, *commit_diff(repo, "HEAD"))
    except PatchIdFailed as error:
        return {"error": str(error), "commit": run_git(repo, "rev-parse", "HEAD").stdout.strip()}
    final = text(seal_value(entries), patch)
    if run_git(repo, "log", "-1", "--format=%B", "HEAD").stdout.rstrip("\n") != final.rstrip("\n"):
        result = run_git(repo, "commit", "-q", "--amend", "--no-verify", "--cleanup=verbatim", "-F", "-", stdin=final)
        if result.returncode != 0:
            return {"error": f"git commit --amend failed: {(result.stderr or result.stdout).strip()}"}
    commit = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    files = sorted(path for path, _ in entries)
    branch = record_branch(repo)
    if branch is not None:
        try:
            add_seal(repo, {"branch": branch, "commit": commit, "patches": [patch], "seal": seal_value(entries),
                            "feature": slug, "paths": files})
        except (OSError, ValueError) as error:
            return {"error": f"seal record not written: {error}", "commit": commit}
    return {"commit": commit, "seal": seal_value(entries), "files": files}


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
    try:
        patch = patch_id(repo, *staged_diff(repo))
    except PatchIdFailed as error:
        return {"error": str(error)}
    old_patch = next((value for value in PATCH.findall(last) if value), "")
    feature = next(iter(ACCEPTANCE_LINE.findall(last)), "")
    branch = record_branch(repo)
    kept = "\n".join(line for line in last.splitlines() if not TRAILERS.match(line))
    message_file.write_text(f"{head}{kept}\n{SEAL_TRAILER}: {value}\n{PATCH_TRAILER}: {patch}\n".lstrip("\n"),
                            encoding="utf-8")
    result = run_git(repo, "-c", "core.editor=true", "rebase", "--continue", env={**os.environ, "GIT_EDITOR": "true"})
    files = sorted(path for path, _ in entries)
    if result.returncode == 0:
        out = {"seal": value, "files": files, "rebase": "continued"}
    elif not state.is_dir():
        return {"error": f"git rebase --continue failed: {(result.stderr or result.stdout).strip()}"}
    else:
        unmerged = run_git(repo, "diff", "--name-only", "--diff-filter=U", "-z").stdout.split("\0")
        out = {"seal": value, "files": files, "rebase": "stopped", "conflicts": [p for p in unmerged if p]}
    if branch is not None:
        head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
        commit = patch_carriers(repo).get(patch, [head])[0]
        try:
            replace_patch(repo, old_patch, {"branch": branch, "commit": commit,
                                            "patches": [old_patch, patch] if old_patch else [patch],
                                            "seal": value, "feature": feature, "paths": files})
        except (OSError, ValueError) as error:
            return {"error": f"seal record not written: {error}", "commit": commit}
    return out


def cmd_seal(args: argparse.Namespace) -> int:
    if args.rebase_continue == bool(args.feature or args.message or args.reverts) or (
            not args.rebase_continue and not (args.feature and args.message)):
        return emit({"error": "seal needs --feature and --message, or --rebase-continue alone"}, 2)

    repo = Path.cwd()
    out = rebase_continue(repo) if args.rebase_continue else seal(
        repo, args.feature, args.message, args.reverts or [])
    return emit(out, 1 if "error" in out else 0)

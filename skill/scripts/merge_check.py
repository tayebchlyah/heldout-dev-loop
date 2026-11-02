"""`merge-check`: the Stage 7 merge decision, auto or human, with its reasons."""

from __future__ import annotations

import argparse
import collections
import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path

import devloop
from devloop import emit, find_plan, frontmatter, git, specs
from notes_path import NotesPathError, main_ref, resolve
from status import rebasing


TEST_OR_DOC_NAMES = ("test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*", "README.md", "CHANGELOG.md")
CONFIG_TYPES = (".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf")
DOC_TYPES = (".md", ".rst", ".txt", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg")


def slug_branches(repo: Path, slug: str) -> list[str]:
    names = (git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads") or "").splitlines()
    return sorted(n for n in names if n.lower().endswith(f"/{slug}"))


def branch_worktree(repo: Path, branch: str) -> Path | None:
    worktree: Path | None = None
    for line in (git(repo, "worktree", "list", "--porcelain") or "").splitlines():
        if line.startswith("worktree "):
            worktree = Path(line[len("worktree "):])
        elif line == f"branch refs/heads/{branch}" or (line == "detached" and worktree
                                                         and rebasing(worktree) == branch):
            return worktree
    return None


def is_test_or_doc(path: str) -> bool:
    """In order: a folder `.github`: code. A folder `tests` or `test`: test. A config file type: code. A folder
    `docs` and a doc file type: doc. A test or doc file name: test or doc. Else code, kit Markdown too: it is
    behaviour. Folder matches are case-sensitive, file types are not."""
    *dirs, name = path.split("/")
    if ".github" in dirs:
        return False
    if {"tests", "test"} & set(dirs):
        return True
    if name.lower().endswith(CONFIG_TYPES):
        return False
    if "docs" in dirs and name.lower().endswith(DOC_TYPES):
        return True
    return any(fnmatch.fnmatchcase(name, p) for p in TEST_OR_DOC_NAMES)


def read_config(repo: Path, rev: str) -> dict | None:
    """`dev-loop.json` at `rev` as an object, or None when absent, not JSON or not an object."""
    try:  # Bytes: a copy that is not UTF-8 does not parse (UnicodeDecodeError is a ValueError).
        config = json.loads(subprocess.run(["git", "-C", str(repo), "show", f"{rev}:dev-loop.json"],
                                           capture_output=True, check=True).stdout)
    except (subprocess.CalledProcessError, ValueError):
        return None
    return config if isinstance(config, dict) else None


MERGE_VALUES = ("auto", "human")


def config_shape_ok(config: dict) -> bool:
    """A known `merge`, and `merge_human_paths` items non-empty after strip and not ending in `/` (a directory
    pattern never matches under fnmatchcase: write `src/auth/*`)."""
    paths = config.get("merge_human_paths", [])
    return (config.get("merge", "auto") in MERGE_VALUES and isinstance(paths, list)
            and all(isinstance(p, str) and p.strip() and not p.endswith("/") for p in paths))


class GitFailed(Exception):
    """A merge-check git call failed: the command exits 1 with `error`, no `merge` value (fail closed)."""


def git_out(repo: Path, *args: str) -> str:
    """Like `git`, for merge-check: a failure raises GitFailed instead of reading as empty, and bytes that are not
    UTF-8 (a branch path name) read as U+FFFD instead of crashing."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode != 0:
        raise GitFailed(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout.decode(errors="replace")


def changed_paths(repo: Path, *revs: str) -> list[str]:
    """`git diff --name-only --no-renames`, sorted: a rename counts as both paths. `--ignore-submodules=none`: a
    `.gitmodules` or user `ignore = all` cannot hide a submodule pointer move."""
    out = git_out(repo, "diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", *revs)
    return sorted(p for p in out.split("\0") if p)


def patch_id(repo: Path, commit: str) -> str | None:
    """`git patch-id --verbatim` (stable, whitespace kept) of the commit against its first parent: "" for an empty
    diff, None when git fails (no first parent, for one). The diff flags keep `.gitattributes` and user config from
    rendering two different changes the same way."""
    diff = subprocess.run(["git", "-C", str(repo), "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                           "--no-renames", "--ignore-submodules=none", "-a", f"{commit}^1", commit], capture_output=True)
    if diff.returncode != 0:
        return None
    out = subprocess.run(["git", "-C", str(repo), "patch-id", "--verbatim"], input=diff.stdout,
                         capture_output=True)
    if out.returncode != 0:
        return None
    return out.stdout.split()[0].decode() if out.stdout.split() else ""


def reviewed_head(worktree: Path | None, notes: Path, spec: Path | None) -> str | None:
    """The sha of the last `Feature gate: green at` ledger line (Tracks M and L), else of the `review-done` marker."""
    if worktree is None:
        return None
    if spec is not None:
        plan = find_plan(notes, spec)
        ledger = devloop.ledger(worktree, plan) if plan else None
        found = re.findall(r"^Feature gate: green at (\S+)", ledger.read_text(errors="replace"), re.MULTILINE) \
            if ledger and ledger.is_file() else []
        return found[-1] if found else None
    git_dir = git(worktree, "rev-parse", "--absolute-git-dir")
    marker = Path(git_dir) / "dev-loop" / "review-done" if git_dir else None
    lines = marker.read_text(errors="replace").splitlines() if marker and marker.is_file() else []
    return (lines[0].strip() or None) if lines else None


def cmd_merge_check(args: argparse.Namespace) -> int:
    try:
        return merge_check(args)
    except GitFailed as exc:
        return emit({"error": str(exc)}, 1)


def merge_check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    slug = args.feature.lower() if args.feature else None
    worktree: Path | None
    if slug is None:
        top = git(repo, "rev-parse", "--show-toplevel")
        branch = git(repo, "symbolic-ref", "--short", "-q", "HEAD") or ""
        if not top or "/" not in branch:
            return emit({"error": "no feature: run merge-check on a feature branch or pass --feature"}, 1)
        worktree, slug = Path(top), branch.rsplit("/", 1)[1].lower()
    else:
        branches = slug_branches(repo, slug)
        if not branches:
            return emit({"error": f"no feature {slug}: no branch <prefix>/{slug}"}, 1)
        if len(branches) > 1:
            return emit({"error": f"feature {slug} matches more than one branch: {', '.join(branches)}"}, 1)
        branch = branches[0]
        worktree = branch_worktree(repo, branch)
    head = (git_out(worktree, "rev-parse", "HEAD") if worktree
            else git_out(repo, "rev-parse", f"refs/heads/{branch}")).strip()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    matches = [path for name, path in specs(notes) if name == slug]
    spec = matches[-1] if matches else None
    track = (frontmatter(spec).get("track") or None) if spec else "S"
    ref = main_ref(repo)
    if ref is None:
        return emit({"error": "no refs/remotes/origin/main and no refs/heads/main"}, 1)

    reasons: list[dict] = []
    config = read_config(repo, ref)
    if config is not None and not config_shape_ok(config):
        config = None  # Fail closed: an unknown value must not merge without a human.
    if config is None:
        reasons.append({"reason": "config-unreadable"})
    elif config.get("merge") == "human":
        reasons.append({"reason": "merge-human"})
    if (track or "").upper() == "L":
        reasons.append({"reason": "track-l"})
    if config is not None:
        patterns = config.get("merge_human_paths", [])
        for path in changed_paths(repo, f"{ref}...{head}"):
            pattern = next((p for p in patterns if fnmatch.fnmatchcase(path, p)), None)
            if pattern is not None:
                reasons.append({"reason": "human-path", "path": path, "pattern": pattern})
        trusted = (config.get("merge"), config.get("merge_human_paths"), config.get("file_size_exempt", []),
                   config.get("commands"))
        branch_config = read_config(repo, head)
        if branch_config is None:
            changed = trusted != (None, None, [], None)
        else:
            changed = (branch_config.get("merge"), branch_config.get("merge_human_paths"),
                       branch_config.get("file_size_exempt", []), branch_config.get("commands")) != trusted
        if changed:
            reasons.append({"reason": "config-changed"})

    reviewed = reviewed_head(worktree, notes, spec)
    # Only a sha counts, resolved to the full sha in the feature worktree: a ref name would follow later commits.
    if reviewed is not None:
        reviewed = git(worktree, "rev-parse", "--verify", "-q", f"{reviewed}^{{commit}}") \
            if re.fullmatch(r"[0-9a-f]{7,40}", reviewed) else None
    minor: list[dict] = []
    if reviewed is None:
        reasons.append({"reason": "no-review"})
    else:
        base = git(repo, "merge-base", reviewed, ref)
        reviewed_pids = {c: patch_id(repo, c) for c in
                         git_out(repo, "rev-list", "--reverse", f"{base}..{reviewed}" if base else reviewed).split()}
        left = collections.Counter(p for p in reviewed_pids.values() if p)
        landing: collections.Counter[str | None] = collections.Counter()
        for commit in git_out(repo, "rev-list", "--reverse", f"{ref}..{head}").split():
            pid = patch_id(repo, commit)
            landing[pid] += 1
            if pid == "":
                continue
            if pid is not None and left[pid] > 0:
                left[pid] -= 1
                continue
            if pid is None:  # Fail closed: a diff git cannot compute is unreviewed, whatever its paths.
                try:
                    paths = changed_paths(repo, f"{commit}^1", commit)
                except GitFailed:
                    paths = []
                reasons.append({"reason": "unreviewed", "commit": commit, "paths": paths})
                continue
            paths = changed_paths(repo, f"{commit}^1", commit)
            code = [p for p in paths if not is_test_or_doc(p)]
            if code:
                reasons.append({"reason": "unreviewed", "commit": commit, "paths": code})
            else:
                minor.append({"commit": commit, "paths": paths})
        # `git cherry` marks `-` a reviewed commit main already holds: dropping it in a rebase is fine.
        cherry_out = git_out(repo, "cherry", ref, reviewed, *([base] if base else []))
        upstream = {line[2:] for line in cherry_out.splitlines() if line.startswith("- ")}
        for commit, pid in reviewed_pids.items():
            if not pid:
                continue
            if landing[pid] > 0:
                landing[pid] -= 1
            elif commit not in upstream:
                reasons.append({"reason": "dropped", "commit": commit})
    return emit({"merge": "human" if reasons else "auto", "reasons": reasons, "reviewed": reviewed,
                 "head": head, "branch": branch, "unreviewed_minor": minor, "feature": slug, "track": track}, 0)

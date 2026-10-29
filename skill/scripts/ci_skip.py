"""`ci-skip`: may CI skip a diff that is only a release (version files, `CHANGELOG.md`) or a kit upgrade.

Shared by `devloop.py` (`ci-skip`) and `release.py` (`release-check`).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

import release
from devloop import emit
from merge_check import read_config

VERSION = re.compile(rb"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-rc\.[1-9]\d*)?")
HEADING = re.compile(rb"^## \[v?([^\]]+)\]", re.M)
MERGED = {".claude/settings.json", ".codex/hooks.json", ".codex/config.toml"}


class ConfigError(Exception):
    """`version_files` in `dev-loop.json` at the head is not a list of repo-relative paths."""


def changed(repo: Path, base: str, head: str) -> list[str]:
    """The paths changed from the merge base of `base` and `head` to `head`."""
    diff = release.run_git(repo, "diff", "--name-only", "-z", "--no-renames", f"{base}...{head}")
    return [p for p in diff.stdout.split("\0") if p] if diff.returncode == 0 else []


def version_files(repo: Path, head: str) -> list[str]:
    config = read_config(repo, head) or {}
    files = config.get("version_files", [])
    if not (isinstance(files, list) and all(
            isinstance(p, str) and p and not p.startswith("/") and ".." not in p.split("/") for p in files)):
        raise ConfigError("version_files is not a list of repo-relative paths")
    return files


def version_pairs(repo: Path, mb: str, head: str, path: str) -> set[tuple[str, str]] | None:
    """The (old, new) version token pairs of a version-only diff of `path`, else None."""
    diff = subprocess.run(["git", "-C", str(repo), "diff", "-U0", "--text", "--no-renames", mb, head, "--", path],
                          capture_output=True)
    if diff.returncode != 0:
        return None
    hunks: list[tuple[list[bytes], list[bytes]]] = []
    for line in diff.stdout.split(b"\n"):
        if not hunks and line.startswith((b"old mode ", b"new mode ")):
            return None
        if line.startswith(b"@@"):
            hunks.append(([], []))
        elif hunks and line[:1] in (b"-", b"+"):
            hunks[-1][line[:1] == b"+"].append(line[1:])
    if not hunks:
        return None
    pairs: set[tuple[str, str]] = set()
    for removed, added in hunks:
        if len(removed) != len(added):
            return None
        for old, new in zip(removed, added):
            olds, news = VERSION.findall(old), VERSION.findall(new)
            if len(olds) != len(news) or VERSION.sub(b"\0", old) != VERSION.sub(b"\0", new):
                return None
            pairs |= {(a.group(0).decode(), b.group(0).decode())
                      for a, b in zip(VERSION.finditer(old), VERSION.finditer(new)) if a.group(0) != b.group(0)}
    return pairs


def released(repo: Path, head: str) -> str | None:
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{head}:CHANGELOG.md"], capture_output=True)
    match = HEADING.search(shown.stdout) if shown.returncode == 0 else None
    return match.group(1).decode("utf-8", "replace") if match else None


def blocking(repo: Path, base: str, head: str, paths: list[str]) -> list[str]:
    """The sorted paths of `paths` that no rule allows: `CHANGELOG.md`, a version-only change of a `version_files`
    path, a `dev-loop.json` change of `version` only, a kit copy of a kit upgrade that equals the kit the merge
    base's own installer builds (none in a repo without the kit source). `base` and `head` are commit shas; contents
    compare from their merge base. Raises ConfigError."""
    listed = version_files(repo, head)
    merged = release.run_git(repo, "merge-base", base, head)
    if merged.returncode != 0:
        return sorted(paths)
    mb = merged.stdout.strip()
    allowed = {"CHANGELOG.md"}
    pairs: set[tuple[str, str]] = set()
    versioned = []
    for path in (p for p in listed if p in paths):
        found = version_pairs(repo, mb, head, path)
        if found is not None:
            pairs |= found
            versioned.append(path)
    if len(pairs) == 1 and next(iter(pairs))[1] == released(repo, head) and "CHANGELOG.md" in paths:
        allowed.update(versioned)
    old, new = read_config(repo, mb), read_config(repo, head)
    if old is not None and new is not None:
        old.pop("version", None)
        new.pop("version", None)
        if old == new:
            allowed.add("dev-loop.json")
    was, now = release.version_at(repo, mb), release.version_at(repo, head)
    ref = release.kit_reference(repo, mb) if was and now and was != now else None
    if ref is not None:
        allowed |= release.kit_matches(repo, head, set(paths), ref)
        tree = release.run_git(repo, "ls-tree", "-r", "-z", "--name-only", "--full-tree", head)
        present = set(tree.stdout.split("\0"))
        allowed |= {p for p in paths if p not in present and p.startswith(release.KIT_ROOTS) and p not in ref}
    return sorted(p for p in paths if p not in allowed or p in MERGED)


def cmd_ci_skip(args: argparse.Namespace) -> int:
    output = os.environ.get("GITHUB_OUTPUT")
    if args.github_output and not output:
        return emit({"error": "--github-output needs GITHUB_OUTPUT"}, 2)
    repo = Path(args.repo).resolve()
    if release.run_git(repo, "rev-parse", "--git-dir").returncode != 0:
        return emit({"error": f"not a git repo: {repo}"}, 1)
    shas = []
    for rev in (args.base, args.head):
        resolved = release.run_git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}")
        if resolved.returncode != 0:
            return emit({"error": f"cannot resolve {rev}"}, 1)
        shas.append(resolved.stdout.strip())
    base, head = shas
    paths = changed(repo, base, head)
    try:
        block = blocking(repo, base, head, paths)
    except ConfigError as exc:
        return emit({"error": str(exc)}, 1)
    skip = bool(paths) and not block
    kind = ("release" if "CHANGELOG.md" in paths else "kit-upgrade") if skip else None
    if args.github_output:
        with open(output, "a") as out:
            out.write(f"skip={'true' if skip else 'false'}\n")
    return emit({"skip": skip, "kind": kind, "blocking": block, "base": base, "head": head}, 0)

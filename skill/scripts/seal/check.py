"""`devloop.py seal-check`: the net change of ACCEPTANCE_DIR on the branch against its seal commits, the kit, and the taint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from devloop import emit, git
from seal import ACCEPTANCE_DIR, PATCH_TRAILER, SEAL_TRAILER, merge_base, run_git
from seal.audit import taint_file, taint_state
from seal.value import commit_entries, commit_patch_value, seal_value
from seal.weakened import TEST_FILES

TRAILER = re.compile(rf"^{SEAL_TRAILER}:[ \t]*(\S*)[ \t]*$", re.MULTILINE)
PATCH = re.compile(rf"^{PATCH_TRAILER}:[ \t]*(\S*)[ \t]*$", re.MULTILINE)


def violation(reason: str, commit: str | None = None, path: str | None = None, commits: list[str] | None = None) -> dict:
    found = {"reason": reason, "commit": commit, "path": path}
    return found if commits is None else {**found, "commits": commits}


def z_paths(text: str) -> list[str]:
    return [path for path in text.split("\0") if path]


def tree_blobs(repo: Path, rev: str) -> dict[str, str]:
    blobs = {}
    for line in z_paths(run_git(repo, "ls-tree", "-r", "-z", rev, "--", f":(top){ACCEPTANCE_DIR}/").stdout):
        meta, path = line.split("\t", 1)
        blobs[path] = meta.split()[2]
    return blobs


def commit_violations(repo: Path, base: str) -> list[dict]:
    found = []
    sealed: dict[str, str] = {}  # path -> its blob (or `deleted`) in the newest valid seal commit
    seals: set[str] = set()  # the valid seal commits
    expected = tree_blobs(repo, base)
    before: list[tuple[str, str]] = []
    # The body, not `%(trailers)`: git sees no trailer block once `This reverts commit` lines share its paragraph.
    for entry in z_paths(run_git(repo, "log", "--reverse", "-z", "--format=%H%n%B", f"{base}..HEAD").stdout):
        sha, _, body = entry.partition("\n")
        # The last paragraph only: `seal` writes the trailers there; a quote earlier in the body is no seal.
        last = re.split(r"\n[ \t]*\n", body.strip())[-1]
        values = TRAILER.findall(last)
        if not values:
            continue
        entries, outside = commit_entries(repo, sha)
        if outside or not entries:
            found.append(violation("bad-seal", commit=sha))
            continue
        # Two trailers fail too: a seal commit carries exactly the one value of its own diff.
        # The patch value (D1): a clean rebase that merged an upstream change into a sealed file keeps the seal.
        if values != [seal_value(entries)]:
            patch = commit_patch_value(repo, sha)
            if patch is None or PATCH.findall(last) != [patch]:
                found.append({**violation("bad-seal", commit=sha), **({"patch": "failed"} if patch is None else {})})
                continue
        parent = tree_blobs(repo, f"{sha}^1")
        before += [(sha, path) for path, _ in entries if parent.get(path, "deleted") != expected.get(path, "deleted")]
        expected.update(entries)
        sealed.update(entries)
        seals.add(sha)
    changed = z_paths(run_git(repo, "diff", "--name-only", "-z", "--no-renames", base, "HEAD", "--",
                              f":(top){ACCEPTANCE_DIR}/").stdout)
    at_head = tree_blobs(repo, "HEAD")

    def commits(path: str) -> list[str]:
        """D6: the commits that touched the path, valid seal commits left out."""
        log = run_git(repo, "log", "--reverse", "--format=%H", "--no-renames", f"{base}..HEAD", "--",
                      f":(top,literal){path}").stdout.split()
        return [sha for sha in log if sha not in seals]

    found += [violation("unsealed", path=path, commits=commits(path)) for path in changed if path not in sealed]
    found += [violation("changed-after-seal", path=path, commits=commits(path)) for path, blob in sorted(sealed.items())
              if at_head.get(path, "deleted") != blob]
    found += [violation("changed-before-seal", commit=sha, path=path, commits=commits(path)) for sha, path in before]
    return found


def uncommitted_violations(repo: Path) -> list[dict]:
    status = run_git(repo, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all",
                     "--", f":(top){ACCEPTANCE_DIR}/").stdout
    # D4: an untracked path counts only when it is a test file (not `__pycache__/x.pyc`, a fixture, a log).
    return [violation("uncommitted", path=entry[3:]) for entry in z_paths(status)
            if not entry.startswith("??") or any(fnmatchcase(PurePosixPath(entry[3:]).name, g) for g in TEST_FILES)]


MANIFEST = ".dev-loop/MANIFEST.json"
KIT_DIRS = (".agents/skills/dev-loop/", ".agents/skills/pause/", ".agents/skills/continue/", ".agents/skills/release-audit/",
            ".agents/skills/garden/", ".agents/skills/release/", ".agents/skills/release-notes/", ".dev-loop/")
LINK_PREFIX = "link:"


def read_json(repo: Path, rev: str | None, path: str) -> dict:
    """The JSON object at `<rev>:<path>`, or in the working tree when `rev` is None; {} when absent or invalid."""
    try:
        text = (repo / path).read_text() if rev is None else run_git(repo, "show", f"{rev}:{path}").stdout
        found = json.loads(text)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def kit_entry(repo: Path, head: str | None, path: str) -> str | None:
    """The MANIFEST entry the path has at HEAD, or in the working tree when `head` is None; None when absent."""
    if head is None:
        full = repo / path
        if full.is_symlink():
            return LINK_PREFIX + os.readlink(full)
        try:
            return "sha256:" + hashlib.sha256(full.read_bytes()).hexdigest()
        except OSError:  # absent, or a folder
            return None
    tree = run_git(repo, "ls-tree", "-z", head, "--", f":(top,literal){path}").stdout.rstrip("\0")
    if not tree:
        return None
    mode, kind, blob = tree.split("\t", 1)[0].split()
    if kind != "blob":
        return None
    # Bytes, not `run_git`: that decodes text, and the hash is of the raw bytes.
    data = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", blob], capture_output=True).stdout
    return LINK_PREFIX + data.decode("utf-8", "replace") if mode == "120000" else "sha256:" + hashlib.sha256(data).hexdigest()


def kit_violations(repo: Path, base: str, uncommitted: bool) -> list[dict]:
    """A kit path differs from `<base>` and does not match the current MANIFEST: only `upgrade` moves the kit."""
    head = None if uncommitted else "HEAD"
    manifests = [read_json(repo, rev, MANIFEST).get("files") for rev in (base, head)]
    if not any(isinstance(found, dict) for found in manifests):
        return []
    current = manifests[1] if isinstance(manifests[1], dict) else {}
    files = {key for found in manifests if isinstance(found, dict) for key in found}
    pathspecs = [f":(top){d}" for d in KIT_DIRS] + [f":(top,literal){key}" for key in sorted(files)]
    candidates = z_paths(run_git(repo, "diff", "--name-only", "-z", "--no-renames", base,
                                 *([] if uncommitted else ["HEAD"]), "--", *pathspecs).stdout)
    if uncommitted:
        untracked = z_paths(run_git(repo, "ls-files", "-z", "--others", "--exclude-standard", "--",
                                    *(f":(top){d}" for d in KIT_DIRS)).stdout)
        candidates += [path for path in untracked if "__pycache__" not in PurePosixPath(path).parts
                       and not path.endswith(".pyc")]
    found = []
    for path in sorted(set(candidates)):
        entry = kit_entry(repo, head, path)
        if path == MANIFEST or (path in current and entry == current[path]) or (entry is None and path not in current):
            continue
        found.append(violation("kit-changed", path=path))
    return found


def check(repo: Path, uncommitted: bool) -> dict:
    base = merge_base(repo)
    violations = commit_violations(repo, base) if base else []
    if base:
        violations += kit_violations(repo, base, uncommitted)
    if uncommitted:
        violations += uncommitted_violations(repo)
    try:
        state = taint_state(repo)
        # `<base>` itself counts: a tainted subagent with no commit on a fresh branch ends there.
        recent = set(run_git(repo, "rev-list", f"{base}..HEAD" if base else "HEAD").stdout.split()) | {base}
        warnings = [item for item in state["closed"] + state["skipped"] if item.get("end") in recent]
        taint = state["open"]
    except (ValueError, AttributeError, TypeError, KeyError):  # taint.json does not parse or has the wrong shape
        violations.append(violation("taint-unreadable", path=str(taint_file(repo))))
        taint, warnings = [], []
    return {"ok": not violations and not taint, "violations": violations, "taint": taint, "warnings": warnings}


def cmd_seal_check(args: argparse.Namespace) -> int:
    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top is None:
        return emit({"error": "not a git repository"}, 2)
    out = check(Path(top), args.uncommitted)
    return emit(out, 0 if out["ok"] else 1)

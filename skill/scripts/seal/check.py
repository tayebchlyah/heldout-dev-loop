"""`devloop.py seal-check`: the net change of ACCEPTANCE_DIR on the branch against its seal commits, the kit, and the taint."""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

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


def commit_violations(repo: Path, base: str) -> list[dict]:
    found = []
    sealed: dict[str, str] = {}  # path -> its blob (or `deleted`) in the newest valid seal commit
    seals: set[str] = set()  # the valid seal commits
    # The body, not `%(trailers)`: git sees no trailer block once `This reverts commit` lines share its paragraph.
    for entry in z_paths(run_git(repo, "log", "--reverse", "-z", "--format=%H%n%B", f"{base}..HEAD").stdout):
        sha, _, body = entry.partition("\n")
        # The last paragraph only: `seal` writes the trailers there; a quote earlier in the body is no seal.
        last = re.split(r"\n[ \t]*\n", body.strip())[-1]
        values = TRAILER.findall(last)
        if not values:
            continue
        entries, outside = commit_entries(repo, sha)
        # Two trailers fail too: a seal commit carries exactly the one value of its own diff.
        # The patch value (D1): a clean rebase that merged an upstream change into a sealed file keeps the seal.
        if outside or not entries or (values != [seal_value(entries)]
                                       and PATCH.findall(last) != [commit_patch_value(repo, sha)]):
            found.append(violation("bad-seal", commit=sha))
            continue
        sealed.update(entries)
        seals.add(sha)
    pathspec = f":(top){ACCEPTANCE_DIR}/"
    changed = z_paths(run_git(repo, "diff", "--name-only", "-z", "--no-renames", base, "HEAD", "--", pathspec).stdout)
    at_head = {}
    for line in z_paths(run_git(repo, "ls-tree", "-r", "-z", "HEAD", "--", pathspec).stdout):
        meta, path = line.split("\t", 1)
        at_head[path] = meta.split()[2]

    def commits(path: str) -> list[str]:
        """D6: the commits that touched the path, valid seal commits left out."""
        log = run_git(repo, "log", "--reverse", "--format=%H", "--no-renames", f"{base}..HEAD", "--",
                      f":(top,literal){path}").stdout.split()
        return [sha for sha in log if sha not in seals]

    found += [violation("unsealed", path=path, commits=commits(path)) for path in changed if path not in sealed]
    found += [violation("changed-after-seal", path=path, commits=commits(path)) for path, blob in sorted(sealed.items())
              if at_head.get(path, "deleted") != blob]
    return found


def uncommitted_violations(repo: Path) -> list[dict]:
    status = run_git(repo, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all",
                     "--", f":(top){ACCEPTANCE_DIR}/").stdout
    # D4: an untracked path counts only when it is a test file (not `__pycache__/x.pyc`, a fixture, a log).
    return [violation("uncommitted", path=entry[3:]) for entry in z_paths(status)
            if not entry.startswith("??") or any(fnmatchcase(PurePosixPath(entry[3:]).name, g) for g in TEST_FILES)]


MANIFEST = ".dev-loop/MANIFEST.json"
KIT_DIRS = (".agents/skills/dev-loop/", ".dev-loop/")


def read_json(repo: Path, rev: str | None, path: str) -> dict:
    """The JSON object at `<rev>:<path>`, or in the working tree when `rev` is None; {} when absent or invalid."""
    try:
        text = (repo / path).read_text() if rev is None else run_git(repo, "show", f"{rev}:{path}").stdout
        found = json.loads(text)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def kit_violations(repo: Path, base: str, uncommitted: bool) -> list[dict]:
    """A kit file differs from `<base>` while the `dev-loop.json` version did not change: only `upgrade` moves the kit."""
    head = None if uncommitted else "HEAD"
    if read_json(repo, base, "dev-loop.json").get("version") != read_json(repo, head, "dev-loop.json").get("version"):
        return []
    manifests = [read_json(repo, rev, MANIFEST).get("files") for rev in (base, head)]
    files = {key for found in manifests if isinstance(found, dict) for key in found}
    if not files:
        return []
    pathspecs = [f":(top){d}" for d in KIT_DIRS] + [f":(top,literal){key}" for key in sorted(files)]
    changed = z_paths(run_git(repo, "diff", "--name-only", "-z", "--no-renames", base,
                              *([] if uncommitted else ["HEAD"]), "--", *pathspecs).stdout)
    return [violation("kit-changed", path=path) for path in changed]


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

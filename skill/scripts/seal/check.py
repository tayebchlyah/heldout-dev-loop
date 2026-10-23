"""`devloop.py seal-check`: the net change of ACCEPTANCE_DIR on the branch against its seal commits, and the taint."""

from __future__ import annotations

import re
from pathlib import Path

from seal import ACCEPTANCE_DIR, SEAL_TRAILER, merge_base, run_git
from seal.audit import taint_file, taint_state
from seal.value import commit_entries, seal_value

TRAILER = re.compile(rf"^{SEAL_TRAILER}:[ \t]*(\S*)[ \t]*$", re.MULTILINE)


def violation(reason: str, commit: str | None = None, path: str | None = None) -> dict:
    return {"reason": reason, "commit": commit, "path": path}


def z_paths(text: str) -> list[str]:
    return [path for path in text.split("\0") if path]


def commit_violations(repo: Path, base: str) -> list[dict]:
    found = []
    sealed: dict[str, str] = {}  # path -> its blob (or `deleted`) in the newest valid seal commit
    # The body, not `%(trailers)`: git sees no trailer block once `This reverts commit` lines share its paragraph.
    for entry in z_paths(run_git(repo, "log", "--reverse", "-z", "--format=%H%n%B", f"{base}..HEAD").stdout):
        sha, _, body = entry.partition("\n")
        # The last paragraph only: `seal` writes the trailers there; a quote earlier in the body is no seal.
        values = TRAILER.findall(re.split(r"\n[ \t]*\n", body.strip())[-1])
        if not values:
            continue
        entries, outside = commit_entries(repo, sha)
        # Two trailers fail too: a seal commit carries exactly the one value of its own diff.
        if outside or not entries or values != [seal_value(entries)]:
            found.append(violation("bad-seal", commit=sha))
            continue
        sealed.update(entries)
    pathspec = f":(top){ACCEPTANCE_DIR}/"
    changed = z_paths(run_git(repo, "diff", "--name-only", "-z", "--no-renames", base, "HEAD", "--", pathspec).stdout)
    at_head = {}
    for line in z_paths(run_git(repo, "ls-tree", "-r", "-z", "HEAD", "--", pathspec).stdout):
        meta, path = line.split("\t", 1)
        at_head[path] = meta.split()[2]
    found += [violation("unsealed", path=path) for path in changed if path not in sealed]
    found += [violation("changed-after-seal", path=path) for path, blob in sorted(sealed.items())
              if at_head.get(path, "deleted") != blob]
    return found


def uncommitted_violations(repo: Path) -> list[dict]:
    status = run_git(repo, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all",
                     "--", f":(top){ACCEPTANCE_DIR}/").stdout
    return [violation("uncommitted", path=entry[3:]) for entry in z_paths(status)]


def check(repo: Path, uncommitted: bool) -> dict:
    base = merge_base(repo)
    violations = commit_violations(repo, base) if base else []
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

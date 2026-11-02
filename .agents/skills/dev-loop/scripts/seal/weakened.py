"""`weakened` of `devloop.py gate`: test files in the branch diff with an added skip marker or fewer assertion lines."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from seal import merge_base, run_git

TEST_FILES = ("test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*")
SKIP_MARKERS = re.compile("|".join((
    r"\bpytest\.mark\.(?:skip|skipif|xfail)\b", r"\bpytest\.(?:skip|xfail)\(", r"\bunittest\.skip(?:If|Unless)?\b",
    r"\.(?:skip|only|todo|Skip)\(", r"(?<![\w.$])(?:xit|xdescribe)\(")))
ASSERTIONS = ("assert", "expect(", "t.Error", "t.Fatal", "require.")


def skip_marker(line: str) -> bool:
    return SKIP_MARKERS.search(line) is not None


def assertion_lines(text: str) -> int:
    return sum(any(marker in line for marker in ASSERTIONS) for line in text.splitlines())


def weakened(repo: Path) -> list[dict]:
    base = merge_base(repo)
    if not base:
        return []
    found = []
    for path in run_git(repo, "diff", "--name-only", "-z", "--no-renames", base).stdout.split("\0"):
        if not path or not any(fnmatchcase(PurePosixPath(path).name, glob) for glob in TEST_FILES):
            continue
        diff = run_git(repo, "diff", "-U0", "--no-renames", "--no-color", base, "--", f":(top,literal){path}").stdout
        hunks = diff[diff.find("\n@@"):] if "\n@@" in diff else ""  # after the `+++ b/<path>` header
        added = [line[1:] for line in hunks.splitlines() if line.startswith("+")]
        if any(skip_marker(line) for line in added):
            found.append({"path": path, "reason": "skip-added"})
        file = repo / path
        now = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""
        if assertion_lines(now) < assertion_lines(run_git(repo, "show", f"{base}:{path}").stdout):
            found.append({"path": path, "reason": "assertions-removed"})
    return found

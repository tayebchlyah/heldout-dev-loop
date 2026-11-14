"""The file-size ratchet: `devloop.py size-check`, and the size rule of `gate --summary` and `seal`."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import stat
import subprocess
from pathlib import Path

import devloop
from ci_skip import MERGED
from devloop import emit
from seal import ACCEPTANCE_DIR, merge_base, run_git

LIMIT = 40000
EXEMPT_ERROR = "file_size_exempt in dev-loop.json must be a list of non-empty strings"
STARTS = {
    ".py": ("class ", "def ", "async def "),
    ".md": ("## ",),
    **dict.fromkeys((".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"),
                    ("export ", "function ", "async function ", "class ", "describe(")),
}


def exempt_ok(value: object) -> bool:
    """A list of strings, each non-empty after strip and not ending in `/` (the `merge_human_paths` rule)."""
    return isinstance(value, list) and all(isinstance(p, str) and p.strip() and not p.endswith("/") for p in value)


def seam(data: bytes, path: str) -> tuple[dict | None, list[dict]]:
    """The unit start after line 1 nearest the byte middle, and the 3 largest units, by the file extension."""
    prefixes = tuple(p.encode() for p in STARTS.get(os.path.splitext(path)[1], ()))
    starts, offset = [], 0
    for number, line in enumerate(data.splitlines(keepends=True), 1):
        if prefixes and line.startswith(prefixes):
            starts.append((number, offset, line.decode(errors="replace").strip()[:80]))
        offset += len(line)
    lines = len(data.splitlines())
    units = [(nxt[1] if nxt else len(data)) - start[1] for start, nxt in zip(starts, [*starts[1:], None])]
    largest = sorted(range(len(starts)), key=lambda i: -units[i])[:3]
    found = [{"name": starts[i][2], "start": starts[i][0],
              "end": starts[i + 1][0] - 1 if i + 1 < len(starts) else lines} for i in largest]
    after = [s for s in starts if s[0] > 1]
    best = min(after, key=lambda s: abs(s[1] - len(data) / 2), default=None)
    return (None if best is None else {"line": best[0], "name": best[2]}), found


def violation(path: str, data: bytes, base_bytes: int | None, reason: str, quote: bool = True) -> dict:
    found, units = seam(data, path) if quote else (None, [])
    hint = (f"split {path} at line {found['line']} ({found['name']}), or by responsibility" if found
            else f"split {path} by responsibility")
    return {"path": path, "reason": reason, "bytes": len(data), "base_bytes": base_bytes, "seam": found,
            "units": units, "hint": hint}


def reason(size: int, base_bytes: int | None, mode: str) -> str | None:
    if size <= LIMIT:
        return None
    if mode == "all":
        return "over"
    if base_bytes is None:
        return "new"
    return "grown" if size > base_bytes else None


class GitError(Exception):
    pass


def git(top: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(top), *args], capture_output=True)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {os.fsdecode(result.stderr).strip()}")
    return result.stdout


def tree(top: Path, rev: str) -> dict[str, tuple[str, str, int]]:
    out = os.fsdecode(git(top, "ls-tree", "-r", "-z", "-l", rev))
    entries = {}
    for record in filter(None, out.split("\0")):
        meta, path = record.split("\t", 1)
        mode, _, obj, size = meta.split()
        if size != "-" and not size.isdigit():
            raise GitError(f"git ls-tree -r -z -l {rev} failed: unreadable object {obj} for {path}")
        entries[path] = (mode, obj, int(size) if size != "-" else -1)
    return entries


def names(top: Path, *args: str) -> list[str]:
    return [p for p in os.fsdecode(git(top, *args, "-z")).split("\0") if p]


def cat(top: Path, obj: str) -> bytes:
    return git(top, "cat-file", "blob", obj)


def binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def skipped_paths(top: Path, before: dict[str, tuple[str, str, int]] | None) -> tuple[set[str], list[str] | None]:
    try:
        if before is None:
            raw = (top / ".dev-loop" / "MANIFEST.json").read_bytes()
        else:
            raw = cat(top, before[".dev-loop/MANIFEST.json"][1]) if ".dev-loop/MANIFEST.json" in before else b"{}"
        files = json.loads(raw).get("files", {})
        kit = set(files) if isinstance(files, dict) else set()
    except (OSError, ValueError, AttributeError):
        kit = set()
    try:
        config = json.loads((top / "dev-loop.json").read_text())
    except (OSError, ValueError):
        config = {}
    patterns = config.get("file_size_exempt", []) if isinstance(config, dict) else []
    return kit, patterns if exempt_ok(patterns) else None


def check(top: Path, mode: str) -> dict:
    """`size-check` in mode `committed`, `uncommitted` or `all`."""
    try:
        return run_check(top, mode)
    except GitError as error:
        return {"ok": False, "mode": mode, "limit": LIMIT, "violations": [], "error": str(error)}


def run_check(top: Path, mode: str) -> dict:
    base = None if mode == "all" else merge_base(top)
    out: dict = {"ok": True, "mode": mode, "base": base, "limit": LIMIT, "violations": []}
    _, patterns = skipped_paths(top, None)
    if patterns is None:
        return {**out, "ok": False, "error": EXEMPT_ERROR}
    if base is None and mode != "all":
        return {**out, "skipped": "no-base"}
    before = tree(top, base) if base else {}
    kit, _ = skipped_paths(top, before if base else None)
    head = tree(top, "HEAD") if mode == "committed" else {}
    if base is None:
        paths = names(top, "ls-files")
    else:
        paths = names(top, "diff", "--name-only", "--no-renames", base, *(["HEAD"] if mode == "committed" else []))
    if mode != "committed":
        paths += names(top, "ls-files", "--others", "--exclude-standard")
    for path in sorted(set(paths)):
        backup = any(path.startswith(f"{m}.bak-") and len(path) > len(m) + 5 for m in MERGED)
        if path in kit or path in MERGED or backup or any(fnmatch.fnmatchcase(path, p) for p in patterns):
            continue
        if mode == "committed":
            if path not in head or head[path][0] in ("120000", "160000"):
                continue
            size = head[path][2]
        else:
            try:
                info = os.lstat(top / path)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            size = info.st_size
        base_bytes = before[path][2] if path in before else None
        why = reason(size, base_bytes, mode)
        if why is None:
            continue
        data = (cat(top, head[path][1]) if mode == "committed"
                else (top / path).read_bytes())
        if not binary(data):
            out["violations"].append(violation(path, data, None if mode == "all" else base_bytes, why,
                                                    not path.startswith(ACCEPTANCE_DIR + "/")))
    out["ok"] = not out["violations"]
    return out


def staged(repo: Path, entries: list[tuple[str, str]]) -> list[dict]:
    """The `seal` rule: each staged `(path, blob)` against its blob at the base; no MANIFEST or exempt skip."""
    repo = Path(os.fsdecode(git(repo, "rev-parse", "--show-toplevel")).strip())
    base = merge_base(repo)
    before = tree(repo, base) if base else {}
    found = []
    for path, obj in entries:
        if obj == "deleted":
            continue
        mode = os.fsdecode(git(repo, "--literal-pathspecs", "ls-files", "-s", "-z", "--", path)).split(" ", 1)[0]
        if mode in ("120000", "160000"):
            continue
        size = int(git(repo, "cat-file", "-s", obj))
        base_bytes = before[path][2] if path in before else None
        why = reason(size, base_bytes, "committed")
        if why is not None and not binary(data := cat(repo, obj)):
            found.append(violation(path, data, base_bytes, why))
    return found


def cmd_size_check(args: argparse.Namespace) -> int:
    top = devloop.git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top is None:
        return emit({"error": "not a git repository"}, 2)
    out = check(Path(top), "all" if args.all else "uncommitted" if args.uncommitted else "committed")
    return emit(out, 0 if out["ok"] else 1)

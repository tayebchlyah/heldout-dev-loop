"""The file-size ratchet: `devloop.py size-check`, and the size rule of `gate --summary` and `seal`."""

from __future__ import annotations

import fnmatch
import json
import os
import stat
import subprocess
from pathlib import Path

from seal import merge_base, run_git

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


def violation(path: str, data: bytes, base_bytes: int | None, reason: str) -> dict:
    found, units = seam(data, path)
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


def tree(top: Path, rev: str) -> dict[str, tuple[str, str, int]]:
    out = run_git(top, "ls-tree", "-r", "-z", "-l", rev).stdout
    entries = {}
    for record in filter(None, out.split("\0")):
        meta, path = record.split("\t", 1)
        mode, _, obj, size = meta.split()
        entries[path] = (mode, obj, int(size) if size != "-" else -1)
    return entries


def names(top: Path, *args: str) -> list[str]:
    return [p for p in run_git(top, *args, "-z").stdout.split("\0") if p]


def cat(top: Path, obj: str) -> bytes:
    return subprocess.run(["git", "-C", str(top), "cat-file", "blob", obj], capture_output=True, check=True).stdout


def binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def skipped_paths(top: Path) -> tuple[set[str], list[str] | None]:
    try:
        files = json.loads((top / ".dev-loop" / "MANIFEST.json").read_text()).get("files", {})
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
    base = None if mode == "all" else merge_base(top)
    out: dict = {"ok": True, "mode": mode, "base": base, "limit": LIMIT, "violations": []}
    if base is None and mode != "all":
        return {**out, "skipped": "no-base"}
    kit, patterns = skipped_paths(top)
    if patterns is None:
        return {**out, "ok": False, "error": EXEMPT_ERROR}
    before = tree(top, base) if base else {}
    head = tree(top, "HEAD") if mode == "committed" else {}
    if base is None:
        paths = names(top, "ls-files")
    else:
        paths = names(top, "diff", "--name-only", "--no-renames", base, *(["HEAD"] if mode == "committed" else []))
    if mode != "committed":
        paths += names(top, "ls-files", "--others", "--exclude-standard")
    for path in sorted(set(paths)):
        if path in kit or any(fnmatch.fnmatchcase(path, p) for p in patterns):
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
            out["violations"].append(violation(path, data, None if mode == "all" else base_bytes, why))
    out["ok"] = not out["violations"]
    return out

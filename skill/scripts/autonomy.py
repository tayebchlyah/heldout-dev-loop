"""Session B autonomy: the decision log table, the pause note, the session id and the feature worktree test.

Shared by `devloop.py` (`decide`, `pause`, `status`) and the board writer.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

LOG_HEADING = "## Decision log"
LOG_COLUMNS = ("time", "stage", "session", "kind", "risk", "decision", "why", "evidence", "result")
LOG_HEADER = "| " + " | ".join(c.capitalize() for c in LOG_COLUMNS) + " |"
LOG_RULE = "| " + " | ".join("---" for _ in LOG_COLUMNS) + " |"
NOTE = "pause.json"
UNREADABLE_NOTE = {"error": "unreadable pause note"}


def session_id(env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """(id, harness) of the running agent session: Claude Code first, then Codex. An empty value counts as unset."""
    for name, harness in (("CLAUDE_CODE_SESSION_ID", "claude"), ("CODEX_THREAD_ID", "codex")):
        if env.get(name):
            return env[name], harness
    return None, None


def cell(text: str) -> str:
    return re.sub(r"\r\n|\r|\n", " ", text.strip()).replace("|", "\\|")


def split_row(line: str) -> list[str]:
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def section_lines(lines: list[str]) -> tuple[int, int] | None:
    """[start, end) of the decision log section (heading line included), or None."""
    start = next((i for i, line in enumerate(lines) if line.strip() == LOG_HEADING), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"#{1,2} ", lines[i])), len(lines))
    return start, end


def log_rows(text: str) -> list[dict]:
    """The rows of the decision log table, cells unescaped, plus `row`, the raw line."""
    lines = text.splitlines()
    span = section_lines(lines)
    if span is None:
        return []
    rows = []
    for line in lines[span[0] + 1:span[1]]:
        if not line.startswith("|"):
            continue
        values = split_row(line)
        if values and (values[0].lower() == "time" or all(re.fullmatch(r":?-+:?", v) for v in values)):
            continue  # the header and the rule, whatever a Markdown formatter made of them
        if len(values) == len(LOG_COLUMNS):
            rows.append({**dict(zip(LOG_COLUMNS, values)), "row": line})
    return rows


def append_row(path: Path, row: str, lock: Path) -> None:
    """Append one row to the decision log table of `path`, adding the section and the header when absent."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        text = path.read_text() if path.exists() else ""
        lines = text.splitlines()
        span = section_lines(lines)
        if span is None:
            lead = "" if not text else "\n" if text.endswith("\n") else "\n\n"
            text = f"{text}{lead}{LOG_HEADING}\n\n{LOG_HEADER}\n{LOG_RULE}\n{row}\n"
        else:
            table = [i for i in range(span[0] + 1, span[1]) if lines[i].startswith("|")]
            if table:
                lines.insert(table[-1] + 1, row)
            else:
                lines[span[0] + 1:span[0] + 1] = ["", LOG_HEADER, LOG_RULE, row]
            text = "\n".join(lines) + "\n"
        write_atomic(path, text)


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` (a symlink: its target) in one rename, keeping its mode (new file: 0666 minus the umask)."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = path.stat().st_mode & 0o7777
    else:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    os.chmod(tmp, mode)
    with os.fdopen(fd, "w") as out:
        out.write(text)
    os.replace(tmp, path)


def git(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def is_feature_worktree(cwd: Path) -> bool:
    """A linked worktree on a branch with a `/`, whose top level holds `dev-loop.json`."""
    git_dir = git(cwd, "rev-parse", "--absolute-git-dir")
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    branch = git(cwd, "symbolic-ref", "--short", "-q", "HEAD")
    top = git(cwd, "rev-parse", "--show-toplevel")
    return (git_dir is not None and common is not None and Path(git_dir) != Path(common)
            and bool(branch) and "/" in branch and top is not None and (Path(top) / "dev-loop.json").is_file())


def note_path(git_dir: Path) -> Path:
    return git_dir / "dev-loop" / NOTE


def read_note(git_dir: Path) -> dict | None:
    path = note_path(git_dir)
    if not path.is_file():
        return None
    try:
        note = json.loads(path.read_text())
    except (OSError, ValueError):
        return dict(UNREADABLE_NOTE)
    return note if isinstance(note, dict) else dict(UNREADABLE_NOTE)


def find_transcript(session: str, harness: str, env: Mapping[str, str], home: Path) -> str | None:
    if harness == "claude":
        root = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude") / "projects"
        found = sorted(root.glob(f"*/{session}.jsonl"))
    else:
        root = Path(env.get("CODEX_HOME") or home / ".codex") / "sessions"
        found = sorted(root.glob(f"**/rollout-*-{session}.jsonl"))
    return str(found[0]) if found else None

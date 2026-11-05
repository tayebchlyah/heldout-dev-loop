"""Delivery autonomy: the decision log table, the pause note, the session id and the feature worktree test.

The `decide` and `pause` commands. Shared by `status` and the board writer.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import devloop
from devloop import emit, find_plan, specs
from notes_path import NotesPathError, resolve

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


def checked_out_branch(cwd: Path) -> str | None:
    from status import rebasing
    return git(cwd, "symbolic-ref", "--short", "-q", "HEAD") or rebasing(cwd)


def is_feature_worktree(cwd: Path) -> bool:
    """A linked worktree on a branch with a `/`, whose top level holds `dev-loop.json`."""
    git_dir = git(cwd, "rev-parse", "--absolute-git-dir")
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    branch = checked_out_branch(cwd)
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
        root = devloop.claude_home(env, home) / "projects"
        found = sorted(root.glob(f"*/{session}.jsonl"))
    else:
        root = devloop.codex_home(env, home) / "sessions"
        found = sorted(root.glob(f"**/rollout-*-{session}.jsonl"))
    return str(found[0]) if found else None


def branch_slug(repo: Path) -> str | None:
    """The slug of the checked-out branch `<prefix>/<slug>` (a rebase in progress: its branch), or None."""
    branch = checked_out_branch(repo)
    return branch.rsplit("/", 1)[1].lower() if branch and "/" in branch else None


def log_target(repo: Path, slug: str, notes: Path) -> Path | None:
    """The decision log of a feature: its spec, else (Track S) `decisions.md` in its worktree git dir."""
    from status import feature_checkout
    matches = [path for name, path in specs(notes) if name == slug]
    if matches:
        return matches[-1]
    worktree, _ = feature_checkout(repo, slug)
    git_dir = devloop.git(worktree, "rev-parse", "--absolute-git-dir") if worktree else None
    return Path(git_dir) / "dev-loop" / "decisions.md" if git_dir else None


def cmd_decide(args: argparse.Namespace) -> int:
    from status import detect_feature
    repo = Path(args.repo).resolve()
    for name in ("what", "why", "evidence"):
        if not getattr(args, name).strip():
            return emit({"error": f"--{name} is empty"}, 1)
    slug = (args.feature or "").lower() or branch_slug(repo)
    if slug is None:
        return emit({"error": "no feature: pass --feature or run on a branch <prefix>/<slug>"}, 1)
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    try:  # A spec that cannot be read (an iCloud placeholder, a mode change) fails before any write.
        target = log_target(repo, slug, notes)
        common = devloop.git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if target is None or common is None:
            return emit({"error": f"no spec and no branch worktree for {slug}"}, 1)
        session, _ = session_id(os.environ)
        stage = detect_feature(repo, slug, notes)["stage"]
        values = [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), str(stage), (session or "")[:8], args.kind,
                  "risk" if args.risk else "", args.what, args.why, args.evidence, args.result or ""]
        row = "| " + " | ".join(cell(v) for v in values) + " |"
        append_row(target, row, Path(common) / "dev-loop" / "decide.lock")
    except OSError as exc:
        return emit({"error": f"cannot read or write the decision log: {exc}"}, 1)
    return emit({"file": str(target), "row": row}, 0)


INTENT_FIELDS = ("intent", "verified", "open", "next")


def cmd_pause(args: argparse.Namespace) -> int:
    from status import detect
    given = [name for name in (*INTENT_FIELDS, "agent", "runs") if getattr(args, name)]
    if args.clear and (given or args.event):
        return emit({"error": "--clear takes no other option"}, 2)
    if args.event and given:
        return emit({"error": "--event takes no intent, agent or run option"}, 2)
    agents = []
    for value in args.agent or []:
        agent_id, sep, task = value.partition("=")
        if not sep or not agent_id:
            return emit({"error": f"--agent takes <id>=<task>: {value}"}, 2)
        agents.append({"id": agent_id, "task": task})
    event: dict = {}
    if args.event:
        try:
            event = json.loads(sys.stdin.read())
        except ValueError:
            event = None  # type: ignore[assignment]
        if not isinstance(event, dict):
            return emit({"skipped": "bad-input"}, 0)
        if not isinstance(event.get("cwd"), str) or not is_feature_worktree(Path(event["cwd"])):
            return emit({"skipped": "not-a-feature-worktree"}, 0)
        repo = Path(event["cwd"]).resolve()
    else:
        repo = Path(args.repo).resolve()
    git_dir = devloop.git(repo, "rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return emit({"error": f"not a git repository: {repo}"}, 1)
    path = note_path(Path(git_dir))
    named = (args.feature or "").lower() or None
    if args.clear:
        found = path.is_file() and (named is None or (read_note(Path(git_dir)) or {}).get("feature") == named)
        if found:
            path.unlink()
        return emit({"cleared": found}, 0)

    if args.event:
        session, harness = event.get("session_id"), "claude"
        transcript = event.get("transcript_path")
        reason = event.get("error") or "unknown"
        message = event.get("last_assistant_message") or event.get("error_details")
    else:
        session, harness = session_id(os.environ)
        transcript = find_transcript(session, harness, os.environ, Path.home()) if session and harness else None
        reason, message = "manual", None
    slug = named or branch_slug(repo)
    stage = ledger_last = last_decision = None
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError:
        notes = None
    try:  # An unreadable spec, plan or ledger (an iCloud placeholder) must not cost the note.
        if notes is not None:
            stage = detect(repo, named, notes)["stage"]
            target = log_target(repo, slug, notes) if slug else None
            if target is not None and target.is_file():
                rows = log_rows(target.read_text())
                last_decision = rows[-1]["row"] if rows else None
                plan = find_plan(notes, target) if target.suffix == ".md" and devloop.is_spec(notes, target) else None
                top = devloop.git(repo, "rev-parse", "--show-toplevel")
                ledger = devloop.ledger(Path(top), plan) if plan and top else None
                if ledger is not None and ledger.is_file():
                    ledger_last = next((line for line in reversed(ledger.read_text().splitlines()) if line.strip()),
                                       None)
    except OSError:
        stage = ledger_last = last_decision = None
    # Not git(): its strip() eats the leading space of a first ` M <path>` line.
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z"], capture_output=True, text=True)
    entries = iter(status.stdout.split("\0") if status.returncode == 0 else [])
    dirty = []
    for entry in entries:
        if len(entry) > 3:
            dirty.append(entry[3:])
            if entry[0] in "RC":
                next(entries, None)  # a rename or copy: the source path follows, keep the destination
    note = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
        "message": message,
        "harness": harness,
        "session": session,
        "transcript": transcript,
        "feature": slug,
        "stage": stage,
        "ledger_last": ledger_last,
        "dirty": dirty,
        "last_decision": last_decision,
        "agents": agents,
        "runs": list(args.runs or []),
        **{name: getattr(args, name) for name in INTENT_FIELDS},
    }
    write_atomic(path, json.dumps(note, indent=2) + "\n")
    return emit({"file": str(path), "note": note}, 0)

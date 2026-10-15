"""Status note files: machine identity, project name, note names, safe reads and writes (spec D3 to D7)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
from pathlib import Path

BOARD_NOTE = "Dev Loop Board.md"
TEMP_MAX_AGE = 3600


class LinkError(Exception):
    pass


def run(*cmd: str) -> str | None:
    try:
        result = subprocess.run(list(cmd), capture_output=True, text=True)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def load_machine(home: Path) -> dict:
    """D3: read machine.json, or write it once. Never rewrite it."""
    path = home / ".config" / "dev-loop" / "machine.json"
    if path.is_file():
        return json.loads(path.read_text())
    name = run("scutil", "--get", "ComputerName") if sys.platform == "darwin" else None
    user = run("git", "config", "--global", "user.name")
    machine = {
        "id": secrets.token_hex(8),
        "machine": name or socket.gethostname().split(".")[0],
        "owner": user.split()[0] if user else "unknown",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(machine) + "\n")
    return machine


def project_name(repo: Path) -> str:
    """D4: the last part of the origin URL without `.git`, else the main worktree folder name, as a slug."""
    url = run("git", "-C", str(repo), "config", "--get", "remote.origin.url")
    if url:
        name = re.split(r"[/:]", url.rstrip("/"))[-1].removesuffix(".git")
    else:
        common = run("git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir")
        name = Path(common).parent.name if common else repo.name
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def note_name(machine_id: str, project: str, feature: str) -> str:
    """D5."""
    digest = hashlib.sha256("\0".join([machine_id, project, feature]).encode()).hexdigest()[:8]
    return f"{project}-{feature}-{digest}.md"


def root_of(notes: Path) -> Path:
    """D19: the nearest parent of the notes path that holds `.obsidian`, else the notes path."""
    notes = notes.resolve()
    return next((p for p in [notes, *notes.parents] if (p / ".obsidian").is_dir()), notes)


def version_key(text: object) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return ()


def parse(data: bytes) -> dict | None:
    """Schema 1 frontmatter, each value JSON. None when any part does not parse or the frontmatter is cut."""
    try:
        lines = data.decode().splitlines()
        if not lines or lines[0] != "---":
            return None
        fields = {}
        for line in lines[1:lines.index("---", 1)]:
            key, sep, value = line.partition(":")
            if not sep:
                return None
            fields[key.strip()] = json.loads(value.strip())
        return fields
    except (UnicodeDecodeError, ValueError):
        return None


def read_note(path: Path) -> dict | None:
    """The frontmatter of a status note, each value JSON. None when the file cannot be read or does not parse."""
    try:
        return parse(path.read_bytes())
    except OSError:
        return None


def note_text(fields: dict) -> str:
    return "---\n" + "".join(f"{key}: {json.dumps(value)}\n" for key, value in fields.items()) + "---\n"


def write_file(folder: Path, name: str, text: str) -> None:
    """D7: exclusive temp file with no link follow, then rename. Refuses a link folder or a link note."""
    target = folder / name
    if folder.is_symlink() or target.is_symlink():
        raise LinkError(str(target))
    temp = folder / f".{name}.{os.getpid()}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def clean_temps(folder: Path, own_names: set[str], now: float) -> None:
    """Delete `.<name>.<pid>.tmp` files older than 1 hour, for own note names and the board note only."""
    for path in folder.glob(".*.tmp"):
        name, _, pid = path.name[1:-len(".tmp")].rpartition(".")
        if pid.isdigit() and (name in own_names or name == BOARD_NOTE) and not path.is_symlink():
            try:
                if now - path.stat().st_mtime > TEMP_MAX_AGE:
                    path.unlink()
            except FileNotFoundError:
                pass

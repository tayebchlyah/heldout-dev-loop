"""`devloop.py skill-wall`: may `implementer` load this project skill (#274)."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from devloop import emit
from notes_path import main_ref, worktrees

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
NAMES_ERROR = "walled_skills in dev-loop.json must be a list of names, each ^[a-z0-9][a-z0-9-]{0,63}$"
FRONT_KEY = re.compile(r"""(?:^|[{,])\s*["']?(context|hooks)["']?\s*:""", re.MULTILINE)
FENCE = re.compile(r"---[\s\ufeff]*\n([\s\S]*?)---")
PLAIN_KEY = re.compile(r"[A-Za-z0-9_-]+[ \t]*:(?:\s|$)")
LINK_MODE = "120000"
TREE_MODE = "040000"
FILE_MODES = ("100644", "100755")
NOT_LISTED = "not in walled_skills"
NO_COPY = "no copy at the main ref"
DIFFERS = "differs from the main ref"


@dataclass(frozen=True)
class Source:
    link: str | None
    text: bytes


def names_ok(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and NAME.fullmatch(item) for item in value)


def listed_names(config: object) -> list[str]:
    value = config.get("walled_skills") if isinstance(config, dict) else None
    return [item for item in value if isinstance(item, str) and NAME.fullmatch(item)] if isinstance(value, list) else []


def blob(repo: Path, spec: str) -> bytes | None:
    shown = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", spec], capture_output=True)
    return shown.stdout if shown.returncode == 0 else None


def tree_entry(repo: Path, ref: str, path: str) -> tuple[str, str] | None:
    listed = subprocess.run(["git", "-C", str(repo), "ls-tree", "-z", "--full-tree", ref, "--", path], capture_output=True)
    if listed.returncode != 0 or not listed.stdout:
        return None
    meta, _, found = listed.stdout.split(b"\0")[0].partition(b"\t")
    mode, _, sha = meta.decode().split(" ")
    return (mode, sha) if found == os.fsencode(path) else None


def link_folder(link: str) -> str | None:
    folder = posixpath.normpath(f".claude/skills/{link}")
    outside = folder in (".", "..") or folder.startswith("../")
    return None if link.startswith("/") or "\0" in link or outside else folder


def link_text(repo: Path, ref: str, name: str) -> str | None:
    entry = tree_entry(repo, ref, f".claude/skills/{name}")
    data = blob(repo, entry[1]) if entry and entry[0] == LINK_MODE else None
    return None if data is None else os.fsdecode(data)


def skill_roots(repo: Path, ref: str, name: str) -> list[str]:
    root = f".claude/skills/{name}"
    link = link_text(repo, ref, name)
    folder = None if link is None else link_folder(link)
    return [root, folder] if folder else [root]


def read_source(repo: Path, ref: str, name: str) -> Source | None:
    root = f".claude/skills/{name}"
    entry = tree_entry(repo, ref, root)
    if entry is None or entry[0] not in (LINK_MODE, TREE_MODE):
        return None
    data = blob(repo, entry[1]) if entry[0] == LINK_MODE else None
    link = None if data is None else os.fsdecode(data)
    folder = root if link is None else link_folder(link)
    found = tree_entry(repo, ref, f"{folder}/SKILL.md") if folder else None
    text = blob(repo, found[1]) if found and found[0] in FILE_MODES else None
    return None if text is None else Source(link, text)


def same_copy(checkout: Path, name: str, source: Source) -> bool:
    folder = checkout / ".claude" / "skills" / name
    file = folder / "SKILL.md"
    try:
        link = os.readlink(folder) if folder.is_symlink() else None
        return link == source.link and file.is_file() and file.read_bytes() == source.text
    except OSError:
        return False


def front_block(text: bytes) -> str:
    found = FENCE.match(text.decode("utf-8-sig", errors="replace"))
    return found.group(1) if found else ""


def odd_shape(front: str) -> bool:
    lines = [line for line in front.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return bool(lines) and (lines[0][0] in " \t" or any(line[0] not in " \t" and not PLAIN_KEY.match(line) for line in lines))


def scan(text: bytes) -> str | None:
    front = front_block(text)
    keys = {found.group(1) for found in FRONT_KEY.finditer(front)}
    if "context" in keys:
        return "context: fork"
    if b"!`" in text or b"```!" in text or b"!$" in text:
        return "shell line"
    if "hooks" in keys:
        return "hooks key"
    if b"@" in text:
        return "at mention"
    return "frontmatter shape" if odd_shape(front) else None


def main_config(repo: Path, ref: str) -> object:
    data = blob(repo, f"{ref}:dev-loop.json")
    try:
        return None if data is None else json.loads(data)
    except ValueError:
        return None


def setup_lines(repo: Path) -> list[str]:
    ref = main_ref(repo)
    if not ref:
        return []
    lines = []
    for name in listed_names(main_config(repo, ref)):
        source = read_source(repo, ref, name)
        reason = scan(source.text) if source else NO_COPY
        if reason:
            lines.append(f"walled_skills: {name} stays closed to implementer: {reason}")
    return lines


def decide(hook: object, env: Mapping[str, str]) -> dict:
    tool_input = hook.get("tool_input") if isinstance(hook, dict) else None
    name = tool_input.get("skill") if isinstance(tool_input, dict) else None
    places = (hook.get("cwd"), env.get("CLAUDE_PROJECT_DIR")) if isinstance(hook, dict) else ()
    folder = next((place for place in places if isinstance(place, str) and place), None)
    if not (isinstance(name, str) and NAME.fullmatch(name) and folder):
        return {"deny": True, "reason": NOT_LISTED}
    worktree, main = worktrees(Path(folder))
    project = env.get("CLAUDE_PROJECT_DIR")
    checkouts = [worktree, main, *([worktrees(Path(project))[0]] if project else [])]
    ref = main_ref(main)
    if not ref or name not in listed_names(main_config(main, ref)):
        return {"deny": True, "reason": NOT_LISTED}
    source = read_source(main, ref, name)
    if source is None:
        return {"deny": True, "reason": NO_COPY}
    if not all(same_copy(checkout, name, source) for checkout in dict.fromkeys(checkouts)):
        return {"deny": True, "reason": DIFFERS}
    reason = scan(source.text)
    return {"deny": True, "reason": reason} if reason else {"deny": False, "reason": None}


def cmd_skill_wall(args: argparse.Namespace) -> int:
    try:
        hook = json.loads(sys.stdin.read())
    except ValueError:
        hook = None
    return emit(decide(hook, os.environ), 0)

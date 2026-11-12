"""`devloop.py source-wall`: decide a spec-tester tool call against `source_dirs` at the main ref (#302)."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from devloop import emit
from seal.search import HEREDOC
from source_dirs import fold, matches, read_main

NOT_OBJECT = "source-wall: hook input is not a JSON object"
REASON = "Blocked: {} is outside this agent's territory (source_dirs)."
SUITE = "tests/" + "acceptance"
CLI = "/.agents/skills/dev-loop/scripts/devloop.py"
FIELDS = ("file_path", "path", "notebook_path", "glob", "paths", "files", "url", "source", "destination", "directory",
          "filePath", "uri", "args", "pattern")
PATCH_LINE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.*)$", re.MULTILINE)
UNRESOLVED = re.compile(r"[$`*?\[{]")
HOME = re.compile(r"\$HOME(?![A-Za-z0-9_])|\$\{HOME\}")
PWD = re.compile(r"\$PWD(?![A-Za-z0-9_])|\$\{PWD\}")
SHELL_OPS = set("\n;&|<>()$`")
CD_OPS = re.compile(r"[;&|<>()]")
Folders = tuple[str | None, ...]


def _tops(repo: Path) -> tuple[list[str], list[str]]:
    listed = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True, text=True)
    variants: list[str] = []
    real: list[str] = []
    for block in listed.stdout.split("\n\n"):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree ") or "bare" in lines:
            continue
        top = lines[0].removeprefix("worktree ")
        real.append(os.path.realpath(top))
        for form in (top, real[-1]):
            twin = form.removeprefix("/private") if form.startswith("/private/") else "/private" + form
            variants += [fold(form), fold(twin)]
    return variants, real


def _rel(form: str, variants: Sequence[str]) -> str | None:
    inside = [v for v in variants if form == v or form.startswith(v + "/")]
    if not inside:
        return None
    return form[len(max(inside, key=len)) + 1:]


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _expand(text: str, folder: str | None, env: Mapping[str, str]) -> str:
    home = env.get("HOME")
    if home:
        if text == "~" or text.startswith("~/"):
            text = home + text[1:]
        text = HOME.sub(lambda _: home, text)
    if folder is not None:
        text = PWD.sub(lambda _: folder, text)
    return text


def _is_seal(command: str, start: str, real: Sequence[str]) -> bool:
    if any(ch in SHELL_OPS for ch in command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if len(words) < 3 or words[0] != "python3" or words[2] != "seal":
        return False
    return fold(os.path.realpath(os.path.join(start, words[1]))) in {fold(top + CLI) for top in real}


def _field_texts(tool: str, tool_input: dict) -> Iterator[str]:
    for name in FIELDS:
        if name == "pattern" and tool == "Grep":
            continue
        for text in _strings(tool_input.get(name)):
            if name == "glob" and text.startswith("!"):
                continue
            yield text
            if text.startswith("file://"):
                yield text.removeprefix("file://").removeprefix("localhost")
    base = tool_input.get("path")
    if isinstance(base, str):
        for name in ("glob", "pattern"):
            tail = tool_input.get(name)
            if isinstance(tail, str) and not (name == "pattern" and tool == "Grep"):
                yield base + "/" + tail


def _word_parts(word: str) -> Iterator[str]:
    yield word
    for part in re.split(r"[:=<>|;&()]", word):
        yield part
        yield from re.split(r"[\s'\"`]", part)


def _command_candidates(command: str, start: str, env: Mapping[str, str]) -> Iterator[tuple[str, Folders]]:
    text = HEREDOC.sub(lambda m: f"<<{m.group(2)}{m.group(3)}", command.replace("\\\n", ""))
    try:
        words = [(word, word) for word in shlex.split(text)]
    except ValueError:
        words = [(word, re.sub(r"['\"\\]", "", word)) for word in text.split()]
    folders: Folders = (start,)
    current: str | None = start
    moving = False
    for word, bare in words:
        parts = dict.fromkeys([*_word_parts(word), *_word_parts(bare), *_word_parts(re.sub(r"['\"\\]", "", word))])
        for part in parts:
            if part:
                yield part, folders
        if moving and (word == "-" or not word.startswith("-")):
            moving = False
            target = _expand(bare, current, env)
            if (not target or target == "-" or UNRESOLVED.search(target) or CD_OPS.search(target)
                    or (current is None and not target.startswith("/"))):
                current = None
            else:
                current = posixpath.normpath(posixpath.join(current or "", target))
            folders = (*folders, current, None)
        elif word in ("cd", "pushd"):
            moving = True
        elif any(part in ("cd", "pushd") for part in parts):
            folders = (*folders, None)


def _candidates(hook: dict, start: str, env: Mapping[str, str]) -> Iterator[tuple[str, Folders]]:
    tool = hook.get("tool_name")
    tool_input = hook.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    patch = [tool_input.get(name) for name in ("command", "patch")]
    if tool == "apply_patch" or (tool in ("Edit", "Write") and "file_path" not in tool_input
                                 and any(isinstance(p, str) for p in patch)):
        for body in patch:
            if isinstance(body, str):
                yield from ((m.group(1).rstrip(), (start,)) for m in PATCH_LINE.finditer(body))
        return
    if tool == "skillsread":
        yield from ((text, (start,)) for text in _strings(tool_input))
        return
    yield from ((text, (start,)) for text in _field_texts(str(tool), tool_input))
    command = tool_input.get("command")
    if tool in ("Bash", "Monitor") and isinstance(command, str):
        yield from _command_candidates(command, start, env)


def _text_hit(entries: Sequence[str], text: str) -> str | None:
    folded = fold(text)
    for entry in entries:
        name = re.escape(fold(entry)[:-1])
        if re.search(rf"(^|[^a-z0-9_.-]){name}(/|[\"'`;|&)<>*\s]|$)", folded):
            return entry
    return None


def _path_hit(entries: Sequence[str], text: str, folder: str, variants: Sequence[str]) -> str | None:
    whole = text if text.startswith("/") else folder + "/" + text
    normal = "/" + posixpath.normpath(whole).lstrip("/")
    resolved = _rel(fold(os.path.realpath(whole)), variants)
    if resolved is not None and (resolved == SUITE or resolved.startswith(SUITE + "/")):
        return None
    for rel in (_rel(fold(normal), variants), resolved):
        if rel is not None and (hit := matches(entries, rel)):
            return hit
    return None


def _denied(hook: dict, repo: Path, env: Mapping[str, str], entries: Sequence[str]) -> str | None:
    variants, real = _tops(repo)
    cwd = hook.get("cwd")
    start = cwd if isinstance(cwd, str) and cwd else os.path.abspath(repo)
    tool_input = hook.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if hook.get("tool_name") in ("Bash", "Monitor") and isinstance(command, str) and _is_seal(command, start, real):
        return None
    for raw, folders in _candidates(hook, start, env):
        for folder in dict.fromkeys(folders):
            text = _expand(raw, folder, env)
            if UNRESOLVED.search(text) or (folder is None and not text.startswith("/")):
                hit = _text_hit(entries, text)
            else:
                hit = _path_hit(entries, text, folder or "", variants)
                if not hit and ".." in text.split("/"):
                    hit = _text_hit(entries, text)
            if hit:
                return hit
    return None


def decide(hook: object, repo: Path, env: Mapping[str, str]) -> tuple[dict, int]:
    if not isinstance(hook, dict):
        return {"error": NOT_OBJECT}, 1
    main = read_main(repo)
    if error := main.error():
        return {"error": error}, 1
    if main.state != "configured":
        return {"mode": "default", "deny": False, "reason": None, "ref": main.ref}, 0
    try:
        entry = _denied(hook, repo, env, main.entries) if main.entries else None
    except ValueError as exc:
        return {"error": f"source-wall: {exc}"}, 1
    return {"mode": "configured", "deny": entry is not None, "reason": REASON.format(entry) if entry else None,
            "ref": main.ref}, 0


def cmd_source_wall(args: argparse.Namespace) -> int:
    try:
        hook = json.loads(sys.stdin.read())
    except ValueError:
        hook = None
    payload, code = decide(hook, Path(args.repo), os.environ)
    return emit(payload, code)

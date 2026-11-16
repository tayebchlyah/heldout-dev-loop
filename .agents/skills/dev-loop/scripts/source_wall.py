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
from seal import ACCEPTANCE_DIR as SUITE
from seal.search import HEREDOC
from source_dirs import fold, matches, read_main

NOT_OBJECT = "source-wall: hook input is not a JSON object"
REASON = "Blocked: {} is outside this agent's territory (source_dirs)."
CLI = "/.agents/skills/dev-loop/scripts/devloop.py"
FIELDS = ("file_path", "path", "notebook_path", "glob", "paths", "files", "url", "source", "destination", "directory",
          "filePath", "uri", "args", "pattern")
PATCH_LINE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.*)$", re.MULTILINE)
UNRESOLVED = re.compile(r"[$`*?\[{]")
HOME = re.compile(r"\$HOME(?![A-Za-z0-9_])|\$\{HOME\}")
PWD = re.compile(r"\$PWD(?![A-Za-z0-9_])|\$\{PWD\}")
PUNCTUATION = "();<>|&\n"
SEPARATORS = ("&&", ";", "\n")
CD_OPS = re.compile(r"[;&|<>()]")
GLOB_RISK = re.compile(r"[$`\[{]|(^|/)\.")
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


def _in_suite(path: str, variants: Sequence[str]) -> bool:
    rel = _rel(fold(os.path.realpath(path)), variants)
    return rel is not None and (rel == SUITE or rel.startswith(SUITE + "/"))


def _remainder(text: str, variants: Sequence[str]) -> str:
    rel = _rel(fold(text), variants) if text.startswith("/") else None
    return text if rel is None else rel


def _tokens(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _is_operator(token: str) -> bool:
    return bool(token) and all(ch in PUNCTUATION for ch in token)


def _expand(text: str, folder: str | None, env: Mapping[str, str]) -> str:
    if folder is not None and (text == "~+" or text.startswith("~+/")):
        text = folder + text[2:]
    home = env.get("HOME")
    if home:
        if text == "~" or text.startswith("~/"):
            text = home + text[1:]
        text = HOME.sub(lambda _: home, text)
    if folder is not None:
        text = PWD.sub(lambda _: folder, text)
    return text


def _is_seal(command: str, start: str, real: Sequence[str]) -> bool:
    if any(ch in "\n$`" for ch in command):
        return False
    try:
        words = _tokens(command)
    except ValueError:
        return False
    if any(_is_operator(word) for word in words):
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


def _leading_moves(words: Sequence[str], start: str, env: Mapping[str, str]) -> dict[int, str]:
    moves: dict[int, str] = {}
    folder, i = start, 0
    while i + 1 < len(words) and words[i] in ("cd", "pushd"):
        target = _expand(words[i + 1], folder, env)
        after = words[i + 2] if i + 2 < len(words) else ";"
        if (after not in SEPARATORS or _is_operator(target) or target.startswith(("-", "~"))
                or UNRESOLVED.search(target) or (env.get("CDPATH") and not target.startswith(("/", "./", "../"))
                                                 and target not in (".", ".."))):
            break
        folder = posixpath.normpath(posixpath.join(folder, target))
        if not os.path.isdir(folder):
            break
        moves[i + 1] = folder
        i += 3
    return moves


def _command_candidates(command: str, start: str, env: Mapping[str, str]) -> Iterator[tuple[str, Folders]]:
    text = HEREDOC.sub(lambda m: f"<<{m.group(2)}{m.group(3)}", command.replace("\\\n", ""))
    try:
        words = [(word, word) for word in _tokens(text)]
        moves = _leading_moves([word for word, _ in words], start, env)
    except ValueError:
        words = [(word, re.sub(r"['\"\\]", "", word)) for word in text.split()]
        moves = {}
    folders: Folders = (start,)
    seen: Folders = (start,)
    current: str | None = start
    moving = False
    for i, (word, bare) in enumerate(words):
        parts = dict.fromkeys([*_word_parts(word), *_word_parts(bare), *_word_parts(re.sub(r"['\"\\]", "", word))])
        for part in parts:
            if part:
                yield part, folders
        if i in moves:
            current = moves[i]
            folders, seen = (current,), (*seen, current)
        elif i + 1 in moves:
            continue
        elif moving and (word == "-" or not word.startswith("-")):
            moving = False
            target = _expand(bare, current, env)
            if (not target or target.startswith(("-", "~")) or UNRESOLVED.search(target) or CD_OPS.search(target)
                    or (current is None and not target.startswith("/"))):
                current = None
            else:
                current = posixpath.normpath(posixpath.join(current or "", target))
            folders = (*seen, *folders, current, None)
        elif word in ("cd", "pushd"):
            moving = True
        elif any(part in ("cd", "pushd") for part in parts):
            folders = (*seen, *folders, None)


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
        if re.search(rf"(^|[^a-z0-9_.-]){name}(/|[\"'`;|&)<>*\s{{}},$]|$)", folded):
            return entry
    return None


def _path_hit(entries: Sequence[str], text: str, folder: str, variants: Sequence[str]) -> str | None:
    whole = text if text.startswith("/") else folder + "/" + text
    normal = "/" + posixpath.normpath(whole).lstrip("/")
    if _in_suite(whole, variants):
        return None
    for rel in (_rel(fold(normal), variants), _rel(fold(os.path.realpath(whole)), variants)):
        if rel is not None and (hit := matches(entries, rel)):
            return hit
    return None


def _suite_glob(text: str, folder: str | None, variants: Sequence[str]) -> bool:
    found = UNRESOLVED.search(text)
    if not found or text.startswith("~") or ".." in text or (folder is None and not text.startswith("/")):
        return False
    head = posixpath.dirname(text[:found.start()])
    real = os.path.realpath(head if head.startswith("/") else posixpath.join(folder or "", head))
    if GLOB_RISK.search(text[len(head):].lstrip("/")) or not _in_suite(real, variants):
        return False
    for root, dirs, files in os.walk(real):
        for name in (*dirs, *files):
            link = os.path.join(root, name)
            if os.path.islink(link) and not _in_suite(link, variants):
                return False
    return True


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
            if UNRESOLVED.search(text) or text.startswith("~") or (folder is None and not text.startswith("/")):
                hit = None if _suite_glob(text, folder, variants) else _text_hit(entries, _remainder(text, variants))
            else:
                hit = _path_hit(entries, text, folder or "", variants)
                if not hit and ".." in text.split("/"):
                    hit = _text_hit(entries, _remainder(text, variants))
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

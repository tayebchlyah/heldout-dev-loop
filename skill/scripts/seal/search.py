"""`devloop.py search-wall`: deny a subagent search whose root reaches the held-out suite without excluding it (#91)."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Mapping
from fnmatch import fnmatchcase
from pathlib import Path

from devloop import emit
from seal import ACCEPTANCE_DIR

D8_REASON = (f"Blocked: this search reaches the held-out suite {ACCEPTANCE_DIR}. Search the source folders by name "
             f"(grep -rn <name> src lib, Grep path src), or exclude the suite (grep --exclude-dir=acceptance, "
             f"rg -g '!{ACCEPTANCE_DIR}/**', git grep <name> -- . ':!{ACCEPTANCE_DIR}', "
             f"find . -path ./{ACCEPTANCE_DIR} -prune -o ..., Grep glob !{ACCEPTANCE_DIR}/**).")
NOT_OBJECT = "Blocked: guard error: hook input is not a JSON object"
TESTERS = ("spec-tester", "spec_tester")
WILD = re.compile(r"[*?\[{]")
SUITE_NAMES = ("acceptance", "tests", ACCEPTANCE_DIR)


def norm(path: str) -> str:
    path = posixpath.normpath(re.sub(r"/+", "/", path.lower()))
    return path[len("/private"):] if path.startswith("/private/") else path


def absolute(text: str, folder: str, home: str) -> str:
    if text == "~" or text.startswith("~/"):
        text = home + text[1:]
    return text if text.startswith("/") else f"{folder}/{text}"


def inside(path: str, suite: str) -> bool:
    return path == suite or path.startswith(suite + "/")


def ancestor(path: str, suite: str) -> bool:
    return path == "/" or suite.startswith(path + "/")


def braces(text: str) -> list[str]:
    found = re.search(r"\{([^{}]*,[^{}]*)\}", text)
    if not found:
        return [text]
    return [alt for part in found.group(1).split(",")
            for alt in braces(text[:found.start()] + part + text[found.end():])]


def seg_match(rest: list[str], segs: list[str], tail: str) -> bool:
    """Can the name patterns `rest` match `segs` and then a tail of `any` (0+), `some` (1+) or `none` names."""
    if not segs:
        return {"any": True, "some": bool(rest), "none": all(part == "**" for part in rest)}[tail]
    if not rest:
        return False
    if rest[0] == "**":
        return seg_match(rest[1:], segs, tail) or seg_match(rest, segs[1:], tail)
    return fnmatchcase(segs[0], rest[0]) and seg_match(rest[1:], segs[1:], tail)


def reach(text: str, folder: str, home: str, suite: str, tail: str = "any") -> str:
    """How a root (a path, or a pattern with wildcards) meets the suite: `inside`, `ancestor` or `none`."""
    parts = absolute(text, folder, home).lower().split("/")
    cut = next((i for i, part in enumerate(parts) if WILD.search(part)), len(parts))
    base, rest = norm("/".join(parts[:cut]) or "/"), [part for part in parts[cut:] if part]
    if inside(base, suite):
        return "inside"
    if not ancestor(base, suite):
        return "none"
    segs = suite[len(base.rstrip("/")) + 1:].split("/")
    if not rest:
        return "ancestor"
    if seg_match(rest, segs, tail):
        return "inside"
    return "ancestor" if any(seg_match(rest, segs[:k], "none") for k in range(1, len(segs))) else "none"


def worst(texts: list[str], folder: str, home: str, suite: str, tail: str = "any") -> str:
    kinds = {reach(alt, folder, home, suite, tail) for text in texts for alt in braces(text)}
    return next((kind for kind in ("inside", "ancestor") if kind in kinds), "none")


def names_suite(glob: str) -> bool:
    """`glob` is the suite folder, `tests` or `acceptance`, give or take an anchor and a trailing `/**`."""
    glob = re.sub(r"^(/|\./|\*\*/)", "", glob, count=1)
    return re.sub(r"(/\*\*/\*|/\*\*|/)$", "", glob, count=1) in SUITE_NAMES


def suites(dirs: list[str]) -> list[tuple[str, str]]:
    """(top, suite folder) of each git top that holds `dev-loop.json` and the suite folder, normalized."""
    found = []
    for folder in dirs:
        top = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        path = Path(top.stdout.strip())
        if top.returncode == 0 and (path / "dev-loop.json").is_file() and (path / ACCEPTANCE_DIR).is_dir():
            found.append((norm(str(path)), norm(f"{path}/{ACCEPTANCE_DIR}")))
    return list(dict.fromkeys(found))


def grep_tool(tool_input: dict, folder: str, home: str, suite: str) -> bool:
    path = tool_input.get("path")
    kind = reach(path if isinstance(path, str) and path else ".", folder, home, suite)
    glob = tool_input.get("glob")
    excluded = isinstance(glob, str) and glob.startswith("!") and names_suite(glob[1:])
    return kind == "inside" or (kind == "ancestor" and not excluded)


def glob_tool(tool_input: dict, folder: str, home: str, suite: str) -> bool:
    path, pattern = tool_input.get("path"), tool_input.get("pattern")
    if not isinstance(pattern, str):
        return False
    root = absolute(path if isinstance(path, str) and path else ".", folder, home)
    full = pattern if pattern.startswith("/") else f"{root}/{pattern}"
    return worst([full], folder, home, suite, "some") == "inside"


def bash_denied(command: str, folder: str, tops: list[tuple[str, str]], home: str) -> bool:
    return False


def decide(hook: object, env: Mapping[str, str]) -> dict:
    """The search rule on one PreToolUse hook call of either harness: `{"deny": bool, "reason": str | None}`."""
    if not isinstance(hook, dict):
        return {"deny": True, "reason": NOT_OBJECT}
    tool, tool_input = hook.get("tool_name"), hook.get("tool_input")
    if (not hook.get("agent_id") or hook.get("agent_type") in TESTERS or tool not in ("Grep", "Glob", "Bash")
            or not isinstance(tool_input, dict)):
        return {"deny": False, "reason": None}
    dirs = [folder for folder in (hook.get("cwd"), env.get("CLAUDE_PROJECT_DIR")) if isinstance(folder, str) and folder]
    tops = suites(dirs)
    if not tops:
        return {"deny": False, "reason": None}
    home, folder = env.get("HOME", "/"), norm(dirs[0])
    if tool == "Bash":
        command = tool_input.get("command")
        denied = isinstance(command, str) and bash_denied(command, folder, tops, home)
    else:
        check = grep_tool if tool == "Grep" else glob_tool
        denied = any(check(tool_input, folder, home, suite) for _, suite in tops)
    return {"deny": True, "reason": D8_REASON} if denied else {"deny": False, "reason": None}


def cmd_search_wall(args: argparse.Namespace) -> int:
    try:
        hook = json.loads(sys.stdin.read())
    except ValueError:
        hook = None
    return emit(decide(hook, os.environ), 0)

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
from seal import ACCEPTANCE_DIR, SEARCH_WORD

D8_REASON = (f"Blocked: this search reaches the held-out suite {ACCEPTANCE_DIR}. Search the source folders by name "
             f"(grep -rn <name> src lib, Grep path src), or exclude the suite (grep --exclude-dir=acceptance, "
             f"rg -g '!{ACCEPTANCE_DIR}/**', git grep <name> -- . ':!{ACCEPTANCE_DIR}', "
             f"find . -path ./{ACCEPTANCE_DIR} -prune -o ..., Grep glob !{ACCEPTANCE_DIR}/**).")
NOT_OBJECT = "Blocked: guard error: hook input is not a JSON object"
TESTERS = ("spec-tester", "spec_tester")
WILD = re.compile(r"[*?\[{]")
SUITE_NAMES = ("acceptance", "tests", ACCEPTANCE_DIR)
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\1([^\n]*)\n(?:[^\n]*\n)*?\t*\2(?=\n|$)")
OPS = ";&|()<>"
KEYWORDS = ("!", "{", "}", "if", "then", "else", "elif", "do", "while", "until", "time")
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
GREP_LONG = ("--regexp", "--file", "--max-count", "--after-context", "--before-context", "--context", "--directories",
             "--devices", "--include", "--exclude", "--exclude-dir", "--label")
RG_LONG = ("--regexp", "--file", "--glob", "--iglob", "--type", "--type-not", "--max-count", "--after-context",
           "--before-context", "--context", "--threads", "--max-columns", "--replace", "--max-depth", "--encoding", "--sort",
           "--sortr", "--type-add", "--ignore-file", "--max-filesize", "--pre")
GIT_GREP_LONG = ("--max-depth", "--threads", "--max-count", "--context", "--after-context", "--before-context")
GIT_VALUE_OPTIONS = ("-c", "--git-dir", "--work-tree", "--namespace", "--config-env")
PATTERN_GIVEN = {"-e", "-f", "--regexp", "--file"}


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


def shell_tokens(text: str) -> list[tuple[str, str]]:
    """`("op", text)` and `("word", unquoted text)` tokens of a shell command; ValueError on an unclosed quote."""
    out: list[tuple[str, str]] = []
    word: str | None = None
    quote, i = "", 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = ""
            elif quote == '"' and c == "\\" and text[i + 1:i + 2] in ('"', "\\", "$", "`"):
                i += 1
                word = (word or "") + text[i]
            else:
                word = (word or "") + c
        elif c == "\\":
            if text[i + 1:i + 2] != "\n":
                word = (word or "") + text[i + 1:i + 2]
            i += 1
        elif c in "'\"":
            quote, word = c, word or ""
        elif c in " \t\n`" or c in OPS:
            if word is not None:
                out.append(("word", word))
                word = None
            end = i + 1
            while c in OPS and end < len(text) and text[end] in OPS:
                end += 1
            if c in "\n`" or c in OPS:
                out.append(("op", ";" if c in "\n`" else text[i:end]))
            i = end
            continue
        else:
            word = (word or "") + c
        i += 1
    if quote:
        raise ValueError("unclosed quote")
    return out + ([("word", word)] if word is not None else [])


def simple_commands(tokens: list[tuple[str, str]]) -> list[list[str]]:
    """The words of each simple command, split at `;`, `&`, `&&`, `|`, `||`, `(`, `)`, redirections dropped."""
    commands: list[list[str]] = [[]]
    skip = False
    for kind, text in tokens:
        if kind == "op" and (set(text) & set("();|") or text in ("&", "&&")):
            commands.append([])
            skip = False
        elif kind == "op":
            if commands[-1] and commands[-1][-1].isdigit():
                commands[-1].pop()
            skip = True
        elif skip:
            skip = False
        else:
            commands[-1].append(text)
    return [words for words in commands if words]


def parse_options(args: list[str], short: str, long: tuple[str, ...]) -> tuple[list[tuple[str, str | None]], list[str], int | None]:
    """Getopt-style options as `(name, value)`, the operands, and how many operands came before `--` (None: no `--`)."""
    options: list[tuple[str, str | None]] = []
    operands: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            return options, operands + args[i:], len(operands)
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if not eq and name in long and i < len(args):
                value, i = args[i], i + 1
            options.append((name, value if eq or name in long else None))
        elif arg.startswith("-") and len(arg) > 1:
            for k, letter in enumerate(arg[1:], 2):
                if letter in short:
                    value = arg[k:]
                    if not value and i < len(args):
                        value, i = args[i], i + 1
                    options.append(("-" + letter, value))
                    break
                options.append(("-" + letter, None))
        else:
            operands.append(arg)
    return options, operands, None


def roots_denied(roots: list[str], folder: str, home: str, tops: list[tuple[str, str]], excluded: bool) -> bool:
    kinds = {worst(roots, folder, home, suite) for _, suite in tops}
    return "inside" in kinds or ("ancestor" in kinds and not excluded)


def grep_denied(name: str, args: list[str], folder: str, home: str, tops: list[tuple[str, str]]) -> bool:
    options, operands, _ = parse_options(args, "efmABCdD", GREP_LONG)
    names = {option for option, _ in options}
    recursive = (name == "rgrep" or bool(names & {"-r", "-R", "--recursive", "--dereference-recursive"})
                 or ("-d", "recurse") in options or ("--directories", "recurse") in options)
    if not recursive:
        return False
    roots = operands if names & PATTERN_GIVEN else operands[1:]
    excluded = any(option == "--exclude-dir" and value is not None
                   and (fnmatchcase("acceptance", value) or fnmatchcase("tests", value)) for option, value in options)
    return roots_denied(roots or ["."], folder, home, tops, excluded)


def rg_denied(args: list[str], folder: str, home: str, tops: list[tuple[str, str]]) -> bool:
    options, operands, _ = parse_options(args, "efgtTmABCjMrdE", RG_LONG)
    names = {option for option, _ in options}
    roots = operands if names & PATTERN_GIVEN or "--files" in names else operands[1:]
    excluded = any(option in ("-g", "--glob", "--iglob") and value and value.startswith("!") and names_suite(value[1:])
                   for option, value in options)
    return roots_denied(roots or ["."], folder, home, tops, excluded)


def pathspec(spec: str) -> tuple[set[str], str]:
    """The magic words (`top`, `exclude`, ...) and the path of a git pathspec."""
    if spec.startswith(":("):
        magic, _, path = spec[2:].partition(")")
        return set(magic.split(",")), path
    found = re.match(r":([/!^]*):?(.*)", spec, re.S)
    if not found:
        return set(), spec
    flags = set(found.group(1))
    return ({"top"} if "/" in flags else set()) | ({"exclude"} if flags & {"!", "^"} else set()), found.group(2)


def git_grep_denied(args: list[str], folder: str, home: str, tops: list[tuple[str, str]]) -> bool:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            folder = posixpath.normpath(absolute(args[i + 1], folder, home))
        i += 2 if args[i] in ("-C", *GIT_VALUE_OPTIONS) else 1
    if args[i:i + 1] != ["grep"]:
        return False
    options, operands, split = parse_options(args[i + 1:], "efmABC", GIT_GREP_LONG)
    if not {option for option, _ in options} & {"-e", "-f"}:
        operands, split = operands[1:], None if split is None else max(split - 1, 0)
    specs = operands[split:] if split is not None else [
        operand for operand in operands if operand.startswith(":") or os.path.lexists(absolute(operand, folder, home))]
    top = next((top for top, _ in tops if inside(norm(folder), top)), folder)
    positive, exclude = [], []
    for spec in specs:
        magic, path = pathspec(spec)
        path = absolute(path, top if "top" in magic else folder, home)
        if "exclude" in magic:
            exclude.append(norm(path))
        else:
            literal = re.split(r"[*?\[]", path, maxsplit=1)
            positive.append(literal[0] if len(literal) == 1 else posixpath.dirname(literal[0]) or "/")
    for _, suite in tops:
        excluded = any(path == suite or ancestor(path, suite) for path in exclude)
        if roots_denied(positive or [folder], folder, home, [("", suite)], excluded):
            return True
    return False


def find_excluded(printed: str, expr: list[str]) -> bool:
    """`expr` prunes the suite, printed by find as `printed`, or keeps every file under it out."""
    words = [word.lower() for word in expr] + ["", ""]
    for k, word in enumerate(expr):
        value, after = words[k + 1], words[k + 2]
        if word in ("-path", "-ipath", "-wholename") and after == "-prune" and fnmatchcase(printed, value):
            return True
        if word in ("-name", "-iname") and after == "-prune" and (fnmatchcase("acceptance", value) or fnmatchcase("tests", value)):
            return True
        if word in ("-not", "!") and value == "-path" and all(fnmatchcase(printed + tail, after) for tail in ("/\x01", "/\x01/\x01")):
            return True
    return False


def find_denied(args: list[str], folder: str, home: str, tops: list[tuple[str, str]]) -> bool:
    i = 0
    while i < len(args) and (args[i] in ("-H", "-L", "-P", "-D") or args[i].startswith("-O")):
        i += 2 if args[i] == "-D" else 1
    roots = []
    while i < len(args) and not args[i].startswith(("-", "!", "(")):
        roots.append(args[i])
        i += 1
    for _, suite in tops:
        for root in roots or ["."]:
            for alt in braces(root):
                kind = reach(alt, folder, home, suite)
                base = norm(absolute(alt, folder, home))
                printed = f"{alt.rstrip('/')}/{suite[len(base.rstrip('/')) + 1:]}".lower()
                if kind == "inside" or (kind == "ancestor" and (WILD.search(alt) or not find_excluded(printed, args[i:]))):
                    return True
    return False


def bash_denied(command: str, folder: str, tops: list[tuple[str, str]], home: str) -> bool:
    """A simple command of `command` searches a root that reaches a suite without excluding it."""
    try:
        tokens = shell_tokens(HEREDOC.sub(lambda m: f"<<{m.group(2)}{m.group(3)}", command))
    except ValueError:
        return bool(SEARCH_WORD.search(command))
    for words in simple_commands(tokens):
        while words and (words[0] in KEYWORDS or ASSIGNMENT.match(words[0])):
            words = words[1:]
        if not words:
            continue
        name, args = posixpath.basename(words[0]), words[1:]
        if name in ("cd", "pushd"):
            target = next((arg for arg in args if not arg.startswith("-")), None if args else "~")
            folder = posixpath.normpath(absolute(target, folder, home)) if target else folder
        elif ((name in ("grep", "egrep", "fgrep", "rgrep") and grep_denied(name, args, folder, home, tops))
              or (name == "rg" and rg_denied(args, folder, home, tops))
              or (name == "git" and git_grep_denied(args, folder, home, tops))
              or (name == "find" and find_denied(args, folder, home, tops))):
            return True
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
    home, folder = env.get("HOME", "/"), dirs[0]
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

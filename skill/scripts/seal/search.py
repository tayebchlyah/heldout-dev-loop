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

SEARCH_BLOCK_MESSAGE = (f"Blocked: this search reaches the held-out suite {ACCEPTANCE_DIR}. Search the source folders by name "
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
             "--devices", "--include", "--exclude", "--exclude-dir", "--label", "--binary-files", "--group-separator",
             "--exclude-from", "--include-dir", "--include-from", "--include-fs", "--exclude-fs", "--encoding", "--filter",
             "--format", "--from", "--max-files", "--max-size", "--min-size", "--delay", "--replace", "--colors",
             "--colours", "--context-separator", "--file-type", "--file-extension", "--file-magic", "--jobs")
GREP_FLAGS = frozenset("""--recursive --dereference-recursive --extended-regexp --fixed-strings --basic-regexp --perl-regexp
    --ignore-case --no-ignore-case --word-regexp --line-regexp --invert-match --count --files-with-matches
    --files-without-match --only-matching --quiet --silent --byte-offset --with-filename --initial-tab --null
    --null-data --text --binary --unix-byte-offsets --version --help --color --colour --line-buffered --smart-case
    --column-number --hex --with-hex --ignore-binary --decompress --dereference-files --any-line --ascii --empty
    --files --fuzzy --passthru --split --tree --hidden --ignore-files --ungroup --mmap --pretty --tabs --width
    --sort --tag --hexdump --hyperlink --pager --line-number""".split())
RG_LONG = ("--regexp", "--file", "--glob", "--iglob", "--type", "--type-not", "--max-count", "--after-context",
           "--before-context", "--context", "--threads", "--max-columns", "--replace", "--max-depth", "--encoding", "--sort",
           "--sortr", "--type-add", "--type-clear", "--ignore-file", "--max-filesize", "--pre", "--color", "--colors",
           "--engine", "--context-separator", "--path-separator", "--pre-glob", "--dfa-size-limit", "--regex-size-limit",
           "--field-context-separator", "--field-match-separator", "--hostname-bin", "--hyperlink-format", "--generate")
RG_FLAGS = frozenset("""--auto-hybrid-regex --binary --block-buffered --byte-offset --case-sensitive --column --count
    --count-matches --crlf --debug --files --files-with-matches --files-without-match --fixed-strings --follow
    --glob-case-insensitive --heading --help --hidden --ignore --ignore-case --ignore-dot --ignore-exclude
    --ignore-file-case-insensitive --ignore-files --ignore-global --ignore-messages --ignore-parent --ignore-vcs
    --include-zero --invert-match --json --line-buffered --line-number --line-regexp --max-columns-preview --messages
    --mmap --multiline --multiline-dotall --null --null-data --one-file-system --only-matching --passthru --pcre2
    --pcre2-unicode --pcre2-version --pretty --quiet --require-git --search-zip --smart-case --sort-files --stats
    --stop-on-nonmatch --text --trace --trim --type-list --unicode --unrestricted --version --vimgrep
    --with-filename --word-regexp""".split())
GIT_GREP_LONG = ("--max-depth", "--threads", "--max-count", "--context", "--after-context", "--before-context")
GIT_GREP_FLAGS = frozenset("""--all-match --basic-regexp --break --cached --color --column --count --exclude-standard
    --ext-grep --extended-regexp --files-with-matches --files-without-match --fixed-strings --full-name
    --function-context --heading --ignore-case --invert-match --line-number --name-only --null --only-matching
    --open-files-in-pager --perl-regexp --quiet --recurse-submodules --recursive --show-function --text --textconv
    --untracked --word-regexp --index --and --or --not""".split())
BRACE_CAP = 256
UNKNOWN_DIR = "$"
FIND_ACTIONS = {"-print", "-print0", "-ls", "-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0",
                "-fls", "-printf"}
GIT_VALUE_OPTIONS = ("-c", "--git-dir", "--work-tree", "--namespace", "--config-env")
PATTERN_GIVEN = {"-e", "-f", "--regexp", "--file"}


def norm(path: str, fold: bool = True) -> str:
    path = os.path.realpath(re.sub(r"/+", "/", path))
    path = path[len("/private"):] if path.startswith("/private/") else path
    return path.lower() if fold else path


def move(folder: str, target: str, home: str) -> str:
    path = absolute(target, folder, home)
    return UNKNOWN_DIR if re.search(r"[$`]", path) else posixpath.normpath(path)


def absolute(text: str, folder: str, home: str) -> str:
    if text == "~" or text.startswith("~/"):
        text = home + text[1:]
    return text if text.startswith("/") else f"{folder}/{text}"


def inside(path: str, suite: str) -> bool:
    return path == suite or path.startswith(suite + "/")


def ancestor(path: str, suite: str) -> bool:
    return path == "/" or suite.startswith(path + "/")


def braces(text: str) -> list[str] | None:
    texts = [text]
    while True:
        grown, done = [], True
        for item in texts:
            found = re.search(r"\{([^{}]*,[^{}]*)\}", item)
            if found:
                done = False
                grown += [item[:found.start()] + part + item[found.end():] for part in found.group(1).split(",")]
            else:
                grown.append(item)
        if len(grown) > BRACE_CAP:
            return None
        if done:
            return grown
        texts = grown


def seg_match(rest: list[str], segs: list[str], tail: str) -> bool:
    if not segs:
        return {"any": True, "some": bool(rest), "none": all(part == "**" for part in rest)}[tail]
    if not rest:
        return False
    if rest[0] == "**":
        return seg_match(rest[1:], segs, tail) or seg_match(rest, segs[1:], tail)
    return fnmatchcase(segs[0], rest[0]) and seg_match(rest[1:], segs[1:], tail)


def reach(text: str, folder: str, home: str, suite: str, tail: str = "any") -> str:
    full = absolute(text, folder, home)
    if re.search(r"[$`]", full):
        return "inside"
    parts = full.split("/")
    cut = next((i for i, part in enumerate(parts) if WILD.search(part)), len(parts))
    base, rest = norm("/".join(parts[:cut]) or "/"), [part.lower() for part in parts[cut:] if part]
    if ".." in rest:
        return "inside"
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
    alts = [braces(text) for text in texts]
    if None in alts:
        return "inside"
    kinds = {reach(alt, folder, home, suite, tail) for group in alts for alt in group}
    return next((kind for kind in ("inside", "ancestor") if kind in kinds), "none")


def suites(dirs: list[str]) -> list[tuple[str, str]]:
    found = []
    for folder in dirs:
        top = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        path = Path(top.stdout.strip())
        if top.returncode == 0 and (path / "dev-loop.json").is_file() and (path / ACCEPTANCE_DIR).is_dir():
            found.append((norm(str(path)), norm(f"{path}/{ACCEPTANCE_DIR}")))
    return list(dict.fromkeys(found))


def glob_excludes(value: str, root: str, folder: str, home: str, suite: str, fold: bool) -> bool:
    value = value.lower() if fold else value
    if "/" not in value.rstrip("/"):
        return value.rstrip("/") in ("acceptance", "tests")
    value = re.sub(r"(/\*\*/\*|/\*\*|/)$", "", value, count=1)
    if value.startswith("**/"):
        return value[3:] in ("acceptance", "tests", ACCEPTANCE_DIR)
    base = norm(folder)
    if ".." in root.split("/") or not inside(norm(absolute(root, folder, home)), base):
        return False
    if not suite.startswith(base.rstrip("/") + "/"):
        return False
    segs = suite[len(base.rstrip("/")) + 1:].split("/")
    return value.removeprefix("/") in ["/".join(segs[:k]) for k in range(1, len(segs) + 1)]


def globs_exclude(globs: list[tuple[str, bool]], root: str, folder: str, home: str, suite: str) -> bool:
    last = max((k for k, (value, fold) in enumerate(globs)
                if value.startswith("!") and glob_excludes(value[1:], root, folder, home, suite, fold)), default=-1)
    return last >= 0 and all(value.startswith("!") for value, _ in globs[last + 1:])


def grep_tool(tool_input: dict, folder: str, home: str, suite: str) -> bool:
    path = tool_input.get("path")
    root = path if isinstance(path, str) and path else "."
    glob = tool_input.get("glob")
    globs = [(part, False) for part in re.split(r"[\s,]+", glob) if part] if isinstance(glob, str) else []
    kind = reach(root, folder, home, suite)
    return kind == "inside" or (kind == "ancestor" and not globs_exclude(globs, root, folder, home, suite))


def glob_tool(tool_input: dict, folder: str, home: str, suite: str) -> bool:
    path, pattern = tool_input.get("path"), tool_input.get("pattern")
    if not isinstance(pattern, str):
        return False
    root = absolute(path if isinstance(path, str) and path else ".", folder, home)
    full = pattern if pattern.startswith("/") else f"{root}/{pattern}"
    return worst([full], folder, home, suite, "some") == "inside"


def shell_tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    word: str | None = None
    quote, i, ticks = "", 0, 0
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
            if c == "`":
                ticks += 1
                out += [("word", "`"), ("op", "(")] if ticks % 2 else [("op", ")")]
            elif c == "\n" or c in OPS:
                out.append(("op", ";" if c == "\n" else text[i:end]))
            i = end
            continue
        else:
            word = (word or "") + c
        i += 1
    if quote:
        raise ValueError("unclosed quote")
    return out + ([("word", word)] if word is not None else [])


def simple_commands(tokens: list[tuple[str, str]]) -> list:
    items: list = []
    words: list[str] = []
    piped = skip = False

    def flush(op: str) -> None:
        nonlocal words, piped
        if words:
            items.append((words, piped or op in ("|", "|&", "&")))
        words, piped = [], op in ("|", "|&")

    for kind, text in tokens:
        if kind == "op" and (set(text) & set("();|") or text in ("&", "&&")):
            flush(text)
            items += [c for c in text if c in "()"]
            skip = False
        elif kind == "op":
            if words and words[-1].isdigit():
                words.pop()
            skip = True
        elif skip:
            skip = False
        else:
            words.append(text)
    flush(";")
    return items


def parse_options(args: list[str], short: str, long: tuple[str, ...], flags: frozenset[str]) -> tuple[list[tuple[str, str | None]], list[str], int | None]:
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
            if not (eq or name in long or name in flags or name.startswith("--no-")) and i < len(args) and not args[i].startswith("-"):
                options.append(("?", name))
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


def roots_denied(roots: list[str], folder: str, home: str, tops: list[tuple[str, str]], excluded) -> bool:
    for _, suite in tops:
        for root in roots:
            kind = worst([root], folder, home, suite)
            if kind == "inside" or (kind == "ancestor" and not excluded(root, suite)):
                return True
    return False


def grep_denied(name: str, args: list[str], folder: str, home: str, tops: list[tuple[str, str]]) -> bool:
    options, operands, _ = parse_options(args, "efmABCdD", GREP_LONG, GREP_FLAGS)
    names = {option for option, _ in options}
    recursive = (name == "rgrep" or bool(names & {"-r", "-R", "--recursive", "--dereference-recursive"})
                 or ("-d", "recurse") in options or ("--directories", "recurse") in options)
    if not recursive:
        return False
    if "?" in names:
        return True
    roots = operands if names & PATTERN_GIVEN else operands[1:]
    skipped = any(option == "--exclude-dir" and value is not None
                  and (fnmatchcase("acceptance", value) or fnmatchcase("tests", value)) for option, value in options)
    return roots_denied(roots or ["."], folder, home, tops, lambda root, suite: skipped)


def rg_denied(args: list[str], folder: str, home: str, tops: list[tuple[str, str]]) -> bool:
    options, operands, _ = parse_options(args, "efgtTmABCjMrdE", RG_LONG, RG_FLAGS)
    names = {option for option, _ in options}
    if "?" in names:
        return True
    roots = operands if names & PATTERN_GIVEN or "--files" in names else operands[1:]
    globs = [(value or "", option == "--iglob") for option, value in options if option in ("-g", "--glob", "--iglob")]
    return roots_denied(roots or ["."], folder, home, tops,
                        lambda root, suite: globs_exclude(globs, root, folder, home, suite))


def pathspec(spec: str) -> tuple[set[str], str]:
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
            folder = move(folder, args[i + 1], home)
        i += 2 if args[i] in ("-C", *GIT_VALUE_OPTIONS) else 1
    if args[i:i + 1] != ["grep"]:
        return False
    options, operands, split = parse_options(args[i + 1:], "efmABC", GIT_GREP_LONG, GIT_GREP_FLAGS)
    if "?" in {option for option, _ in options}:
        return True
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
            exclude.append((norm(path), norm(path, fold=False), "icase" in magic))
        else:
            literal = re.split(r"[*?\[]", path, maxsplit=1)
            positive.append(literal[0] if len(literal) == 1 else posixpath.dirname(literal[0]) or "/")

    top_of = {suite: found for found, suite in tops}

    def excluded(root: str, suite: str) -> bool:
        for folded, exact, icase in exclude:
            tail = exact[len(top_of[suite]):] if folded.startswith(top_of[suite]) else ""
            if (folded == suite or ancestor(folded, suite)) and (icase or tail == tail.lower()):
                return True
        return False

    return roots_denied(positive or [folder], folder, home, tops, excluded)


def find_excluded(printed: str, expr: list[str]) -> bool:
    if (len(expr) >= 4 and expr[0] in ("-path", "-ipath", "-wholename", "-name", "-iname")
            and expr[2] == "-prune" and expr[3] in ("-o", "-or")):
        fold = expr[0] in ("-ipath", "-iname")
        value = expr[1].lower() if fold else expr[1]
        if expr[0] in ("-name", "-iname"):
            return any(fnmatchcase(name, value) for name in ("acceptance", "tests"))
        return fnmatchcase(printed.lower() if fold else printed, value)
    if {"-o", "-or", ","} & set(expr):
        return False
    for k, word in enumerate(expr[:-2]):
        if (word in ("-not", "!") and expr[k + 1] == "-path" and not FIND_ACTIONS & set(expr[:k])
                and all(fnmatchcase(printed + tail, expr[k + 2]) for tail in ("/\x01", "/\x01/\x01"))):
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
            alts = braces(root)
            if alts is None:
                return True
            for alt in alts:
                kind = reach(alt, folder, home, suite)
                base = norm(absolute(alt, folder, home))
                printed = f"{alt.rstrip('/')}/{suite[len(base.rstrip('/')) + 1:]}"
                slashed = alt.endswith("/") and alt != "/"
                if kind == "inside" or (kind == "ancestor" and (WILD.search(alt) or slashed or not find_excluded(printed, args[i:]))):
                    return True
    return False


def bash_denied(command: str, folder: str, tops: list[tuple[str, str]], home: str) -> bool:
    try:
        tokens = shell_tokens(HEREDOC.sub(lambda m: f"<<{m.group(2)}{m.group(3)}", command))
    except ValueError:
        return bool(SEARCH_WORD.search(command))
    scopes: list[str] = []
    for item in simple_commands(tokens):
        if item == "(":
            scopes.append(folder)
            continue
        if item == ")":
            folder = scopes.pop() if scopes else folder
            continue
        words, detached = item
        while words and (words[0] in KEYWORDS or ASSIGNMENT.match(words[0])):
            words = words[1:]
        if not words:
            continue
        name, args = posixpath.basename(words[0]), words[1:]
        if name in ("cd", "pushd", "popd"):
            if detached:
                continue
            target = next((arg for arg in args if not arg.startswith("-")), "~" if name == "cd" else None)
            if name == "popd" or "-" in args or target is None or re.match(r"\+\d", target):
                folder = UNKNOWN_DIR
            else:
                folder = move(folder, target, home)
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
    if (not hook.get("agent_id") or hook.get("agent_type") in TESTERS or tool not in ("Grep", "Glob", "Bash", "Monitor")
            or not isinstance(tool_input, dict)):
        return {"deny": False, "reason": None}
    dirs = [folder for folder in (hook.get("cwd"), env.get("CLAUDE_PROJECT_DIR")) if isinstance(folder, str) and folder]
    tops = suites(dirs)
    if not tops:
        return {"deny": False, "reason": None}
    home, folder = env.get("HOME", "/"), dirs[0]
    if tool in ("Bash", "Monitor"):
        command = tool_input.get("command")
        denied = isinstance(command, str) and bash_denied(command, folder, tops, home)
    else:
        check = grep_tool if tool == "Grep" else glob_tool
        denied = any(check(tool_input, folder, home, suite) for _, suite in tops)
    return {"deny": True, "reason": SEARCH_BLOCK_MESSAGE} if denied else {"deny": False, "reason": None}


def cmd_search_wall(args: argparse.Namespace) -> int:
    try:
        hook = json.loads(sys.stdin.read())
    except ValueError:
        hook = None
    return emit(decide(hook, os.environ), 0)

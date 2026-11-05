#!/usr/bin/env python3
"""Repo checks of this repo only (not in the kit). One JSON object on stdout.

comments: each comment line, trailing .sh comment and private docstring added against <base> is of an allowed kind (CODING_STANDARDS.md); a docstring is private unless a .py file outside tests/ imports its name.
subjects: the subject of each commit in <base>..HEAD follows the gitmoji convention of cliff.toml.
<base>: git merge-base HEAD origin/main, else main, else none (skipped no-base).
promoted: the final tag --tag vX.Y.Z has the kit source of its highest release candidate vX.Y.Z-rc.N (no base). With --sha <rev>: the commit <rev> stands for the final tag, before it exists.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tokenize
import tomllib
import warnings
from pathlib import Path


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def merge_base(repo: Path) -> str | None:
    for upstream in ("origin/main", "main"):
        result = run_git(repo, "merge-base", "HEAD", upstream)
        if result.returncode == 0:
            return result.stdout.strip()
    return None


def gitmoji(repo: Path) -> list[str]:
    try:
        config = tomllib.loads((repo / "cliff.toml").read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read cliff.toml: {type(exc).__name__}") from exc
    emojis: list[str] = []
    for parser in config.get("git", {}).get("commit_parsers", []):
        match = re.match(r"\^\(\?:(.*?)\)", parser.get("message", ""))
        for emoji in match.group(1).split("|") if match else []:
            if emoji and emoji not in emojis:
                emojis.append(emoji)
    if not emojis:
        raise ValueError("cliff.toml has no gitmoji in git.commit_parsers")
    return emojis


def subject_ok(subject: str, emojis: list[str]) -> bool:
    alternatives = "|".join(re.escape(e) for e in sorted(emojis, key=len, reverse=True))
    return re.match(rf"(?:{alternatives})\([a-z0-9][a-z0-9-]*\) \S", subject) is not None


def subjects(repo: Path, base: str) -> tuple[dict, int]:
    try:
        emojis = gitmoji(repo)
    except ValueError as exc:
        return {"ok": False, "base": base, "error": str(exc)}, 1
    log = run_git(repo, "log", "--no-merges", "--format=%H%x09%s", f"{base}..HEAD")
    if log.returncode != 0:
        return {"ok": False, "base": base, "error": "git log failed"}, 1
    head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    violations, older = [], []
    for line in reversed(log.stdout.splitlines()):
        commit, _, subject = line.partition("\t")
        if not subject_ok(subject, emojis):
            (violations if commit == head else older).append({"commit": commit, "subject": subject})
    return {"ok": not violations, "base": base, "violations": violations, "warnings": older}, int(bool(violations))


CHECKED_SUFFIXES = (".py", ".sh", ".mjs", ".js", ".ts")
SKIPPED_PREFIXES = ("tests/acceptance/", ".agents/", ".claude/", ".codex/", ".dev-loop/", "node_modules/")
KINDS = re.compile(
    r"https?://|#\d+\b|\bRFC \d+|allow-comment: dependency"
    r"|(?:#|//)\s*(?:shortcut: [^,]+, \S|SPDX-License-Identifier|Copyright|License(?::|d under|\s*$)"
    r"|(?i:noqa)|nosec|isort:|type:|pyright:|pylint:|pragma|fmt: (?:off|on|skip)|eslint-|biome-ignore|@ts-|shellcheck)"
)
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
HEREDOC = re.compile(r"<<-?[ \t]*(?:\\(\w+)|(['\"])([^'\"]+)\2|([A-Za-z_]\w*))")
CODE_TOKENS = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}


def checked(path: str) -> bool:
    return path.endswith(CHECKED_SUFFIXES) and not path.startswith(SKIPPED_PREFIXES)


def kind_ok(text: str) -> bool:
    return KINDS.search(text) is not None


def python_tokens(text: str) -> list[tokenize.TokenInfo] | None:
    try:
        return list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return None


def code_rows(tokens: list[tokenize.TokenInfo]) -> set[int]:
    return {row for tok in tokens if tok.type not in CODE_TOKENS for row in range(tok.start[0], tok.end[0] + 1)}


def shell_trailing(text: str) -> dict[int, str]:
    found: dict[int, str] = {}
    stack: list[str] = []
    word = None
    for row, line in enumerate(text.splitlines(), 1):
        if word is not None:
            word = None if line.strip() == word else word
            continue
        opened, i = bool(stack), 0
        while i < len(line):
            char, top = line[i], stack[-1] if stack else ""
            if top == "'":
                stack.pop() if char == "'" else None
            elif top in ('"', "$'"):
                i += char == "\\"
                if char == top[-1]:
                    stack.pop()
                elif top == '"' and line.startswith("$(", i):
                    stack.append("$(")
                    i += 1
            elif char == "\\":
                i += 1
            elif char == ")" and top in ("$(", "("):
                stack.pop()
            elif char == "(" and top in ("$(", "("):
                stack.append("(")
            elif char == "'" and line[i - 1:i] == "$":
                stack.append("$'")
            elif char in "'\"":
                stack.append(char)
            elif heredoc_at(line, i) and not stack:
                heredoc = HEREDOC.match(line, i)
                word, i = next(g for g in heredoc.group(1, 3, 4) if g), heredoc.end()
                continue
            elif char == "#" and not stack and (i == 0 or line[i - 1] in " \t"):
                if line[:i].strip() and not opened:
                    found[row] = line[i:].strip()
                break
            i += 1
    return found


def heredoc_at(line: str, i: int) -> bool:
    before = line[:i]
    return (line.startswith("<<", i) and line[i - 1:i] != "<" and line[i + 2:i + 3] != "<"
            and before.count("((") <= before.count("))") and HEREDOC.match(line, i) is not None)


def trailing_comments(path: str, text: str) -> dict[int, str]:
    if path.endswith(".sh"):
        return shell_trailing(text)
    tokens = python_tokens(text) if path.endswith(".py") else None
    if tokens is None:
        return {}
    code = code_rows(tokens)
    return {tok.start[0]: tok.string.strip() for tok in tokens if tok.type == tokenize.COMMENT and tok.start[0] in code}


def parse_python(text: str) -> ast.Module | None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.parse(text)
        except (SyntaxError, ValueError):
            return None


def module_stem(path: str) -> str:
    file = Path(path)
    return file.parent.name if file.name == "__init__.py" else file.stem


def dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if not isinstance(node, ast.Attribute):
        return None
    head = dotted(node.value)
    return f"{head}.{node.attr}" if head else None


def import_pairs(tree: ast.Module) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module:
                    pairs.add((node.module.split(".")[-1], alias.name))
                bound[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name] = alias.name.split(".")[-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (name := dotted(node.value)) in bound:
            pairs.add((bound[name], node.attr))
    return pairs


def owner_public(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, scope: str, public: set[str]) -> bool:
    if scope == "module":
        return node.name in public
    if scope == "class" and not isinstance(node, ast.ClassDef):
        return not node.name.startswith("_") or (node.name.startswith("__") and node.name.endswith("__"))
    return False


def visit_docstrings(node: ast.AST, scope: str, public: set[str], found: list[tuple[int, int, str]]) -> None:
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, DEFS):
            visit_docstrings(child, scope, public, found)
            continue
        is_public = owner_public(child, scope, public)
        doc = ast.get_docstring(child)
        if doc is not None and not is_public:
            first = child.body[0]
            found.append((first.lineno, first.end_lineno or first.lineno, doc))
        inner = "class" if is_public and scope == "module" and isinstance(child, ast.ClassDef) else "private"
        visit_docstrings(child, inner, public, found)


def private_docstrings(tree: ast.Module, public: set[str]) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    visit_docstrings(tree, "module", public, found)
    return sorted(found)


def docstring_texts(text: str) -> set[str]:
    tree = parse_python(text)
    nodes = [] if tree is None else [n for n in ast.walk(tree) if isinstance(n, (ast.Module, *DEFS))]
    return {doc for n in nodes if (doc := ast.get_docstring(n)) is not None}


def comment_lines(path: str, text: str) -> dict[int, str]:
    lines = text.splitlines()
    if path.endswith(".py"):
        tokens = python_tokens(text)
        if tokens is not None:
            code = code_rows(tokens)
            return {tok.start[0]: lines[tok.start[0] - 1].strip() for tok in tokens
                    if tok.type == tokenize.COMMENT and tok.start[0] not in code}
    marker = "//" if path.endswith((".mjs", ".js", ".ts")) else "#"
    return {n: line.strip() for n, line in enumerate(lines, 1) if line.strip().startswith(marker)}


def allowed_lines(path: str, text: str) -> set[int]:
    lines = text.splitlines()
    comments = comment_lines(path, text)
    allowed: set[int] = set()
    n = 2 if lines and lines[0].startswith("#!") else 1
    if n == 2 and 1 in comments:
        allowed.add(1)
    while n <= len(lines) and (n in comments or not lines[n - 1].strip()):
        if n in comments:
            allowed.add(n)
        n += 1
    for n in sorted(comments):
        if kind_ok(comments[n]) or (n - 1 in allowed and n - 1 in comments):
            allowed.add(n)
    return allowed


class GitFailed(Exception):
    pass


def public_pairs(repo: Path) -> dict[tuple[str, str], set[str]]:
    listing = run_git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listing.returncode != 0:
        raise GitFailed("git ls-files")
    pairs: dict[tuple[str, str], set[str]] = {}
    for path in listing.stdout.split("\0"):
        if not path.endswith(".py") or path.startswith(("tests/", *SKIPPED_PREFIXES)):
            continue
        try:
            tree = parse_python((repo / path).read_text())
        except (OSError, UnicodeDecodeError):
            continue
        for pair in import_pairs(tree) if tree is not None else ():
            pairs.setdefault(pair, set()).add(path)
    return pairs


def base_comments(repo: Path, base: str) -> tuple[set[str], set[str]]:
    listing = subprocess.run(["git", "ls-tree", "-r", "-z", "--name-only", base], cwd=repo, capture_output=True)
    if listing.returncode != 0:
        raise GitFailed("git ls-tree")
    paths = [p for p in listing.stdout.decode("utf-8", "replace").split("\0") if p and "\n" not in p and checked(p)]
    batch = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, capture_output=True,
                           input="".join(f"{base}:{p}\n" for p in paths).encode())
    if batch.returncode != 0:
        raise GitFailed("git cat-file")
    out, pos, texts, docs = batch.stdout, 0, set(), set()
    for path in paths:
        end = out.index(b"\n", pos)
        header = out[pos:end].split()
        pos = end + 1
        if len(header) != 3 or header[1] != b"blob":
            continue
        size = int(header[2])
        blob, pos = out[pos:pos + size], pos + size + 1
        try:
            source = blob.decode()
            texts.update(comment_lines(path, source).values())
            texts.update(trailing_comments(path, source).values())
            if path.endswith(".py"):
                docs.update(docstring_texts(source))
        except UnicodeDecodeError:
            continue
    return texts, docs


def added_lines(repo: Path, base: str) -> dict[str, set[int] | None]:
    changed = run_git(repo, "diff", "--name-only", "-z", "--no-renames", base)
    untracked = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    if changed.returncode != 0 or untracked.returncode != 0:
        raise GitFailed("git diff" if changed.returncode != 0 else "git ls-files")
    added: dict[str, set[int] | None] = {p: None for p in untracked.stdout.split("\0") if p and checked(p)}
    for path in changed.stdout.split("\0"):
        if not path or not checked(path) or path in added:
            continue
        diff = run_git(repo, "diff", "-U0", "--no-renames", "--no-ext-diff", "--no-color", base, "--", path)
        if diff.returncode != 0:
            raise GitFailed("git diff")
        rows: set[int] = set()
        for match in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", diff.stdout, re.M):
            start, count = int(match.group(1)), int(match.group(2) or 1)
            rows.update(range(start, start + count))
        added[path] = rows
    return added


def docstring_violations(path: str, text: str, rows: set[int] | None,
                         pairs: dict[tuple[str, str], set[str]], old_docs: set[str]) -> list[dict]:
    tree = parse_python(text) if path.endswith(".py") else None
    if tree is None:
        return []
    stem = module_stem(path)
    public = {name for (owner, name), importers in pairs.items() if owner == stem and importers - {path}}
    found = []
    for first, last, doc in private_docstrings(tree, public):
        if (rows is None or not rows.isdisjoint(range(first, last + 1))) and not kind_ok(doc) and doc not in old_docs:
            line = next((part.strip() for part in doc.splitlines() if part.strip()), "")
            found.append({"path": path, "line": first, "text": line})
    return found


def comments(repo: Path, base: str) -> tuple[dict, int]:
    try:
        old, old_docs = base_comments(repo, base)
        added = added_lines(repo, base)
        pairs = public_pairs(repo)
    except GitFailed as exc:
        return {"ok": False, "base": base, "error": f"{exc} failed"}, 1
    violations = []
    for path, rows in added.items():
        try:
            text = (repo / path).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        allowed = allowed_lines(path, text)
        for n, line in comment_lines(path, text).items():
            if (rows is None or n in rows) and n not in allowed and line not in old:
                violations.append({"path": path, "line": n, "text": line})
        for n, line in trailing_comments(path, text).items():
            if (rows is None or n in rows) and not kind_ok(line) and line not in old:
                violations.append({"path": path, "line": n, "text": line})
        violations.extend(docstring_violations(path, text, rows, pairs, old_docs))
    violations.sort(key=lambda v: (v["path"], v["line"]))
    return {"ok": not violations, "base": base, "violations": violations}, int(bool(violations))


KIT_SOURCE = ("skill/", "skills/", "claude/", "codex/", "bin/", "lib/")
FINAL_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
CANDIDATE_TAG = re.compile(r"(v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))-rc\.([1-9]\d*)")


def promoted(repo: Path, tag: str, sha: str | None = None) -> tuple[dict, int]:
    candidate_form = CANDIDATE_TAG.fullmatch(tag)
    if not FINAL_TAG.fullmatch(tag) and not candidate_form:
        return {"error": f"not a vX.Y.Z or vX.Y.Z-rc.N tag: {tag}"}, 2
    if candidate_form:
        return {"ok": True, "tag": tag, "skipped": "not-a-final-tag"}, 0
    target = f"{sha}^{{commit}}" if sha is not None else f"refs/tags/{tag}^{{commit}}"
    final = run_git(repo, "rev-parse", "--verify", "--quiet", target)
    if final.returncode != 0:
        return {"error": f"unknown commit: {sha}" if sha is not None else f"unknown tag: {tag}"}, 2
    listing = run_git(repo, "tag", "--list", f"{tag}-rc.*")
    if listing.returncode != 0:
        return {"ok": False, "tag": tag, "error": "git tag failed"}, 1
    numbers = {}
    for name in listing.stdout.split():
        match = CANDIDATE_TAG.fullmatch(name)
        if match and match.group(1) == tag:
            numbers[int(match.group(2))] = name
    if not numbers:
        return {"ok": False, "tag": tag, "candidate": None, "violations": [], "reason": "no-release-candidate"}, 1
    candidate = numbers[max(numbers)]
    diff = run_git(repo, "diff", "--name-only", "-z", "--no-renames",
                   f"refs/tags/{candidate}^{{commit}}", final.stdout.strip())
    if diff.returncode != 0:
        return {"ok": False, "tag": tag, "candidate": candidate, "error": "git diff failed"}, 1
    violations = sorted(p for p in diff.stdout.split("\0") if p.startswith(KIT_SOURCE))
    return {"ok": not violations, "tag": tag, "candidate": candidate, "violations": violations}, int(bool(violations))


COMMANDS = {"comments": comments, "subjects": subjects}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=[*sorted(COMMANDS), "promoted"])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--tag")
    parser.add_argument("--sha")
    args = parser.parse_args(argv)
    if (args.command == "promoted") != (args.tag is not None):
        parser.error("--tag goes with promoted only")
    if args.sha is not None and args.command != "promoted":
        parser.error("--sha goes with promoted only")
    top = run_git(Path(args.repo), "rev-parse", "--show-toplevel") if Path(args.repo).is_dir() else None
    if top is None or top.returncode != 0:
        print(json.dumps({"error": f"not a git repository: {args.repo}"}))
        return 2
    repo = Path(top.stdout.strip())
    if args.command == "promoted":
        payload, code = promoted(repo, args.tag, args.sha)
        print(json.dumps(payload, ensure_ascii=False))
        return code
    base = merge_base(repo)
    if base is None:
        payload, code = {"ok": True, "base": None, "skipped": "no-base", "violations": []}, 0
    else:
        payload, code = COMMANDS[args.command](repo, base)
    print(json.dumps(payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())

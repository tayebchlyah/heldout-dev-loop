#!/usr/bin/env python3
"""Repo checks of this repo only (not in the kit). One JSON object on stdout.

comments: each comment line added against <base> is of an allowed kind (CODING_STANDARDS.md).
subjects: the subject of each commit in <base>..HEAD follows the gitmoji convention of cliff.toml.
<base>: git merge-base HEAD origin/main, else main, else none (skipped no-base).
promoted: the final tag --tag vX.Y.Z has the kit source of its highest release candidate vX.Y.Z-rc.N (no base). With --sha <rev>: the commit <rev> stands for the final tag, before it exists.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tokenize
import tomllib
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
    violations, warnings = [], []
    for line in reversed(log.stdout.splitlines()):
        commit, _, subject = line.partition("\t")
        if not subject_ok(subject, emojis):
            (violations if commit == head else warnings).append({"commit": commit, "subject": subject})
    return {"ok": not violations, "base": base, "violations": violations, "warnings": warnings}, int(bool(violations))


CHECKED_SUFFIXES = (".py", ".sh", ".mjs", ".js", ".ts")
SKIPPED_PREFIXES = ("tests/acceptance/", ".agents/", ".claude/", ".codex/", ".dev-loop/", "node_modules/")
KINDS = re.compile(
    r"https?://|#\d+\b|\bRFC \d+|allow-comment: dependency"
    r"|(?:#|//)\s*(?:shortcut: [^,]+, \S|SPDX-License-Identifier|Copyright|License(?::|d under|\s*$)"
    r"|noqa|type:|pyright:|pylint:|pragma|fmt: (?:off|on|skip)|eslint-|biome-ignore|@ts-|shellcheck)"
)
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


def trailing_comments(path: str, text: str) -> dict[int, str]:
    tokens = python_tokens(text) if path.endswith(".py") else None
    if tokens is None:
        return {}
    code = code_rows(tokens)
    return {tok.start[0]: tok.string.strip() for tok in tokens if tok.type == tokenize.COMMENT and tok.start[0] in code}


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


def base_comments(repo: Path, base: str) -> set[str]:
    listing = subprocess.run(["git", "ls-tree", "-r", "-z", "--name-only", base], cwd=repo, capture_output=True)
    if listing.returncode != 0:
        raise GitFailed("git ls-tree")
    paths = [p for p in listing.stdout.decode("utf-8", "replace").split("\0") if p and "\n" not in p and checked(p)]
    batch = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, capture_output=True,
                           input="".join(f"{base}:{p}\n" for p in paths).encode())
    if batch.returncode != 0:
        raise GitFailed("git cat-file")
    out, pos, texts = batch.stdout, 0, set()
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
        except UnicodeDecodeError:
            continue
    return texts


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


def comments(repo: Path, base: str) -> tuple[dict, int]:
    try:
        old = base_comments(repo, base)
        added = added_lines(repo, base)
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

#!/usr/bin/env python3
"""Repo checks of this repo only (not in the kit). One JSON object on stdout.

comments: each comment line added against <base> is of an allowed kind (CODING_STANDARDS.md).
subjects: the subject of each commit in <base>..HEAD follows the gitmoji convention of cliff.toml.
<base>: git merge-base HEAD origin/main, else main, else none (skipped no-base).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
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


COMMANDS = {"subjects": subjects}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    top = run_git(Path(args.repo), "rev-parse", "--show-toplevel") if Path(args.repo).is_dir() else None
    if top is None or top.returncode != 0:
        print(json.dumps({"error": f"not a git repository: {args.repo}"}))
        return 2
    repo = Path(top.stdout.strip())
    base = merge_base(repo)
    if base is None:
        payload, code = {"ok": True, "base": None, "skipped": "no-base", "violations": []}, 0
    else:
        payload, code = COMMANDS[args.command](repo, base)
    print(json.dumps(payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())

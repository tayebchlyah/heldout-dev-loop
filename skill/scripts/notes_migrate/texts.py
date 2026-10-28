"""Plan the notes migration texts: links (spec D15), frontmatter (D16), audit ids (D17), the D9 scans and the plan hash."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import NamedTuple

import devloop
from notes_migrate.links import code_ranges, find_links, resolve, rewrite
from notes_migrate.plan import Blocked, Move, plan_moves, scan
from release import project_note


class Write(NamedTuple):
    """The new `text` of a note at `path`, its place after the moves; sha256 of the UTF-8 bytes before and after.
    Encode `text` with `errors="surrogateescape"`: a note that is not UTF-8 keeps its bytes."""

    path: Path
    old_sha: str
    new_sha: str
    text: str


class Plan(NamedTuple):
    moves: list[Move]
    writes: list[Write]
    links: dict
    audit_ids: int
    outside_links: list[dict]
    outside_skipped: list[str]
    repo_links: list[dict]
    git_case: list[str]
    gitignore: list[str]
    ambiguous: list[str]
    blocked: list[Blocked]
    plan_hash: str


RUN_PATH = re.compile(r"(?<![\w-])security-audit/run-(\d+)/")
PROJECT_IDS = (
    (re.compile(r"^(- \d{4}-\d{2}-\d{2} )run-(\d+)( at )", re.M), r"\1Run \2\3"),
    (re.compile(r"(\(security, [\w-]+, )run-(\d+)\)"), r"\1Run \2)"),
    (re.compile(r"^(### Release audit )run-(\d+),", re.M), r"\1Run \2,"),
)
RUN_FOLDER = re.compile(r"security-audit/run-\d+/")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def _frontmatter(text: str, keys: dict[str, str]) -> str:
    """`text` with each of `keys` set (`feature` only added when missing), every other byte kept; a note with no
    frontmatter gets a block of `keys`; an unclosed block is left alone."""
    bom = "﻿" if text.startswith("﻿") else ""
    body = text[len(bom):]
    nl = "\r\n" if body.split("\n", 1)[0].endswith("\r") else "\n"
    lines = body.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return bom + "".join(f"{line}{nl}" for line in ["---", *(f"{k}: {v}" for k, v in keys.items()), "---"]) + body
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return text
    for name, value in keys.items():
        at = next((i for i in range(1, end) if re.match(rf"{name}\s*:", lines[i])), None)
        if at is None:
            lines.insert(end, f"{name}: {value}{nl}")
            end += 1
        elif name != "feature":
            ending = lines[at][len(lines[at].rstrip("\r\n")):]
            lines[at] = f"{name}: {value}{ending}"
    return bom + "".join(lines)


def _audit_ids(text: str, project: bool) -> tuple[str, int]:
    """D17: the run ids of the project note line kinds, and `security-audit/run-<N>/` paths, outside code."""
    count = 0
    for pattern, repl in (*(PROJECT_IDS if project else ()), (RUN_PATH, r"Security Audit/Run \1/")):
        code = code_ranges(text)

        def one(match: re.Match) -> str:
            nonlocal count
            if any(a <= match.start() < b for a, b in code):
                return match[0]
            count += 1
            return match.expand(repl)

        text = pattern.sub(one, text)
    return text, count


def _repo_links(repo: Path, olds: list[str]) -> list[dict]:
    """Lines of the repo's tracked text files outside `tests/acceptance/` that name an old path as whole segments."""
    if not olds:
        return []
    pattern = re.compile(r"(?:^|(?<=[/ `'\"(]))(?:" + "|".join(map(re.escape, sorted(olds, key=len, reverse=True)))
                         + r")(?=$|[/. `'\")])")
    found = []
    for rel in (devloop.git(repo, "ls-files", "-z") or "").split("\0"):
        if not rel or rel.startswith("tests/acceptance/"):
            continue
        try:
            text = (repo / rel).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found += [{"file": rel, "line": n} for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
    return found


def _git_case(notes: Path, moves: list[Move]) -> list[str]:
    """D11: the commands that make a case-insensitive git index follow each case-only rename of a tracked folder."""
    if devloop.git(notes, "config", "--bool", "core.ignorecase") != "true":
        return []
    commands = []
    for move in moves:
        old, new = (path.relative_to(notes).as_posix() for path in (move.src, move.dst))
        if move.role == "folder" and old != new and old.lower() == new.lower() and devloop.git(notes, "ls-files", "--", old):
            commands += [f"git rm -r --cached {shlex.quote(old)}", f"git add {shlex.quote(new)}"]
    return commands


def build_plan(repo: Path, notes: Path, kit_version: str) -> Plan:
    """The whole notes migration: the moves, the new text of each note it changes, the scans; changes nothing."""
    found = scan(notes)
    moves, blocked, ambiguous = plan_moves(repo, notes, kit_version, found)
    vault, files, placeholders = found.vault, found.files, set(found.placeholders)
    inside = [path for path in files.values() if path.is_relative_to(notes)]
    moved: dict[Path, Path] = {}
    for move in moves:
        if move.role == "folder":
            moved.update((path, move.dst / path.relative_to(move.src)) for path in inside if path.is_relative_to(move.src))
        elif move.dst != move.src:
            moved[move.src] = move.dst
        else:
            moved.pop(move.src, None)
    winners = {m.dst: m.src for m in moves if m.role == "board-merge" and m.dst != m.src}
    removed = {m.src for m in moves if m.role == "board-merge" and m.dst == m.src}
    gone = removed | {path for path in inside if winners.get(moved.get(path, path), path) != path}
    roles = {m.src: m for m in moves if m.role in ("spec", "plan", "framing")}
    with_spec = {m.dst.parent for m in roles.values() if m.role == "spec"}
    project = project_note(notes)
    notes_md = [(path, False) for path in sorted(inside) if path.suffix == ".md" and path not in placeholders | gone]
    notes_md += [(Path(root, name), True) for top in found.linked for root, _, names in os.walk(top)
                 for name in sorted(names) if name.endswith(".md")]
    writes, link_files, link_count, audit_ids = [], [], 0, 0
    for note, under_link in notes_md:
        old = note.read_bytes().decode("utf-8", "surrogateescape")
        new_path = moved.get(note, note)
        text, links = rewrite(old, note, new_path, moved, notes, vault, files)
        if note in roles:
            move = roles[note]
            keys = {"type": move.role, "feature": move.slug}
            if move.role == "plan" and move.dst.parent in with_spec:
                keys["spec"] = "Spec.md"
            text = _frontmatter(text, keys)
        rel = note.relative_to(notes).as_posix()
        ids = 0
        if not RUN_FOLDER.match(rel):
            text, ids = _audit_ids(text, note == project)
        if text == old:
            continue
        if under_link or os.path.islink(note):
            if Blocked(rel, "link") not in blocked:
                blocked.append(Blocked(rel, "link"))
            continue
        if links:
            link_files.append(new_path.relative_to(notes).as_posix())
        link_count += links
        audit_ids += ids
        writes.append(Write(new_path, _sha(old), _sha(text), text))
    outside_links, outside_skipped = [], []
    for path in sorted(files.values()) if vault else []:
        if path.is_relative_to(notes):
            continue
        rel = path.relative_to(vault).as_posix()
        if path in placeholders:
            if (path.name[1:-len(".icloud")] if path.name.endswith(".icloud") else path.name).endswith(".md"):
                outside_skipped.append(rel)
        elif path.suffix == ".md":
            text = path.read_text(encoding="utf-8", errors="replace")
            outside_links += [{"file": rel, "line": text.count("\n", 0, link.start) + 1, "link": text[link.start:link.end]}
                              for link in find_links(text) if resolve(link, path, notes, vault, files) in moved]
    plan_hash = hashlib.sha256(json.dumps(sorted(
        [["move", str(m.src), str(m.dst)] for m in moves] + [["write", str(w.path), w.old_sha, w.new_sha] for w in writes]
    )).encode()).hexdigest()
    return Plan(
        moves=moves,
        writes=writes,
        links={"files": link_files, "count": link_count},
        audit_ids=audit_ids,
        outside_links=outside_links,
        outside_skipped=outside_skipped,
        repo_links=_repo_links(repo, [m.src.relative_to(notes).as_posix() for m in moves if m.dst != m.src]),
        git_case=_git_case(notes, moves),
        gitignore=[item.path for item in blocked if item.reason == "gitignore"],
        ambiguous=ambiguous,
        blocked=blocked,
        plan_hash=plan_hash,
    )

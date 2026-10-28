"""Plan the notes migration: feature pairing (spec D10), folder renames (D11) and the guards (D13, D14)."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import NamedTuple

import devloop
from board.store import version_key
from notes_migrate.links import find_links, key, resolve


class Move(NamedTuple):
    """`src` (a path before the run) goes to `dst` (its final path). A `folder` move carries its whole tree;
    a move listed after the move of a parent folder names its source by the path before that move.
    `board-merge`: `src` moves to `dst`, replacing an older note there; `dst == src`: the old note `src` is
    removed, the newer note already at its target stays."""

    src: Path
    dst: Path
    role: str
    slug: str | None


class Blocked(NamedTuple):
    path: str
    reason: str


DATED = re.compile(r"(\d{8}) (.+)")
BAD = set("[]|#^")
RENAMES = {"answers": devloop.ANSWERS, "maps": devloop.MAPS, "security-audit": devloop.AUDITS,
           "dev-loop-board": devloop.BOARD}
SF_DATALESS = 0x40000000


def _nfc(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def _title(words: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in re.split(r"-+", words) if word)


def _slug(note: Path) -> str:
    return (devloop.frontmatter(note).get("feature") or devloop.slug_from_title(note.name)).lower()


def _placeholder(path: Path) -> bool:
    if path.name.startswith(".") and path.name.endswith(".icloud"):
        return True
    return bool(getattr(os.stat(path, follow_symlinks=False), "st_flags", 0) & SF_DATALESS)


def _names_spec(value: str, spec: Path) -> bool:
    wiki = re.fullmatch(r"\[\[([^\]|#^]+)[^\]]*\]\]", value)
    name = (wiki[1].rsplit("/", 1)[-1] if wiki else value).lower()
    return name in (spec.name.lower(), spec.stem.lower())


def _plan_hits(plan: Path, specs: list[tuple[str, Path]]) -> list[Path]:
    """The specs a plan names by the first D10 rule that names any: `spec:`, then `feature:`, then the stem."""
    fields = devloop.frontmatter(plan)
    rules = (
        lambda slug, spec: bool(fields.get("spec")) and _names_spec(fields["spec"], spec),
        lambda slug, spec: fields.get("feature", "").lower() == slug,
        lambda slug, spec: plan.stem in (spec.stem, spec.stem + " Plan"),
    )
    for rule in rules:
        hits = [spec for slug, spec in specs if rule(slug, spec)]
        if hits:
            return hits
    return []


class _Planner:
    def __init__(self, notes: Path) -> None:
        self.notes = notes
        self.moves: list[Move] = []
        self.blocked: list[Blocked] = []
        self.renames: list[tuple[Path, Path]] = []

    def block(self, path: Path | str, reason: str) -> None:
        item = Blocked(path if isinstance(path, str) else path.relative_to(self.notes).as_posix(), reason)
        if item not in self.blocked:
            self.blocked.append(item)

    def exact(self, path: Path) -> bool:
        """`path` exists with each part by its exact name: the macOS file system ignores case."""
        here = self.notes
        for part in path.relative_to(self.notes).parts:
            try:
                names = {_nfc(name) for name in os.listdir(here)}
            except OSError:
                return False
            if _nfc(part) not in names:
                return False
            here = here / part
        return True

    def now(self, path: Path) -> Path:
        """Where the final `path` is before the run, through the folder renames planned so far."""
        for src, dst in reversed(self.renames):
            if path == dst or dst in path.parents:
                return src / path.relative_to(dst)
        return path

    def case_clash(self, path: Path) -> Path | None:
        """The first folder of `path` that exists only under a name of another case: a move would land in it."""
        here = self.notes
        for part in path.relative_to(self.notes).parts:
            try:
                names = {_nfc(name) for name in os.listdir(here)}
            except OSError:
                return None
            if _nfc(part) not in names:
                return here / part if _nfc(part).lower() in {name.lower() for name in names} else None
            here = here / part
        return None

    def add(self, src: Path, dst: Path, role: str, slug: str | None) -> None:
        clash = self.case_clash(self.now(dst.parent))
        if clash is not None:
            self.block(clash, "collision")
        elif self.exact(self.now(dst)):
            self.block(dst, "target-exists")
        else:
            self.moves.append(Move(src, dst, role, slug))

    def kids(self, folder: Path, top: str | None) -> list[tuple[Path, str]]:
        """Each entry of `folder`, folders first, with its path relative to the renamed folder (D11)."""
        out = []
        for kid in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            name = kid.name
            run = re.fullmatch(r"run-(\d+)", name)
            if name == ".DS_Store":
                continue
            if top == "maps" and kid.is_dir():
                name = _title(name)
            elif top == "security-audit" and kid.is_dir() and run:
                name = f"Run {run[1]}"
            elif top == "dev-loop-board" and name == "features" and kid.is_dir():
                name = devloop.BOARD_FEATURES
            elif top == "dev-loop-board" and kid.suffix == ".md" and kid.is_file() and not _placeholder(kid) \
                    and devloop.frontmatter(kid).get("type") == "dev-loop-status":
                name = f"{devloop.BOARD_FEATURES}/{name}"
            out.append((kid, name))
        return out

    def place(self, src: Path, dst: Path, board: bool, top: str | None) -> None:
        """Move `src` to `dst`; into an existing `dst` folder entry by entry, the newer note winning in the board."""
        here = self.now(dst)
        if not self.exact(here):
            if not src.is_dir():
                self.moves.append(Move(src, dst, "board-merge" if board else "file", None))
                return
            self.moves.append(Move(src, dst, "folder", None))
            self.renames.append((src, dst))
            kids = [(kid, name) for kid, name in self.kids(src, top) if name != kid.name]
        elif src.is_dir() and here.is_dir():
            kids = self.kids(src, top)
        elif board and src.is_file() and here.is_file():
            newer = src.stat().st_mtime > here.stat().st_mtime
            self.moves.append(Move(src, dst if newer else src, "board-merge", None))
            return
        else:
            self.block(dst, "target-exists")
            return
        for kid, name in kids:
            if kid.is_dir() and name != kid.name and BAD & set(name):
                self.block(dst / name, "bad-name")
            else:
                self.place(kid, dst / name, board, None)

    def features(self, live: list[str], vault: Path | None, files: dict[str, Path]) -> list[str]:
        """The D10 moves of `specs/`, `plans/` and `framing/`; returns the ambiguous notes."""
        notes = self.notes

        def md(name: str) -> list[Path]:
            folder = notes / name
            if name not in live:
                return []
            return sorted(p for p in folder.iterdir() if p.suffix == ".md" and p.is_file() and not _placeholder(p))

        specs = [(_slug(spec), spec) for spec in md("specs")]
        paired: dict[Path, list[tuple[str, Path]]] = {spec: [] for _, spec in specs}
        orphans, ambiguous = [], []
        for role, name in (("plan", "plans"), ("framing", "framing")):
            for note in md(name):
                hits = _plan_hits(note, specs) if role == "plan" else [s for slug, s in specs if slug == _slug(note)]
                if len(hits) == 1:
                    paired[hits[0]].append((role, note))
                    continue
                if hits:
                    ambiguous.append(note.relative_to(notes).as_posix())
                orphans.append((role, note))
        for slug, spec in specs:
            dated = DATED.fullmatch(spec.stem)
            if not dated:
                self.block(spec, "no-date")
                continue
            plans = [note for role, note in paired[spec] if role == "plan"]
            framings = [note for role, note in paired[spec] if role == "framing"]
            dates = [found[1] for found in map(DATED.fullmatch, (f.stem for f in framings)) if found]
            folder = notes / devloop.FEATURES / f"{dates[0] if dates else dated[1]} {dated[2]}"
            if BAD & set(folder.name):
                self.block(folder, "bad-name")
                continue
            self.add(spec, folder / "Spec.md", "spec", slug)
            for plan in plans:
                self.add(plan, folder / ("Plan.md" if plan == plans[-1] else plan.name), "plan", slug)
            for framing in framings:
                self.add(framing, folder / "Framing.md", "framing", slug)
        for role, note in orphans:
            dated = DATED.fullmatch(note.stem)
            if not dated:
                self.block(note, "no-date")
                continue
            title = dated[2].removesuffix(" Plan") if role == "plan" else _title(dated[2])
            folder = notes / devloop.FEATURES / f"{dated[1]} {title}"
            if BAD & set(folder.name):
                self.block(folder, "bad-name")
                continue
            slug = (devloop.frontmatter(note).get("feature") or devloop.slug_from_title(folder.name)).lower()
            self.add(note, folder / f"{role.title()}.md", role, slug)
        self.linked_files(live, vault, files)
        return ambiguous

    def linked_files(self, live: list[str], vault: Path | None, files: dict[str, Path]) -> None:
        """Each other file of the three folders goes to the one feature folder whose moved notes link to it."""
        homes: dict[Path, set[tuple[Path, str | None]]] = {}
        for move in [m for m in self.moves if m.role in ("spec", "plan", "framing")]:
            for link in find_links(move.src.read_text(encoding="utf-8", errors="replace")):
                target = resolve(link, move.src, self.notes, vault, files)
                if target is not None:
                    homes.setdefault(target, set()).add((move.dst.parent, move.slug))
        for name in ("specs", "plans", "framing"):
            if name not in live:
                continue
            top = self.notes / name
            for root, dirs, names in os.walk(top):
                dirs.sort()
                for entry in sorted(names):
                    path = Path(root, entry)
                    if entry == ".DS_Store" or (path.parent == top and path.suffix == ".md"):
                        continue
                    if _placeholder(path):
                        self.block(path, "placeholder")
                    elif len(homes.get(path, ())) != 1:
                        self.block(path, "unknown-entry")
                    else:
                        [(folder, slug)] = homes[path]
                        self.add(path, folder / path.relative_to(top), "file", slug)


class Scan(NamedTuple):
    """One walk of the vault root (else the notes folder), dot folders skipped: `files` keyed for `links.resolve`,
    the placeholders, and the symbolic-link folders under the notes folder (the walk does not enter them)."""

    vault: Path | None
    files: dict[str, Path]
    placeholders: list[Path]
    linked: list[Path]


def scan(notes: Path) -> Scan:
    vault = next((parent for parent in notes.parents if (parent / ".obsidian").is_dir()), None)
    keep = {notes, *notes.parents}
    found = Scan(vault, {}, [], [])
    for root, dirs, names in os.walk(vault or notes):
        dirs[:] = [d for d in dirs if not d.startswith(".") or Path(root, d) in keep]
        found.linked.extend(Path(root, d) for d in dirs if Path(root, d).is_relative_to(notes)
                            and os.path.islink(Path(root, d)))
        for name in names:
            path = Path(root, name)
            if _placeholder(path):
                found.placeholders.append(path)
            found.files[key(path.relative_to(vault or notes).as_posix())] = path
    return found


def plan_moves(repo: Path, notes: Path, kit_version: str,
               found: Scan | None = None) -> tuple[list[Move], list[Blocked], list[str]]:
    """The moves of the notes migration, the items that block it and the ambiguous notes; changes nothing.
    `found`: the `scan` of `notes` when the caller already has it."""
    planner = _Planner(notes)
    vault, files, placeholders, _ = found or scan(notes)
    for path in placeholders:
        if path.is_relative_to(notes):
            planner.block(path, "placeholder")
    present = [name for name in devloop.old_layout(notes) if name in devloop.OLD_NAMES]
    live = []
    for name in present:
        top = notes / name
        if top.is_symlink():
            planner.block(top, "link")
            continue
        live.append(name)
        for root, dirs, names in os.walk(top):
            for entry in sorted(dirs + names):
                if os.path.islink(Path(root, entry)):
                    planner.block(Path(root, entry), "link")
    for old, new in RENAMES.items():
        if old in live:
            planner.place(notes / old, notes / new, old == "dev-loop-board", old)
    ambiguous = planner.features(live, vault, files)
    seen = set()
    for move in planner.moves:
        for path in (move.src, move.dst):
            for step in (path, *path.parents):
                if step == notes:
                    break
                if os.path.islink(planner.now(step)):
                    planner.block(planner.now(step), "link")
        key = _nfc(move.dst.relative_to(notes).as_posix()).lower()
        if key in seen:
            planner.block(move.dst, "collision")
        seen.add(key)
    _worktree_guards(planner, repo, notes, kit_version)
    _gitignore_guard(planner, notes, present)
    return planner.moves, planner.blocked, ambiguous


def _worktree_guards(planner: _Planner, repo: Path, notes: Path, kit_version: str) -> None:
    """D13 `old-kit-worktree` and `open-ledger` (D6) over every worktree of the repo."""
    plans = Path(os.path.realpath(notes / "plans"))
    listing = devloop.git(repo, "worktree", "list", "--porcelain") or ""
    for line in listing.splitlines():
        if not line.startswith("worktree "):
            continue
        tree = Path(line.removeprefix("worktree "))
        try:
            manifest = json.loads((tree / ".dev-loop" / "MANIFEST.json").read_text())
        except (OSError, ValueError):
            manifest = None
        version = manifest.get("version") if isinstance(manifest, dict) else None
        if version is not None and version_key(version) < version_key(kit_version):
            planner.block(str(tree), "old-kit-worktree")
        for progress in sorted((tree / ".superpowers" / "sdd").glob("*/progress.md")):
            try:
                named = (progress.parent / "plan-path").read_text().strip()
            except OSError:
                named = ""
            if not named or Path(os.path.realpath(tree / named)).is_relative_to(plans):
                planner.block(str(progress.parent), "open-ledger")


def _gitignore_guard(planner: _Planner, notes: Path, present: list[str]) -> None:
    """D14: an old folder the vault git repo ignores needs its new name ignored too."""
    top = devloop.git(notes, "rev-parse", "--show-toplevel")
    if not top:
        return
    base = Path(os.path.relpath(os.path.realpath(notes), os.path.realpath(top)))

    def ignored(name: str) -> bool:
        return devloop.git(Path(top), "check-ignore", "-q", "--no-index", f"{(base / name).as_posix()}/") is not None

    for old in ("security-audit", "dev-loop-board"):
        if old in present and ignored(old) and not ignored(RENAMES[old]):
            planner.block(f"{RENAMES[old]}/", "gitignore")

"""Find, resolve and rewrite the Obsidian links of a note, outside code (spec D15)."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote


class Link(NamedTuple):
    start: int
    end: int
    kind: str
    target: str
    suffix: str
    text: str | None
    form: str


WIKI = re.compile(r"!?\[\[([^\]]+?)\]\]")
MD = re.compile(r'\[([^\]]*)\]\((?:<([^>]*)>|([^)\s]+))(\s+"[^"]*")?\)')
FENCE = re.compile(r"(`{3,}|~{3,})")
CODE = re.compile(r"(`+).+?\1")


def code_ranges(text: str) -> list[tuple[int, int]]:
    ranges, fence, pos = [], "", 0
    for line in text.splitlines(keepends=True):
        mark = FENCE.match(line.lstrip("> \t"))
        run = mark.group(1) if mark else ""
        if fence or run:
            ranges.append((pos, pos + len(line)))
            if not fence:
                fence = run
            elif run.startswith(fence):
                fence = ""
        else:
            ranges += [(pos + m.start(), pos + m.end()) for m in CODE.finditer(line)]
        pos += len(line)
    return ranges


def _row(text: str, pos: int) -> bool:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end >= 0 else len(text)].lstrip("> \t").startswith("|")


def _split_suffix(target: str) -> tuple[str, str]:
    cut = min((i for i in (target.find("#"), target.find("^")) if i >= 0), default=len(target))
    return target[:cut], target[cut:]


def find_links(text: str) -> list[Link]:
    """Every wikilink and Markdown link of `text` outside code spans and fenced blocks, in text order."""
    code = code_ranges(text)
    found = []
    for m in WIKI.finditer(text):
        if any(a <= m.start() < b for a, b in code):
            continue
        inner = m.group(1)
        bar = inner.find("|")
        head, shown = (inner, None) if bar < 0 else (inner[:bar].removesuffix("\\"), inner[bar + 1:])
        target, suffix = _split_suffix(head)
        form = "relative" if target.startswith(("./", "../")) else "notes" if "/" in target else "name"
        found.append(Link(m.start(), m.end(), "wiki", target, suffix, shown, form))
    for m in MD.finditer(text):
        if any(a <= m.start() < b for a, b in code):
            continue
        dest = m.group(2) if m.group(2) is not None else m.group(3)
        target, _, anchor = dest.partition("#")
        form = "angle" if m.group(2) is not None else "encoded" if "%" in dest else "bare"
        found.append(Link(m.start(), m.end(), "md", target, "#" + anchor if anchor else "", m.group(1), form))
    return sorted(found)


def key(rel: str) -> str:
    rel = unicodedata.normalize("NFC", rel).lower()
    return rel[:-3] if rel.endswith(".md") else rel


def _lookup(path: Path, root: Path, files: dict[str, Path]) -> Path | None:
    try:
        return files.get(key(path.relative_to(root).as_posix()))
    except ValueError:
        return None


def _locate(link: Link, note: Path, notes: Path, vault: Path | None, files: dict[str, Path]) -> tuple[Path | None, bool]:
    root = vault or notes
    if link.kind == "md":
        dest = unquote(link.target)
        hit = _lookup(Path(os.path.normpath(note.parent / dest)), root, files)
        return (hit, True) if hit else (files.get(key(dest.lstrip("/"))), False)
    if link.form == "relative":
        return _lookup(Path(os.path.normpath(note.parent / link.target)), root, files), True
    want = key(link.target)
    if "/" in want:
        hits = [p for k, p in files.items() if k == want or k.endswith("/" + want)]
    else:
        hits = [p for k, p in files.items() if k.rsplit("/", 1)[-1] == want]
    return min(hits, key=lambda p: (p.parent.name != "specs", len(p.parts), str(p)), default=None), False


def resolve(link: Link, note: Path, notes: Path, vault: Path | None, files: dict[str, Path]) -> Path | None:
    """The file `link` in `note` points to, as Obsidian resolves it; `files` maps the NFC lower-case path from
    the vault root (else `notes`), without `.md`, to the real path. A name a spec and a plan share: the spec."""
    return _locate(link, note, notes, vault, files)[0]


def _rewritten(text: str, link: Link, note_old: Path, note_new: Path, moved: dict[Path, Path], notes: Path,
               vault: Path | None, files: dict[str, Path]) -> str:
    old = text[link.start:link.end]
    target, relative = _locate(link, note_old, notes, vault, files)
    if target is None:
        return old
    new_target = moved.get(target, target)
    if new_target == target and not (relative and note_new != note_old):
        return old
    if relative:
        path = Path(os.path.relpath(new_target, note_new.parent)).as_posix()
        if link.kind == "wiki" and not path.startswith("../") and ("/" in path or link.target.startswith("./")):
            path = "./" + path
    elif link.kind == "md":
        path = new_target.relative_to(vault or notes).as_posix()
    elif vault and key(link.target) == key(target.relative_to(vault).as_posix()):
        path = new_target.relative_to(vault).as_posix()
    else:
        path = new_target.relative_to(notes).as_posix()
    path = unicodedata.normalize("NFC", path)
    if path.endswith(".md") and not link.target.lower().endswith(".md"):
        path = path[:-3]
    if link.kind == "wiki":
        shown = link.text if link.text is not None or new_target == target else link.target + link.suffix
        sep = "\\|" if _row(text, link.start) or "\\|" in old else "|"
        bang = "!" if old.startswith("!") else ""
        return f"{bang}[[{path}{link.suffix}{'' if shown is None else sep + shown}]]"
    full = path + link.suffix
    if link.form == "encoded":
        dest = full.replace(" ", "%20")
    elif link.form == "angle" or " " in full:
        dest = f"<{full}>"
    else:
        dest = full
    title = MD.match(text, link.start).group(4) or ""
    return f"[{link.text}]({dest}{title})"


def rewrite(text: str, note_old: Path, note_new: Path, moved: dict[Path, Path], notes: Path, vault: Path | None,
            files: dict[str, Path]) -> tuple[str, int]:
    """`text` of a note moving from `note_old` to `note_new` with each link to a `moved` file, and each relative
    link of a moved note, rewritten in its own form; every other byte unchanged. Returns the text and the count."""
    out, count, pos = [], 0, 0
    for link in find_links(text):
        new = _rewritten(text, link, note_old, note_new, moved, notes, vault, files)
        count += new != text[link.start:link.end]
        out += [text[pos:link.start], new]
        pos = link.end
    out.append(text[pos:])
    return "".join(out), count

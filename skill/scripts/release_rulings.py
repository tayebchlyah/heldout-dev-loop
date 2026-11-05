"""The `## Deferred` parser of the project note: item and ruling lines, sections.

Public API: `release.py` imports it and re-exports it, `release_carry.py` reaches it through `release`. Imports only `re`.
"""

from __future__ import annotations

import re

ITEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \(security, (confirmed|needs-validation), ([A-Za-z0-9._-]+(?: [0-9]+)?)\): (.+)$")
RULING_RE = re.compile(r"^(?: {2}|\t)- Ruling (\d{4}-\d{2}-\d{2}): (\w+) +\S")
CLOSING_WORDS = ("accept", "close")
LEGACY_RE = re.compile(r"(?i)\brc\.\d+ only\b|\bfor rc\.\d+\b|\bbefore the final\b|\bnot (?:a ruling )?for the final\b")
SHAPE_RE = re.compile(r"(?i)^[ \t]+[-*+][\s*_+-]*ruling")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
FINGERPRINT_RE = re.compile(r"Fingerprint `([^`]+)`")
SECURITY_RE = re.compile(r"(?i)\bsecurity\b")
TAG_RE = re.compile(r"v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-rc\.[1-9]\d*)?)")
WAIVE_RE = re.compile(f"({TAG_RE.pattern}) +\\S")
CHECKBOX_RE = re.compile(r"^\[[ x]\] ")


def section(text: str, heading: str) -> list[str]:
    """The lines of the `## <heading>` section, up to the next `## ` heading. `###` subsections stay in."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip() == heading
        elif inside:
            out.append(line)
    return out


def deferred_items(text: str, tag: str | None = None) -> tuple[list[dict], list[str]]:
    """(every item in file order, unparsed lines) of `## Deferred`, closed for `tag` (a release tag `v` plus `TAG_RE`, or None).

    An item is `{line, verdict, source, fingerprint, closed, ruling}`. A ruling line is two spaces or one tab, `- Ruling <date>: <word> <reason>`. `closed`: its last ruling line in file order and its newest ruling by date (a date tie: the
    later line) both close it, so any other word opens it again (#216). Closing: `accept`, `close`, and `waive <tag> <reason>` whose tag equals `tag`; a waive of another tag, or any waive with `tag` None, does not close. `ruling`:
    `{date, word, carried, tag}` of its last ruling line (`carried`: an automatic `accept carried from the` ruling; `tag`: the waive tag, else None), else None. An `accept` or `close` whose reason names a release candidate
    (`rc.N only`, `for rc.N`, `before the final`, `not for the final`) is no ruling. Unparsed: a top-level bullet that names security and does not match the item format; under an item, an indented bullet that starts
    with `ruling` and is not a valid ruling (a waive with no tag or reason included), whatever the item state; a rc-scoped `accept` or `close` line while the item is not closed for `tag`; the opening line of a fence
    (three backticks or tildes) still open at the end of its item; after a column 0 line of an item that is not a bullet or a fence, every indented bullet that starts with `ruling`, valid or not, outside a fence (#262: Markdown
    shows it under the item, so it fails closed). Only a top-level `- ` line ends an item. A line (any indentation) whose stripped text starts with three backticks or tildes opens a fence under an item, an info string allowed. The fence closes only on a line of the same character, at least as long as the opener, with nothing after it but whitespace (CommonMark). A line inside a fence is never a ruling and never unparsed; only a top-level `- ` line ends the item then.
    """
    items: list[dict] = []
    unparsed: list[str] = []
    current: dict | None = None
    newest = ""
    newest_closes = False
    fence = ""
    mark = ""
    detached = False
    legacy: list[str] = []

    def flush() -> None:
        if fence:
            unparsed.append(fence)
        if current is not None and not current["closed"]:
            unparsed.extend(legacy)

    for line in section(text, "Deferred"):
        if current is not None and fence and not line.startswith("- "):
            stripped = line.strip()
            if stripped and stripped[0] == mark[0] and len(stripped) >= len(mark) and not stripped.strip(mark[0]):
                fence = ""
            continue
        if line.startswith("- "):
            flush()
            current = None
            newest = ""
            fence = ""
            detached = False
            legacy = []
            body = CHECKBOX_RE.sub("", line[2:])
            match = ITEM_RE.match(body)
            if match:
                fingerprint = FINGERPRINT_RE.search(match.group(4))
                current = {
                    "line": line.rstrip(),
                    "verdict": match.group(2),
                    "source": match.group(3),
                    "fingerprint": fingerprint.group(1) if fingerprint else None,
                    "closed": False,
                    "ruling": None,
                }
                items.append(current)
            elif SECURITY_RE.search(line):
                unparsed.append(line.rstrip())
        elif current is not None and FENCE_RE.match(line.strip()):
            fence = line.rstrip()
            mark = FENCE_RE.match(line.strip()).group(1)
        elif line.strip() and not line[0].isspace():
            detached = current is not None
        elif detached:
            if SHAPE_RE.match(line):
                unparsed.append(line.rstrip())
        elif current is not None:
            ruling = RULING_RE.match(line)
            word = ruling.group(2) if ruling else ""
            reason = line[ruling.end(2):].strip() if ruling else ""
            waive = WAIVE_RE.match(reason) if word == "waive" else None
            if ruling and word == "waive" and not waive:
                ruling = None
            if ruling and word in CLOSING_WORDS and LEGACY_RE.search(reason):
                legacy.append(line.rstrip())
            elif ruling:
                carried = line[ruling.start(2):].startswith("accept carried from the ")
                current["ruling"] = {"date": ruling.group(1), "word": word, "carried": carried, "tag": waive.group(1) if waive else None}
                closes = word in CLOSING_WORDS or (waive is not None and waive.group(1) == tag)
                if ruling.group(1) >= newest:
                    newest = ruling.group(1)
                    newest_closes = closes
                current["closed"] = closes and newest_closes
            elif SHAPE_RE.match(line):
                unparsed.append(line.rstrip())
    flush()
    return items, unparsed


def security_items(text: str, tag: str | None = None) -> tuple[list[dict], list[str]]:
    """(open items, unparsed lines) of `## Deferred`: the items of `deferred_items` that are not closed for `tag`."""
    items, unparsed = deferred_items(text, tag)
    return [i for i in items if not i["closed"]], unparsed

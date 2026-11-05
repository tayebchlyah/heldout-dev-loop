"""The `## Deferred` parser of the project note: item and ruling lines, sections.

Public API: `release.py` and `release_carry.py` import it. Imports only `re`.
"""

from __future__ import annotations

import re

ITEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \(security, (confirmed|needs-validation), ([A-Za-z0-9._-]+(?: [0-9]+)?)\): (.+)$")
RULING_RE = re.compile(r"^(?: {2,}|\t)\s*- Ruling (\d{4}-\d{2}-\d{2}): (\w+) +\S")
CLOSING_WORDS = ("accept", "close")
FINGERPRINT_RE = re.compile(r"Fingerprint `([^`]+)`")
SECURITY_RE = re.compile(r"(?i)\bsecurity\b")
TAG_RE = re.compile(r"v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-rc\.[1-9]\d*)?)")
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


def deferred_items(text: str) -> tuple[list[dict], list[str]]:
    """(every item in file order, unparsed lines) of `## Deferred`.

    An item is `{line, verdict, source, fingerprint, closed, ruling}`. `closed`: its last ruling line in file order and its newest ruling by date (a date tie: the later line)
    both say `accept` or `close`, so any other word opens it again (#216). `ruling`: `{date, word, carried}` of its last ruling line (`carried`: an automatic `accept carried from the` ruling), else None. A top-level bullet that names security and does
    not match the item format is unparsed.
    """
    items: list[dict] = []
    unparsed: list[str] = []
    current: dict | None = None
    newest = ""
    for line in section(text, "Deferred"):
        if line.startswith("- "):
            current = None
            newest = ""
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
        elif line.strip() and not line[0].isspace():
            current = None
        elif current is not None:
            ruling = RULING_RE.match(line)
            if ruling:
                carried = line[ruling.start(2):].startswith("accept carried from the ")
                current["ruling"] = {"date": ruling.group(1), "word": ruling.group(2), "carried": carried}
                if ruling.group(1) >= newest:
                    newest = ruling.group(1)
                    newest_closes = ruling.group(2) in CLOSING_WORDS
                current["closed"] = ruling.group(2) in CLOSING_WORDS and newest_closes
    return items, unparsed


def security_items(text: str) -> tuple[list[dict], list[str]]:
    """(open items, unparsed lines) of `## Deferred`: the items of `deferred_items` that are not closed."""
    items, unparsed = deferred_items(text)
    return [i for i in items if not i["closed"]], unparsed

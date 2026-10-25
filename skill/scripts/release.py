"""Release security audit: parse the project note (security items, rulings, audit records).

Shared by `devloop.py` (`release-plan`, `release-record`, `release-check`).
"""

from __future__ import annotations

import re
from pathlib import Path

ITEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \(security, (confirmed|needs-validation), ([A-Za-z0-9._-]+)\): (.+)$")
RULING_RE = re.compile(r"^(?: {2,}|\t)\s*- Ruling \d{4}-\d{2}-\d{2}: (\w+) ")
RECORD_RE = re.compile(
    r"^- (\d{4}-\d{2}-\d{2}) (run-\d+) at ([0-9a-f]{40}): confirmed \d+, needs validation \d+, hardening \d+, "
    r"profile \S+, scope (whole|[0-9a-f]{40}\.\.[0-9a-f]{40})$"
)
CLOSING_WORDS = ("accept", "close")
FINGERPRINT_RE = re.compile(r"Fingerprint `([^`]+)`")
SECURITY_RE = re.compile(r"(?i)\bsecurity\b")
CHECKBOX_RE = re.compile(r"^\[[ x]\] ")


def project_note(notes: Path) -> Path | None:
    """The project note: `Project Note.md`, else `<notes folder name>.md`, else None."""
    for path in (notes / "Project Note.md", notes / f"{notes.name}.md"):
        if path.is_file():
            return path
    return None


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


def security_items(text: str) -> tuple[list[dict], list[str]]:
    """(open items, unparsed lines) of `## Deferred`.

    An item is `{line, verdict, source, fingerprint}`. A `needs-validation` item with a ruling `accept` or `close`
    is closed and left out. A top-level bullet that names security and does not match the item format is unparsed.
    """
    items: list[dict] = []
    unparsed: list[str] = []
    current: dict | None = None
    closed = False

    def finish() -> None:
        if current is not None and not closed:
            items.append(current)

    for line in section(text, "Deferred"):
        if line.startswith("- "):
            finish()
            current, closed = None, False
            body = CHECKBOX_RE.sub("", line[2:])
            match = ITEM_RE.match(body)
            if match:
                fingerprint = FINGERPRINT_RE.search(match.group(4))
                current = {
                    "line": line.rstrip(),
                    "verdict": match.group(2),
                    "source": match.group(3),
                    "fingerprint": fingerprint.group(1) if fingerprint else None,
                }
            elif SECURITY_RE.search(line):
                unparsed.append(line.rstrip())
        elif line.strip() and not line[0].isspace():
            finish()
            current, closed = None, False
        elif current is not None and current["verdict"] == "needs-validation":
            ruling = RULING_RE.match(line)
            if ruling and ruling.group(1) in CLOSING_WORDS:
                closed = True
    finish()
    return items, unparsed


def current_record(text: str) -> dict | None:
    """`{line, run, sha, scope}` of the last record bullet of `## Release audits`, or None."""
    found = None
    for line in section(text, "Release audits"):
        match = RECORD_RE.match(line.rstrip())
        if match:
            found = {"line": line.rstrip(), "run": match.group(2), "sha": match.group(3), "scope": match.group(4)}
    return found

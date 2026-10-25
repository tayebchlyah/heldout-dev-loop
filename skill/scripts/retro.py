"""`devloop.py status` `review_due` and `devloop.py sessions`: the weekly review and the sessions retro reads.

D3 log rows, D4 due rule, D8 to D11 the session list.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

LOG = "Gardener Log.md"
DUE_DAYS = 7
CELL = re.compile(r"(?<!\\)\|")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def cells(line: str) -> list[str]:
    return [c.strip() for c in CELL.split(line.strip())[1:-1]]


def run_dates(text: str) -> list[date]:
    found: list[date] = []
    columns: list[str] | None = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            columns = None
            continue
        row = cells(line)
        if columns is None:
            if "Date" in row and "Verdict" in row and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("|"):
                columns = row
            continue
        values = dict(zip(columns, row))
        when = values.get("Date", "")
        if values.get("Verdict") == "run" and ISO_DATE.fullmatch(when):
            try:
                found.append(date.fromisoformat(when))
            except ValueError:
                continue
    return found


def review_due(repo: Path, notes: Path, today: date) -> dict | None:
    """`{"last", "days", "log"}` when the weekly review is due, else None (D3, D4)."""
    from devloop import frontmatter, specs  # lazy: devloop imports this module

    log = notes / LOG
    if not log.exists():
        if not any(frontmatter(path).get("shipped") for _, path in specs(notes)):
            return None
        dates = []
    else:
        try:
            dates = run_dates(log.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            dates = []
    if not dates:
        return {"last": None, "days": None, "log": str(log)}
    last = max(dates)
    days = (today - last).days
    return {"last": last.isoformat(), "days": days, "log": str(log)} if days >= DUE_DAYS else None

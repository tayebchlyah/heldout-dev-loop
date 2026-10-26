"""devloop.py review-merge and acceptance: the mechanical steps of the Stage 6 feature gate.

`review-merge` reads the reviewer tables (columns `file`, `line`, `finding`, `class`, `verdict`) and merges the
findings with the same file and line. `acceptance` runs the project acceptance command, reads its JUnit XML,
records the run and gives the progress guard verdict.
"""

from __future__ import annotations

import re
from pathlib import Path

COLUMNS = ("file", "line", "finding", "class", "verdict")
CLASSES = {"correctness", "security", "spec", "standards"}


class TableError(Exception):
    def __init__(self, message: str, row: int | None) -> None:
        super().__init__(message)
        self.message, self.row = message, row


def cells(line: str) -> list[str]:
    """The cells of one table line: `\\|` is a pipe, each cell stripped of spaces and of one pair of backticks."""
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    out = []
    for cell in re.split(r"(?<!\\)\|", line):
        cell = cell.replace("\\|", "|").strip()
        if len(cell) >= 2 and cell.startswith("`") and cell.endswith("`"):
            cell = cell[1:-1]
        out.append(cell)
    return out


def norm_file(cell: str, top: str | None) -> str:
    cell = re.sub(r"^.*?/devloop-second-review-[^/]*/", "", cell)
    if top and cell.startswith(top + "/"):
        cell = cell[len(top) + 1:]
    return cell[2:] if cell.startswith("./") else cell


def norm_line(cell: str) -> int | None:
    if cell.lower() in ("", "-", "n/a"):
        return None
    match = re.fullmatch(r"[Ll]?(\d+)(\s*-\s*\d+)?", cell)
    if match is None:
        raise ValueError(cell)
    return int(match.group(1))


def parse_table(text: str, top: str | None) -> list[dict]:
    """The findings of the first table whose header holds the five columns. Raises TableError."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        header = [c.lower() for c in cells(line)]
        if set(COLUMNS) <= set(header):
            break
    else:
        raise TableError("no review table with the columns file, line, finding, class, verdict", None)
    index = {name: header.index(name) for name in COLUMNS}
    body = lines[i + 1:]
    if body and re.fullmatch(r"[\s|:-]+", body[0]):
        body = body[1:]
    rows = []
    for n, line in enumerate(body, 1):
        if not line.strip().startswith("|"):
            break
        row = cells(line)
        if len(row) != len(header):
            raise TableError(f"cell count {len(row)}, header has {len(header)}", n)
        values = {name: row[index[name]] for name in COLUMNS}
        if not values["file"]:
            raise TableError("empty file", n)
        if not values["finding"]:
            raise TableError("empty finding", n)
        try:
            line_no = norm_line(values["line"])
        except ValueError:
            raise TableError(f"bad line: {values['line']}", n) from None
        kind = values["class"].lower()
        if kind not in CLASSES:
            raise TableError(f"bad class: {values['class']}", n)
        rows.append({"file": norm_file(values["file"], top), "line": line_no, "finding": values["finding"],
                     "class": kind, "verdict": values["verdict"]})
    return rows


def merge(tables: list[tuple[str, list[dict]]]) -> dict:
    """One row per file and line, sorted by file then line (no line first), numbered from 1."""
    rows: dict[tuple[str, int | None], dict] = {}
    for reviewer, findings in tables:
        for f in findings:
            row = rows.setdefault((f["file"], f["line"]), {"file": f["file"], "line": f["line"], "reviewers": [],
                                                           "findings": []})
            if reviewer not in row["reviewers"]:
                row["reviewers"].append(reviewer)
            row["findings"].append({"reviewer": reviewer, "finding": f["finding"], "class": f["class"],
                                    "verdict": f["verdict"]})
    ordered = [rows[key] for key in sorted(rows, key=lambda k: (k[0], k[1] is not None, k[1] or 0))]
    ordered = [{"n": n, **row} for n, row in enumerate(ordered, 1)]
    return {"rows": ordered, "counts": {reviewer: len(findings) for reviewer, findings in tables},
            "security": [row["n"] for row in ordered if any(f["class"] == "security" for f in row["findings"])]}


def review_merge(pairs: list[str], top: str | None) -> tuple[dict, int]:
    texts: list[tuple[str, str, str]] = []
    for pair in pairs:
        reviewer, sep, path = pair.partition("=")
        if not sep or not reviewer or not path:
            return {"error": f"not <reviewer>=<file>: {pair}"}, 2
        if any(reviewer == seen for seen, _, _ in texts):
            return {"error": f"reviewer given twice: {reviewer}"}, 2
        try:
            texts.append((reviewer, path, Path(path).read_text(errors="replace")))
        except OSError as exc:
            return {"error": f"cannot read {path}: {type(exc).__name__}"}, 2
    tables = []
    for reviewer, path, text in texts:
        try:
            tables.append((reviewer, parse_table(text, top)))
        except TableError as exc:
            return {"error": exc.message, "file": path, "row": exc.row}, 1
    return merge(tables), 0

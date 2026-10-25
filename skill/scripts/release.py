"""Release security audit: parse the project note (security items, rulings, audit records).

Shared by `devloop.py` (`release-plan`, `release-record`, `release-check`).
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
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


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def mismatch(notes: Path, record: dict) -> str | None:
    """Why the run folder of the record does not back it, else None."""
    path = notes / "security-audit" / record["run"] / "run-metadata.json"
    try:
        meta = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return f"{path.parent.name}/run-metadata.json: {type(exc).__name__}"
    source = meta.get("source_ref") if isinstance(meta, dict) else None
    commit = source.get("commit") if isinstance(source, dict) else None
    if commit != record["sha"]:
        return f"source_ref.commit is {commit}, the record says {record['sha']}"
    if meta.get("run_status") != "complete":
        return f"run_status is {meta.get('run_status')}, not complete"
    return None


def stale_paths(repo: Path, record_sha: str, target: str) -> list[str] | None:
    """Code paths changed since the audited commit (all paths when it is not an ancestor), None when none."""
    from devloop import is_test_or_doc  # lazy: devloop imports this module

    ancestor = run_git(repo, "merge-base", "--is-ancestor", record_sha, target).returncode == 0
    diff = run_git(repo, "diff", "--name-only", "-z", "--no-renames", record_sha, target)
    if diff.returncode != 0:  # the record commit is unknown here
        diff = run_git(repo, "ls-tree", "-r", "--name-only", "-z", target)
    paths = [p for p in diff.stdout.split("\0") if p]
    return paths if not ancestor else [p for p in paths if not is_test_or_doc(p)] or None


def check(repo: Path, notes: Path, target: str, tag: str | None) -> tuple[dict, int]:
    """The release gate: a current audit record and no open security item. Exit 0 when ok."""
    resolved = run_git(repo, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
    if resolved.returncode != 0:
        return {"error": f"cannot resolve {target}"}, 1
    sha = resolved.stdout.strip()
    if tag is not None and not fnmatch.fnmatchcase(tag, "v*"):
        return {"ok": True, "tag": tag, "target": sha, "record": None, "reasons": [], "skipped": "not-a-release-tag"}, 0
    note = project_note(notes)
    if note is None:
        return {"error": "no-project-note"}, 1
    try:
        text = note.read_text()
    except (OSError, ValueError) as exc:
        return {"error": f"cannot read {note.name}: {type(exc).__name__}"}, 1
    reasons: list[dict] = []
    record = current_record(text)
    if record is None:
        reasons.append({"reason": "no-audit"})
    elif (detail := mismatch(notes, record)) is not None:
        reasons.append({"reason": "record-mismatch", "detail": detail})
    elif record["sha"] != sha and (paths := stale_paths(repo, record["sha"], sha)):
        reasons.append({"reason": "stale-audit", "paths": paths})
    items, unparsed = security_items(text)
    if items:
        reasons.append({"reason": "open-security", "items": [i["line"] for i in items]})
    if unparsed:
        reasons.append({"reason": "unparsed-security", "lines": unparsed})
    ok = not reasons
    return {"ok": ok, "tag": tag, "target": sha, "record": record["line"] if record else None, "reasons": reasons}, 0 if ok else 1

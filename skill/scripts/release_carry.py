"""The `accept` carry of `release-record`: a `needs_validation` finding found again with unchanged trace files.

Used by `release.py` (`release-record`).
"""

from __future__ import annotations

from pathlib import Path

import release


def carry(repo: Path, text: str, fingerprint: str, trace: object, new_sha: str, today: str) -> tuple[str, dict, str | None] | None:
    """The carry of one `needs_validation` record against the project note `text`.

    None: the last `## Deferred` item of `fingerprint` has no last ruling `accept`, or there is none.
    `("carried", {fingerprint, from, ruling}, <ruling sub-bullet>)`: every trace file is the same at the record commit of
    the baseline item's run and at `new_sha`. The baseline is the nearest item at or before the last one whose last
    ruling is an `accept` that is not itself a carry, so a chain of carries keeps the commit of the human ruling. Else `("reopened", {fingerprint, why, files}, None)`, `why` `no-commit`,
    `bad-trace` or `changed`.
    """
    prior = [i for i in release.deferred_items(text)[0] if i["fingerprint"] == fingerprint]
    if not prior or prior[-1]["ruling"] is None or prior[-1]["ruling"]["word"] != "accept":
        return None
    item = next((i for i in reversed(prior) if i["ruling"] and i["ruling"]["word"] == "accept" and not i["ruling"]["carried"]), None)

    def reopened(why: str, files: list[str] | None = None) -> tuple[str, dict, None]:
        return "reopened", {"fingerprint": fingerprint, "why": why, "files": sorted(files or [])}, None

    top = Path(release.run_git(repo, "rev-parse", "--show-toplevel").stdout.strip() or repo)
    old = None
    for line in release.section(text, "Release audits"):
        match = release.RECORD_RE.match(line.rstrip())
        if item and match and match.group(2) == item["source"]:
            old = match.group(3)
    if item is None or old is None or release.run_git(top, "cat-file", "-e", f"{old}^{{commit}}").returncode != 0:
        return reopened("no-commit")
    files = trace_files(top, trace, new_sha)
    if files is None:
        return reopened("bad-trace")
    changed = [f for f in files if release.run_git(top, "--literal-pathspecs", "diff", "--quiet", old, new_sha, "--", f).returncode != 0]
    if changed:
        return reopened("changed", changed)
    ruling = item["ruling"]["date"]
    return "carried", {"fingerprint": fingerprint, "from": item["source"], "ruling": ruling}, (
        f"  - Ruling {today}: accept carried from the {ruling} ruling ({item['source']}): "
        f"fingerprint and trace files unchanged since {old[:7]}."
    )


def trace_files(top: Path, trace: object, sha: str) -> list[str] | None:
    if not isinstance(trace, list) or not trace:
        return None
    files: list[str] = []
    for entry in trace:
        file = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(file, str) or not file or file.startswith("/") or "\0" in file or ".." in file.split("/"):
            return None
        if file not in files:
            files.append(file)
    for file in files:
        try:
            listed = release.run_git(top, "--literal-pathspecs", "ls-tree", sha, "--", file)
        except UnicodeEncodeError:
            return None
        if listed.returncode != 0 or not listed.stdout.startswith(("100644 blob ", "100755 blob ")):
            return None
    return files

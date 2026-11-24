#!/usr/bin/env python3
"""Upstream watch: compare each pin of upstream.json with its latest release, keep one open issue.

Signal per source: `release` (the latest GitHub release tag), `plugin-version` (the `version` of a
plugin.json at the last commit that changed it), `tree` (the tree id of a folder at the head of the
default branch). Per new source: the diffs of watched files, the commits of the range that changed one,
and for `release` the notes of each release after the pin; upstream text goes in code, so it pings and
links nobody. One JSON object on stdout. Exit 1 with `error` when gh fails or a source fails.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import subprocess
import sys
from pathlib import Path

LABEL = "upstream-watch"
BODY_LIMIT = 65536  # GitHub refuses a longer issue body
NOTES_LIMIT = 3000
RELEASE_PAGE, RELEASE_PAGES = 20, 5  # allow-comment: dependency, GitHub answers HTTP 504 to 100 large release bodies.
KEEP_LEVELS = [{"diffs", "notes", "commits"}, {"notes", "commits"}, {"commits"}, set()]


class GhError(Exception):
    pass


def gh(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise GhError(f"gh {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def api(path: str):
    return json.loads(gh("api", path))


def latest(src: dict) -> tuple[str, str, str]:
    repo = src["repo"]
    if src["signal"] == "release":
        tag = api(f"repos/{repo}/releases/latest")["tag_name"]
        return src["pin"], tag, tag
    if src["signal"] == "plugin-version":
        sha = api(f"repos/{repo}/commits?path={src['file']}&per_page=1")[0]["sha"]
        content = api(f"repos/{repo}/contents/{src['file']}?ref={sha}")["content"]
        return src["version"], json.loads(base64.b64decode(content))["version"], sha
    sha = api(f"repos/{repo}/commits/HEAD")["sha"]
    parent, _, name = src["folder"].rpartition("/")
    tree = next((e["sha"] for e in api(f"repos/{repo}/contents/{parent}?ref={sha}") if e["name"] == name), None)
    if tree is None:
        raise GhError(f"no folder {src['folder']} at {sha}")
    return src["tree"], tree, sha


def blobs(repo: str, ref: str) -> dict[str, str]:
    data = api(f"repos/{repo}/git/trees/{ref}?recursive=1")
    if data["truncated"]:
        raise GhError(f"tree of {ref} is truncated")
    return {e["path"]: e["sha"] for e in data["tree"] if e["type"] == "blob"}


def changed(src: dict, head: str) -> list[tuple[str, str]]:
    before, after = blobs(src["repo"], src["pin"]), blobs(src["repo"], head)
    return [(path, "added" if path not in before else "removed" if path not in after else "modified")
            for path in sorted(before.keys() | after.keys())
            if watched(path, src["paths"]) and before.get(path) != after.get(path)]


def watched(path: str | None, paths: list[str]) -> bool:
    return path is not None and any(path == p or (p.endswith("/") and path.startswith(p)) for p in paths)


def commits(src: dict, head: str, cmp: dict, files: list[tuple[str, str]]) -> list[tuple[str, str]]:
    subject = {}
    for path, _ in files:
        for item in api(f"repos/{src['repo']}/commits?path={path}&sha={head}&per_page=100"):
            subject[item["sha"]] = (item["commit"]["message"].splitlines() or [""])[0]
    return [(c["sha"], subject[c["sha"]]) for c in cmp["commits"] if c["sha"] in subject]


def releases(src: dict) -> tuple[list[dict], bool]:
    after = []
    for page in range(1, RELEASE_PAGES + 1):
        listing = api(f"repos/{src['repo']}/releases?per_page={RELEASE_PAGE}&page={page}")
        for item in listing:
            if item["tag_name"] == src["pin"]:
                return after, True
            if not (item["draft"] or item["prerelease"]):
                after.append(item)
        if len(listing) < RELEASE_PAGE:
            break
    return after, False


def ticks(text: str, least: int) -> str:
    return "`" * max([least] + [len(run) + 1 for run in re.findall(r"`+", text)])


def span(text: str) -> str:
    return f"{ticks(text, 1)} {text} {ticks(text, 1)}"


def notes_block(item: dict, keep: set[str]) -> list[str]:
    if "notes" not in keep:
        return [f"- {item['tag_name']}: {item['html_url']}"]
    text = (item["body"] or "").replace("\r\n", "\n")
    if len(text) > NOTES_LIMIT:
        text = text[:NOTES_LIMIT] + f"\n[cut at {NOTES_LIMIT} characters, full notes at the link]"
    fence = ticks(text, 3)
    return [f"<details><summary>{html.escape(item['tag_name'])}</summary>", "", item["html_url"], "",
            f"{fence}text", text, fence, "", "</details>"]


def section(src: dict, old: str, new: str, cmp: dict, files: list[tuple[str, str]], log: list[tuple[str, str]],
            notes: tuple[list[dict], bool] | None, keep: set[str]) -> str:
    lines = [f"## {src['name']}: {old} to {new}", "", f"`{src['repo']}`, compare: {cmp['html_url']}", ""]
    for part, text in (("diffs", "Diffs"), ("notes", "Release note bodies"), ("commits", "Commits")):
        if part not in keep:
            lines += [f"{text} left out: the issue body went over the GitHub limit, see the links.", ""]
    if not files:
        lines += ["No watched file changed.", ""]
    patch_of = {f["filename"]: f.get("patch") for f in cmp["files"]}
    for path, status in files:
        patch = patch_of.get(path)
        lines.append(f"- `{path}` ({status})" + ("" if patch else ", diff on the compare page"))
        if "diffs" in keep and patch:
            fence = ticks(patch, 3)
            lines += ["", f"{fence}diff", patch, fence]
        lines.append("")
    if "commits" in keep and log:
        lines += ["Commits that changed a watched file:", ""]
        if cmp["total_commits"] > len(cmp["commits"]):
            lines += [f"Only the first {len(cmp['commits'])} of {cmp['total_commits']} commits of the range are "
                      "checked: see the compare page.", ""]
        lines += [f"- [`{sha[:7]}`](https://github.com/{src['repo']}/commit/{sha}) {span(text)}" for sha, text in log]
        lines.append("")
    if notes is not None:
        after, found = notes
        lines += ["Releases after the pin:", ""]
        if not found:
            lines += ["The pin is not among the 100 newest releases: the list may be incomplete.", ""]
        for item in after:
            lines += notes_block(item, keep)
            if "notes" in keep:
                lines.append("")
        lines.append("")
    return "\n".join(lines)


def render(news: list[tuple], keep: set[str]) -> str:
    head = ("Sources with a new release since their pin in `upstream.json`. For each: upgrade, re-copy, "
            "or ignore (set `ignored` to the new value).\n\n")
    return head + "\n".join(section(*item, keep) for item in news)


def write_issue(title: str, body: str) -> tuple[str, int]:
    issues = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open", "--json", "number", "--limit", "1"))
    if issues:
        number = issues[0]["number"]
        gh("issue", "edit", str(number), "--title", title, "--body-file", "-", stdin=body)
        return "edited", number
    gh("label", "create", LABEL, "--force", "--color", "c5def5", "--description", "A new upstream release to review")
    url = gh("issue", "create", "--title", title, "--label", LABEL, "--body-file", "-", stdin=body)
    number = re.search(r"/(\d+)\s*$", url)
    if number is None:
        raise GhError(f"gh issue create: no issue number in {url.strip()!r}")
    return "created", int(number.group(1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare each upstream pin with its latest release.")
    parser.add_argument("--sources", type=Path, default=Path(__file__).resolve().parents[1] / "upstream.json")
    parser.add_argument("--dry-run", action="store_true", help="print the issue body, write no issue")
    args = parser.parse_args(argv)
    rows, news, errors = [], [], []
    for src in json.loads(args.sources.read_text())["sources"]:
        row = {"name": src["name"], "state": "current", "from": None, "to": None}
        rows.append(row)
        try:
            old, new, head = latest(src)
            row.update({"from": old, "to": new})
            row["state"] = "current" if new == old else "ignored" if new == src.get("ignored") else "new"
            if row["state"] == "new":
                cmp = api(f"repos/{src['repo']}/compare/{src['pin']}...{head}")
                files = changed(src, head)
                log = commits(src, head, cmp, files)
                notes = releases(src) if src["signal"] == "release" else None
                row["compare"] = cmp["html_url"]
                news.append((src, old, new, cmp, files, log, notes))
        except (GhError, KeyError, IndexError, ValueError, TypeError) as exc:
            row.update({"state": "error", "error": str(exc)})
            errors.append(f"{src['name']}: {exc}")
    body = next(text for keep in KEEP_LEVELS if len(text := render(news, keep)) < BODY_LIMIT or not keep)
    out = {"sources": rows, "action": "none", "issue": None}
    if args.dry_run:
        out.update({"action": "dry-run", "body": body})
    elif news:
        try:
            out["action"], out["issue"] = write_issue(f"Upstream watch: {', '.join(s['name'] for s, *_ in news)}", body)
        except GhError as exc:
            errors.append(str(exc))
    if errors:
        out["error"] = "; ".join(errors)
    print(json.dumps(out))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

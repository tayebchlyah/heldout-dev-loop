#!/usr/bin/env python3
"""Upstream watch: compare each pin of upstream.json with its latest release, keep one open issue.

Signal per source: `release` (the latest GitHub release tag), `plugin-version` (the `version` of a
plugin.json at the last commit that changed it), `tree` (the tree id of a folder at the head of the
default branch). One JSON object on stdout. Exit 1 with `error` when gh fails or a source fails.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

LABEL = "upstream-watch"
BODY_LIMIT = 65536  # GitHub refuses a longer issue body


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


def section(src: dict, old: str, new: str, cmp: dict, files: list[tuple[str, str]], patches: bool) -> str:
    lines = [f"## {src['name']}: {old} to {new}", "", f"`{src['repo']}`, compare: {cmp['html_url']}", ""]
    if not patches:
        lines += ["Diffs left out: the issue body went over the GitHub limit, see the compare page.", ""]
    if not files:
        lines += ["No watched file changed.", ""]
    patch_of = {f["filename"]: f.get("patch") for f in cmp["files"]}
    for path, status in files:
        patch = patch_of.get(path)
        lines.append(f"- `{path}` ({status})" + ("" if patch else ", diff on the compare page"))
        if patches and patch:
            fence = "`" * max([3] + [len(run) + 1 for run in re.findall(r"`+", patch)])
            lines += ["", f"{fence}diff", patch, fence]
        lines.append("")
    return "\n".join(lines)


def render(news: list[tuple], patches: bool) -> str:
    head = ("Sources with a new release since their pin in `upstream.json`. For each: upgrade, re-copy, "
            "or ignore (set `ignored` to the new value).\n\n")
    return head + "\n".join(section(*item, patches) for item in news)


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
                row["compare"] = cmp["html_url"]
                news.append((src, old, new, cmp, files))
        except (GhError, KeyError, IndexError, ValueError, TypeError) as exc:
            row.update({"state": "error", "error": str(exc)})
            errors.append(f"{src['name']}: {exc}")
    body = render(news, True)
    if len(body) >= BODY_LIMIT:
        body = render(news, False)
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

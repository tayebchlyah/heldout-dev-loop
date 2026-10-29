"""devloop.py capture: a before and an after screen of each driven route in the PR body, through `gh pr edit --attach`.

Stdlib only. gh uploads each attached file to GitHub `user-attachments` and replaces its Markdown reference in the body.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from devloop import emit, git
from seal import git_dir
from verify import GH_LINE, gh_too_old, gh_version, read_config

START = "<!-- dev-loop: captures -->"
END = "<!-- /dev-loop: captures -->"
MAX_BYTES = 10485760
MAX_FILES = 50
AFTER_MODES = ("diff", "since", "all")
TAIL_LINES = 20


def read_drive(path: Path) -> dict | None:
    """A drive.json: a JSON object with a `routes` list, its routes kept when they name an entry and a path."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("routes"), list):
        return None
    routes = [r for r in data["routes"]
              if isinstance(r, dict) and isinstance(r.get("entry"), str) and isinstance(r.get("path"), str)]
    return {**data, "routes": routes}


def by_entry(routes: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for route in routes:
        groups.setdefault(route["entry"], []).append(route)
    return groups


CAPTURE_NAME = re.compile(r"-capture(-\d+)?$")


def new_capture_folder(evidence: Path, stamp: str) -> Path:
    """`<stamp>-capture`, else `-capture-2`, `-capture-3`: a taken name is never reused, so a recapture in the
    same second keeps the first one's files."""
    evidence.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        folder = evidence / (f"{stamp}-capture" if n == 1 else f"{stamp}-capture-{n}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            n += 1


def after_set(evidence: Path) -> dict | None:
    """The newest `diff` drive gives the entries; each newer `since` or `all` drive replaces the routes of
    the entries of that set it ran, and never adds one. None when there is no `diff` drive."""
    drives = []
    for folder in sorted(evidence.iterdir()) if evidence.is_dir() else []:
        drive = read_drive(folder / "drive.json") if folder.is_dir() and not CAPTURE_NAME.search(folder.name) else None
        if drive and drive.get("mode") in AFTER_MODES:
            drives.append((folder, drive))
    bases = [i for i, (_, drive) in enumerate(drives) if drive["mode"] == "diff"]
    if not bases:
        return None
    folder, drive = drives[bases[-1]]
    groups = by_entry(drive["routes"])
    for folder, drive in drives[bases[-1] + 1:]:
        groups.update({entry: routes for entry, routes in by_entry(drive["routes"]).items() if entry in groups})
    return {"routes": [r for routes in groups.values() for r in routes], "head": drive.get("head"),
            "folder": str(folder)}


def file_of(value: object) -> Path | None:
    return Path(value) if isinstance(value, str) and value and Path(value).is_file() else None


def copy(src: Path, dst: Path) -> Path:
    if src.resolve() != dst.resolve():
        shutil.copyfile(src, dst)
    return dst


def safe(entry: str) -> str:
    """An entry name usable in a file name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", entry)


def short(head: object) -> str:
    return f"`{head[:7]}`" if isinstance(head, str) and head else "none"


def merge_body(body: str, section: str) -> str:
    """The section in place of an existing one, the rest kept; else appended after one blank line."""
    end = body.find(END)
    start = body.rfind(START, 0, end) if end != -1 else -1
    if start != -1:
        return body[:start] + section.rstrip("\n") + body[end + len(END):]
    return (body.rstrip("\n") + "\n\n" if body.strip() else "") + section


def tail(text: str) -> str:
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def write_after(repo: Path, target: str) -> tuple[dict, int]:
    """The after set of the evidence folder, written to `target` as a drive.json for `drive --routes-from`."""
    if not isinstance(read_config(repo).get("verify"), dict):
        return {"ok": True, "skipped": "no-verify"}, 0
    evidence = git_dir(repo) / "dev-loop" / "evidence"
    after = after_set(evidence)
    if after is None:
        return {"error": f"no diff drive in {evidence}: run drive first"}, 1
    path = Path(target).absolute()
    path.write_text(json.dumps({**after, "mode": "diff"}, indent=2) + "\n")
    return {"ok": True, "after": str(path), "routes": len(after["routes"]), "head": after["head"]}, 0


def home_short(folder: str) -> str:
    """The folder with the home folder prefix as `~`."""
    home = str(Path.home())
    return "~" + folder[len(home):] if folder == home or folder.startswith(home + "/") else folder


def run(repo: Path, pr: int, before_path: str | None, after_path: str | None) -> tuple[dict, int]:
    if not isinstance(read_config(repo).get("verify"), dict):
        return {"ok": True, "skipped": "no-verify"}, 0
    found = gh_version()
    if gh_too_old(found):
        return {"error": GH_LINE.format(found or "none")}, 1
    before = None
    if before_path:
        before = read_drive(Path(before_path))
        if before is None:
            return {"error": f"--before {before_path} is not a JSON object with a routes list"}, 1
    evidence = git_dir(repo) / "dev-loop" / "evidence"
    if after_path:
        drive = read_drive(Path(after_path))
        if drive is None:
            return {"error": f"--after {after_path} is not a JSON object with a routes list"}, 1
        after = {"routes": drive["routes"], "head": drive.get("head"), "folder": drive["folder"] if isinstance(drive.get("folder"), str)
                 else str(Path(after_path).resolve().parent)}
    else:
        after = after_set(evidence)
        if after is None:
            return {"error": f"no diff drive in {evidence}: run drive first"}, 1
    # An entry stays when one route is ok; its route position k counts every route of the entry.
    groups = {e: rs for e, rs in by_entry(sorted(after["routes"], key=lambda r: r["entry"])).items()
              if any(r.get("ok") is True for r in rs)}
    if not groups:
        return {"ok": True, "skipped": "no-routes"}, 0

    folder = new_capture_folder(evidence, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    # Every before file is kept, so a recapture finds them after the before worktree is gone.
    kept, before_shot, before_video = [], {}, {}
    for entry, routes in by_entry(before["routes"] if before else []).items():
        for k, route in enumerate(routes, 1):
            shot = file_of(route.get("screenshot"))
            if shot:
                shot = copy(shot, folder / f"before-{safe(entry)}-{k}.jpg")
                if isinstance(route.get("status"), int) and route["status"] < 400:
                    before_shot.setdefault((entry, route["path"]), shot)
            video = file_of(route.get("video"))
            if video and entry not in before_video:
                before_video[entry] = copy(video, folder / f"before-{safe(entry)}.webm")
            kept.append({**route, "screenshot": str(shot) if shot else None,
                         "video": str(before_video[entry]) if video else None})

    blocks = [f"{START}\n## Screens", f"Before: {short(before.get('head')) if before else 'none'}, "
                                      f"after: {short(after['head'])}."]
    files: list[Path] = []
    count = omitted = 0
    full = False

    def fits(need: list[Path]) -> bool:
        nonlocal full
        full = full or len(files) + len(set(need) - set(files)) > MAX_FILES
        return not full

    def add(need: list[Path]) -> None:
        files.extend(f for f in need if f not in files)

    for entry, routes in groups.items():
        name = safe(entry)
        ok_count = sum(r.get("ok") is True for r in routes)
        clip = next((v for v in (file_of(r.get("video")) for r in routes) if v), None)
        if clip and clip.stat().st_size <= MAX_BYTES:
            if not fits(need := [f for f in (before_video.get(entry),) if f and f.stat().st_size <= MAX_BYTES]
                        + [folder / f"after-{name}.webm"]):
                omitted += ok_count
                continue
            copy(clip, folder / f"after-{name}.webm")
            add(need)
            count += ok_count
            shown = f"![](./{need[0].name})" if len(need) == 2 else "new"
            blocks.append(f"#### {entry} (journey)\n\nBefore\n\n{shown}\n\nAfter\n\n![](./after-{name}.webm)")
            continue
        notes, rows = (["Video over 10 MB, screenshots shown."] if clip else []), []
        for k, route in enumerate(routes, 1):
            if route.get("ok") is not True:
                continue
            if full:
                omitted += 1
                continue
            cells, need = [], []
            for side, shot in (("before", before_shot.get((entry, route["path"]))),
                               ("after", file_of(route.get("screenshot")))):
                if shot is None:
                    cells.append("new" if side == "before" else "none")
                elif shot.stat().st_size > MAX_BYTES:
                    cells.append("over 10 MB")
                    notes += [] if "Image over 10 MB, see the evidence." in notes else ["Image over 10 MB, see the evidence."]
                else:
                    target = shot if side == "before" else folder / f"after-{name}-{k}.jpg"
                    cells.append(f"![{side} {entry} {route['path']}](./{target.name})")
                    need.append((shot, target))
            if not fits([target for _, target in need]):
                omitted += 1
                continue
            for shot, target in need:
                copy(shot, target)
            add([target for _, target in need])
            count += 1
            rows.append(f"| `{route['path']}` | {cells[0]} | {cells[1]} |")
        if rows:
            blocks.append("\n\n".join([f"#### {entry}", *notes,
                                       "| Route | Before | After |\n| --- | --- | --- |\n" + "\n".join(rows)]))
    if omitted:
        blocks.append(f"{omitted} more routes in the evidence: {home_short(after['folder'])}")
    section = "\n\n".join(blocks) + f"\n{END}\n"

    view = subprocess.run(["gh", "pr", "view", str(pr), "--json", "body", "--jq", ".body"], cwd=repo,
                          capture_output=True, text=True)
    if view.returncode != 0:
        return {"error": "gh pr view failed", "tail": tail(view.stdout + view.stderr)}, 1
    body_md = folder / "body.md"
    body_md.write_text(merge_body(view.stdout.removesuffix("\n"), section))
    before_json = folder / "before.json"
    before_json.write_text(json.dumps({**before, "routes": kept} if before else {"routes": []}, indent=2) + "\n")
    attach = [arg for f in files for arg in ("--attach", str(f))]
    edit = subprocess.run(["gh", "pr", "edit", str(pr), "--body-file", str(body_md), *attach], cwd=repo,
                          capture_output=True, text=True)
    if edit.returncode != 0:
        return {"error": "gh pr edit failed", "tail": tail(edit.stdout + edit.stderr)}, 1
    return {"ok": True, "pr": pr, "files": [str(f) for f in files], "routes": count, "omitted": omitted,
            "body": str(body_md), "before": str(before_json)}, 0


def cmd_capture(args: argparse.Namespace) -> int:
    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top is None:
        return emit({"error": "not a git repository"}, 1)
    if args.write_after:
        return emit(*write_after(Path(top), args.write_after))
    payload, code = run(Path(top), args.pr, args.before, args.after)
    return emit(payload, code)

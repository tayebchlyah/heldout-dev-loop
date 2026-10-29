"""Verify skill rules: the `verify` key of dev-loop.json, the feature map entries, and the checks of check_setup.py.

Stdlib only: a map entry's frontmatter holds one line of JSON per key (valid YAML), parsed with `json`.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from devloop import emit, git

FILL = "<!-- dev-loop: fill -->"
SECTIONS = ("Launch", "Doctor", "Drive", "Evidence", "Cleanup")
MISSING = ('no verify skill for the ui stack: run devloop.py verify-init --base-url <url>, '
           'or set "verify": false in dev-loop.json')
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
GH_MIN = (2, 99, 0)
GH_LINE = "gh 2.99.0 or later needed for PR captures (gh pr edit --attach), found {}"


def url_ok(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def relative_ok(path: object) -> bool:
    """A non-empty repo-relative path that stays in the repo."""
    if not isinstance(path, str) or not path.strip():
        return False
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts


def config_errors(verify: object) -> list[str]:
    """The shape of the `verify` value: `false`, or an object with `skill`, `base_url`, optional `setup` and
    `storage_state`."""
    if verify is False:
        return []
    if not isinstance(verify, dict):
        return ["verify in dev-loop.json must be false or an object"]
    errors = []
    if not isinstance(verify.get("skill"), str) or not SKILL_NAME.match(verify["skill"]):
        errors.append("verify.skill in dev-loop.json must be lower case words joined by -")
    if not url_ok(verify.get("base_url")):
        errors.append("verify.base_url in dev-loop.json must be an http:// or https:// URL")
    setup = verify.get("setup", [])
    if not isinstance(setup, list) or not all(isinstance(c, str) and c.strip() for c in setup):
        errors.append("verify.setup in dev-loop.json must be a list of non-empty strings")
    if "storage_state" in verify and not relative_ok(verify["storage_state"]):
        errors.append("verify.storage_state in dev-loop.json must be a repo-relative path")
    return errors


def frontmatter(text: str) -> dict[str, str] | None:
    """The raw `key: value` lines between the first `---` line and the next, or None when there is none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return None


def routes_error(routes: object) -> str | None:
    if not isinstance(routes, list) or not routes:
        return "routes must be a non-empty list"
    for route in routes:
        if not isinstance(route, dict) or not isinstance(route.get("path"), str) or not route["path"].startswith("/"):
            return "each route needs a path that starts with /"
        names = route.get("names", [])
        if not isinstance(names, list) or not all(
                isinstance(n, dict) and all(isinstance(n.get(k), str) and n[k].strip() for k in ("role", "name"))
                for n in names):
            return "names must be a list of objects with a non-empty role and name"
    return None


def load_entry(repo: Path, path: Path) -> tuple[dict | None, str | None]:
    """A map entry's `routes`, `paths` and `script`, or None and the reason it breaks the entry shape."""
    try:
        fields = frontmatter(path.read_text())
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"unreadable: {exc}"
    if fields is None:
        return None, "no frontmatter"
    values = {}
    for key in ("routes", "paths", "script"):
        if key in fields:
            try:
                values[key] = json.loads(fields[key])
            except ValueError:
                return None, f"{key} is not one line of JSON"
    if error := routes_error(values.get("routes")):
        return None, error
    paths = values.get("paths")
    if not isinstance(paths, list) or not paths or not all(
            isinstance(p, str) and p.strip() and not p.endswith("/") for p in paths):
        return None, "paths must be a non-empty list of patterns, none empty or ending in /"
    script = values.get("script")
    if script is not None:
        if not relative_ok(script):
            return None, "script must be a repo-relative path"
        if not (repo / script).is_file():
            return None, f"script not found: {script}"
    routes = [{**route, "names": route.get("names", [])} for route in values["routes"]]
    return {"routes": routes, "paths": paths, "script": script}, None


def skill_dir(skill: str) -> str:
    return f".agents/skills/{skill}"


def entries(repo: Path, skill: str) -> list[Path]:
    """The map entries: `*.md` directly under `features/`, the README index left out."""
    folder = repo / skill_dir(skill) / "features"
    return sorted(p for p in folder.glob("*.md") if p.name != "README.md") if folder.is_dir() else []


def read_config(repo: Path) -> dict:
    try:
        config = json.loads((repo / "dev-loop.json").read_text())
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def stacks(repo: Path) -> set[str]:
    try:
        match = re.search(r"^stacks:\s*(.+)$", (repo / "AGENTS.md").read_text(), re.MULTILINE)
    except OSError:
        return set()
    return {s.strip() for s in match.group(1).split(",")} if match else set()


def check(repo: Path) -> list[str]:
    """The check_setup.py lines for the verify skill. dev-loop.json errors themselves are check_project_config's."""
    config = read_config(repo)
    if "verify" not in config:
        return [MISSING] if "ui" in stacks(repo) else []
    verify = config["verify"]
    if errors := config_errors(verify):
        return errors
    if verify is False:
        return []
    skill = verify["skill"]
    base = skill_dir(skill)
    skill_md = repo / base / "SKILL.md"
    if not skill_md.is_file():
        return [f"missing {base}/SKILL.md (verify.skill)"]
    failures = []
    text = skill_md.read_text()
    if (frontmatter(text) or {}).get("name") != skill:
        failures.append(f"{base}/SKILL.md lacks frontmatter name: {skill}")
    failures += [f"{base}/SKILL.md lacks the H2 section {s}" for s in SECTIONS
                 if not re.search(rf"^## {s}\s*$", text, re.MULTILINE)]
    features = repo / base / "features"
    marked = [skill_md] + sorted(p for p in features.rglob("*") if p.is_file()) if features.is_dir() else [skill_md]
    for path in marked:
        try:
            if FILL in path.read_text():
                failures.append(f"fill marker {FILL} in {path.relative_to(repo).as_posix()}")
        except (OSError, UnicodeDecodeError):
            continue
    found = entries(repo, skill)
    if not found:
        failures.append(f"no feature map entry under {base}/features/")
    for path in found:
        _, reason = load_entry(repo, path)
        if reason:
            failures.append(f"bad feature map entry {path.relative_to(repo).as_posix()}: {reason}")
    harnesses = config.get("harnesses", [])
    link = repo / ".claude" / "skills" / skill
    if isinstance(harnesses, list) and "claude" in harnesses and not (
            link.exists() and link.resolve() == (repo / base).resolve()):
        failures.append(f".claude/skills/{skill} must link to ../../{base}")
    return failures


def gh_version() -> str | None:
    """The `X.Y.Z` that `gh --version` prints, or None: no gh, or no version in its output."""
    try:
        out = subprocess.run(["gh", "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+\.\d+", out)
    return match.group() if match else None


def gh_too_old(found: str | None) -> bool:
    return found is None or tuple(map(int, found.split("."))) < GH_MIN


def gh_line(repo: Path) -> list[str]:
    """The check_setup.py line for `capture`: only when `verify` is an object."""
    if not isinstance(read_config(repo).get("verify"), dict):
        return []
    found = gh_version()
    return [GH_LINE.format(found or "none")] if gh_too_old(found) else []


SKILL_TEMPLATE = """---
name: {skill}
description: Launch, check, drive and clean up this project's app, as a user does. {fill}
---

# {skill}

## Launch
The exact command that starts the app for verification, and how to tell it is ready (usable, not only up). {fill}

## Doctor
One read-only check that this instance is worth driving: process up, right build, port ours, auth valid. {fill}

## Drive
`python3 .agents/skills/dev-loop/scripts/devloop.py drive`, and the project CLI commands an agent uses to reach a state. {fill}

## Evidence
What a proof captures and where it stays: `drive` writes `<git-dir>/dev-loop/evidence/<run>/`. {fill}

## Cleanup
How to stop what Launch started, never by process name. Cleanup keeps the evidence. {fill}
"""

FEATURES_TEMPLATE = """# Feature map

One file per user-facing feature: frontmatter `routes`, `paths`, optional `script` (one line of JSON each), then
`## Sub-features`, `## How to get to it (user POV)`, `## Driving it with <harness>`, `## Gotchas`. The dev-loop skill's
`references/verify.md` has the contract.

{fill}
"""


def cmd_verify_init(args: argparse.Namespace) -> int:
    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top is None:
        return emit({"error": "not a git repository"}, 1)
    repo = Path(top)
    try:
        config = json.loads((repo / "dev-loop.json").read_text())
    except (OSError, ValueError):
        return emit({"error": f"no readable dev-loop.json in {repo}"}, 1)
    if not isinstance(config, dict):
        return emit({"error": "dev-loop.json must hold a JSON object"}, 1)
    if "verify" in config:
        return emit({"error": "dev-loop.json already has a verify key"}, 1)
    skill = args.name
    if skill is None:
        common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        main = Path(common).parent if common else repo
        skill = "verify-" + re.sub(r"[^a-z0-9]+", "-", main.name.lower()).strip("-")
    if not SKILL_NAME.match(skill):
        return emit({"error": f"skill name must be lower case words joined by -: {skill}"}, 1)
    if not url_ok(args.base_url):
        return emit({"error": f"base URL must be http:// or https://: {args.base_url}"}, 1)
    folder = f".agents/skills/{skill}"
    if os.path.lexists(repo / folder):
        return emit({"error": f"{folder} already exists"}, 1)
    harnesses = config.get("harnesses", [])
    link = f".claude/skills/{skill}" if isinstance(harnesses, list) and "claude" in harnesses else None
    if link and os.path.lexists(repo / link):
        return emit({"error": f"{link} already exists"}, 1)
    if link and os.path.lexists(repo / ".claude/skills") and not (repo / ".claude/skills").is_dir():
        return emit({"error": ".claude/skills exists and is not a folder"}, 1)

    (repo / folder / "features").mkdir(parents=True)
    (repo / folder / "SKILL.md").write_text(SKILL_TEMPLATE.format(skill=skill, fill=FILL))
    (repo / folder / "features" / "README.md").write_text(FEATURES_TEMPLATE.format(fill=FILL))
    files = [f"{folder}/SKILL.md", f"{folder}/features/README.md"]
    if link:
        (repo / link).parent.mkdir(parents=True, exist_ok=True)
        (repo / link).symlink_to(f"../../{folder}")
        files.append(link)
    verify = {"skill": skill, "base_url": args.base_url}
    config["verify"] = verify
    (repo / "dev-loop.json").write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    files.append("dev-loop.json")
    return emit({"skill": skill, "files": files, "config": verify}, 0)


PLAYWRIGHT_DIRS = ("node_modules/playwright", "node_modules/@playwright/test",
                   "frontend/node_modules/playwright", "frontend/node_modules/@playwright/test")
READY_SECONDS = 30
TAIL_LINES = 20


def tail(text: str) -> str:
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def reachable(url: str, seconds: float) -> bool:
    """Any HTTP answer counts, an error status too. Direct: a proxy setting never answers for the app."""
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + seconds
    while True:
        try:
            opener.open(url, timeout=2).close()
            return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)


def clean_video(value: object, folder: Path) -> str | None:
    """A runner's `video` path, kept only when it is a file inside the entry's video folder."""
    if not isinstance(value, str) or not Path(value).is_absolute():
        return None
    path = Path(value).resolve()
    return str(path) if path.is_file() and path.is_relative_to(folder.resolve()) else None


def before_jobs(repo: Path, base: str, source: str, kit_runner: str) -> list[tuple[str, list, str]] | str:
    """The before run of `--routes-from`: per entry of the after `drive.json` with an `ok` route, its `ok` route
    paths (no names) and its runner. Only those map entries are read; a missing or bad one counts as absent."""
    from capture import read_drive

    after = read_drive(Path(source))
    if after is None:
        return f"--routes-from {source} is not a JSON object with a routes list"
    wanted: dict[str, list[dict]] = {}
    journeys = set()
    for route in after["routes"]:
        if route.get("video"):
            journeys.add(route["entry"])
        if route.get("ok") is True:
            wanted.setdefault(route["entry"], []).append({"path": route["path"], "names": []})
    jobs = []
    for stem, routes in wanted.items():
        path = repo / base / "features" / f"{stem}.md"
        entry = None
        if Path(stem).name == stem and not stem.startswith(".") and path.is_file():
            entry, _ = load_entry(repo, path)
        if stem in journeys:
            if entry and entry["script"]:
                jobs.append((stem, routes, str(repo / entry["script"])))
        else:
            jobs.append((stem, routes, kit_runner))
    return jobs


def cmd_drive(args: argparse.Namespace) -> int:
    from seal import git_dir, merge_base

    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top is None:
        return emit({"error": "not a git repository"}, 1)
    repo = Path(top)
    try:
        config = json.loads((repo / "dev-loop.json").read_text())
    except (OSError, ValueError):
        return emit({"error": f"no readable dev-loop.json in {repo}"}, 1)
    verify = config.get("verify", False) if isinstance(config, dict) else False
    if verify is False:
        return emit({"ok": True, "skipped": "no-verify", "routes": []}, 0)
    if errors := config_errors(verify):
        return emit({"error": "; ".join(errors)}, 1)
    base = skill_dir(verify["skill"])
    if not (repo / base / "SKILL.md").is_file():
        return emit({"error": f"missing {base}/SKILL.md (verify.skill)"}, 1)
    kit_runner = str(Path(__file__).parent / "drive.mjs")
    if args.routes_from:
        mode = "before"
        jobs = before_jobs(repo, base, args.routes_from, kit_runner)
        if isinstance(jobs, str):
            return emit({"error": jobs}, 1)
    else:
        loaded = {}
        for path in entries(repo, verify["skill"]):
            entry, reason = load_entry(repo, path)
            if reason:
                return emit({"error": f"bad feature map entry {path.relative_to(repo).as_posix()}: {reason}"}, 1)
            loaded[path.stem] = (path.relative_to(repo).as_posix(), entry)

        if args.since and not git(repo, "rev-parse", "--verify", "-q", f"{args.since}^{{commit}}"):
            return emit({"error": f"--since is not a commit: {args.since}"}, 1)
        if args.all:
            selected = sorted(loaded)
        else:
            base = merge_base(repo)
            revs = f"{args.since}..HEAD" if args.since else f"{base}...HEAD" if base else None
            if revs is None:
                return emit({"error": "no base branch: run drive --all or --since <sha>"}, 1)
            diff = git(repo, "-c", "core.quotepath=off", "diff", "--name-only", "-z", "--no-renames", revs)
            if diff is None:
                return emit({"error": f"git diff failed: {revs}"}, 1)
            changed = set(diff.split("\0")) - {""}
            selected = sorted(stem for stem, (rel, entry) in loaded.items() if rel in changed or any(
                fnmatch.fnmatchcase(path, pattern) for path in changed for pattern in entry["paths"]))
        mode = "all" if args.all else "since" if args.since else "diff"
        jobs = [(stem, loaded[stem][1]["routes"],
                 str(repo / loaded[stem][1]["script"]) if loaded[stem][1]["script"] else kit_runner)
                for stem in selected]
    selected = [stem for stem, _, _ in jobs]
    head = git(repo, "rev-parse", "HEAD")
    if not selected:
        return emit({"ok": True, "selected": [], "routes": [], "evidence": None, "mode": mode, "head": head}, 0)

    if not any((repo / d).exists() for d in PLAYWRIGHT_DIRS):
        return emit({"error": f"playwright not found in {', '.join(PLAYWRIGHT_DIRS)}: run npm i -D playwright "
                              "&& npx playwright install chromium in the repo root"}, 1)
    url = verify["base_url"]
    if not reachable(url, float(os.environ.get("DEVLOOP_TEST_READY_WAIT", READY_SECONDS))):
        return emit({"error": f"base_url not reachable: {url}"}, 1)
    for command in verify.get("setup", []):
        done = subprocess.run(command, shell=True, cwd=repo, capture_output=True, text=True)
        if done.returncode != 0:
            return emit({"error": f"setup command failed: {command}", "tail": tail(done.stdout + done.stderr)}, 1)
    state = None
    if "storage_state" in verify:
        state = repo / verify["storage_state"]
        if not state.is_file():
            return emit({"error": f"storage_state not found after setup: {verify['storage_state']}"}, 1)
        state = str(state.resolve())

    evidence = git_dir(repo) / "dev-loop" / "evidence" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence.mkdir(parents=True, exist_ok=True)
    routes = []
    for stem, entry_routes, runner in jobs:
        video = evidence / "video" / stem
        video.mkdir(parents=True, exist_ok=True)
        payload = {"base_url": url, "storage_state": state, "routes": entry_routes, "evidence": str(evidence),
                   "entry": stem, "video": str(video)}
        done = subprocess.run(["node", runner], cwd=repo, input=json.dumps(payload), capture_output=True, text=True)
        try:
            result = json.loads(done.stdout)
            if done.returncode != 0 or not isinstance(result.get("routes"), list):
                raise ValueError
        except (ValueError, AttributeError):
            if mode == "before":
                routes += [{"entry": stem, "path": r["path"], "ok": False, "status": None, "missing": [],
                            "console_errors": [], "error": "runner failed", "screenshot": None, "video": None,
                            "seconds": 0} for r in entry_routes]
                continue
            return emit({"error": f"runner failed for {stem}", "tail": tail(done.stdout + done.stderr)}, 1)
        routes += [{**r, "video": clean_video(r.get("video"), video)} for r in result["routes"]]
    out = {"ok": all(r.get("ok") is True for r in routes), "selected": selected, "routes": routes,
           "evidence": str(evidence), "mode": mode, "head": head}
    (evidence / "drive.json").write_text(json.dumps(out, indent=2) + "\n")
    return emit(out, 0 if out["ok"] or mode == "before" else 1)

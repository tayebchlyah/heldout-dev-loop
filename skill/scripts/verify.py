"""Verify skill rules: the `verify` key of dev-loop.json, the feature map entries, and the checks of check_setup.py.

Stdlib only: a map entry's frontmatter holds one line of JSON per key (valid YAML), parsed with `json`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

FILL = "<!-- dev-loop: fill -->"
SECTIONS = ("Launch", "Doctor", "Drive", "Evidence", "Cleanup")
MISSING = ('no verify skill for the ui stack: run devloop.py verify-init --base-url <url>, '
           'or set "verify": false in dev-loop.json')
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


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

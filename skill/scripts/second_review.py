#!/usr/bin/env python3
"""`devloop.py second-review`: the other model family's CLI as a read-only reviewer. The locks and the decision
order (D2, D4, D6 rules 1 to 6) live here; `_review` is the seam for the CLI run."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

import devloop

REVIEWER = {"claude": "codex", "codex": "claude"}
EFFORTS = {"codex": ("minimal", "low", "medium", "high", "xhigh"), "claude": ("low", "medium", "high", "xhigh", "max")}
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]*")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def load_unique(data: bytes | str) -> object:
    """JSON with duplicate keys refused. Bytes must be UTF-8."""
    return json.loads(data.decode("utf-8") if isinstance(data, bytes) else data, object_pairs_hook=_pairs)


def project_lock(repo: Path, ref: str) -> bool | None:
    """`second_review` of `dev-loop.json` at `ref`: False when the file or key is absent, None when bad."""
    shown = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:dev-loop.json"], capture_output=True)
    if shown.returncode != 0:
        return False
    try:
        config = load_unique(shown.stdout)
    except ValueError:
        return None
    if not isinstance(config, dict):
        return None
    value = config.get("second_review", False)
    return value if isinstance(value, bool) else None


def _entry_ok(entry: object, reviewer: str) -> bool:
    if entry is False:
        return True
    if not isinstance(entry, dict) or set(entry) - {"model", "effort"}:
        return False
    model = entry.get("model")
    return ((model is None or (isinstance(model, str) and MODEL.fullmatch(model) is not None))
            and entry.get("effort", "high") in EFFORTS[reviewer])


def local_entry(value: object, reviewer: str) -> tuple[str, dict | None]:
    """The D4 rules on the local `second_review` value."""
    if value is False:
        return "user-off", None
    if not isinstance(value, dict) or set(value) - set(EFFORTS) or not all(_entry_ok(v, k) for k, v in value.items()):
        return "bad-config", None
    entry = value.get(reviewer)
    if entry is None:
        return "not-chosen", None
    if entry is False:
        return "user-off", None
    return "ok", {"model": entry.get("model"), "effort": entry.get("effort", "high")}


def main_worktree(repo: Path) -> Path:
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True)
    return Path(common.stdout.strip()).parent if common.returncode == 0 else repo.resolve()


def payload(**fields: object) -> dict:
    out: dict = {"state": None, "reason": None, "message": None, "reviewer": None, "head": None, "base": None,
                 "model": None, "effort": None, "endpoint": "default", "output": None, "files": 0, "left_out": 0,
                 "seconds": 0, "tokens": None, "record": None}
    out.update(fields)
    return out


def _review(repo: Path, reviewer: str, ref: str, sha: str, entry: dict, timeout_minutes: float,
            env: Mapping[str, str], out: dict) -> tuple[dict, int]:
    """Rules 7 to 11: lookup, export, CLI run, audit. Filled by later tasks."""
    return {**out, "state": "failed", "reason": "export"}, 1


def run(repo: Path, harness: str, timeout_minutes: float, env: Mapping[str, str]) -> tuple[dict, int]:
    # Replace refs must not change what the locks read: every git call below inherits this.
    saved = os.environ.get("GIT_NO_REPLACE_OBJECTS")
    os.environ["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        return _decide(repo, harness, timeout_minutes, env)
    finally:
        if saved is None:
            del os.environ["GIT_NO_REPLACE_OBJECTS"]
        else:
            os.environ["GIT_NO_REPLACE_OBJECTS"] = saved


def _decide(repo: Path, harness: str, timeout_minutes: float, env: Mapping[str, str]) -> tuple[dict, int]:
    reviewer = REVIEWER[harness]
    out = payload(reviewer=reviewer)

    def skip(reason: str) -> tuple[dict, int]:
        return {**out, "state": "skipped", "reason": reason}, 0

    def fail(reason: str, message: str) -> tuple[dict, int]:
        return {**out, "state": "failed", "reason": reason, "message": message}, 1

    sha = devloop.git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    if devloop.git(repo, "rev-parse", "--is-inside-work-tree") != "true" or not sha:
        return fail("git", "not a git worktree or HEAD does not resolve")
    ref = devloop.main_ref(repo)
    if ref is None:
        return skip("no-main-ref")
    out["base"] = devloop.git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    lock = project_lock(repo, ref)
    if lock is None:
        return fail("bad-config", "dev-loop.json second_review is not a boolean, or the file does not parse")
    if not lock:
        return skip("project-off")
    local = main_worktree(repo) / "dev-loop.local.json"
    state, entry = "not-chosen", None
    if local.is_file():
        try:
            config = load_unique(local.read_bytes())
        except (ValueError, OSError):
            config = None
        if not isinstance(config, dict):
            state = "bad-config"
        elif "second_review" in config:
            state, entry = local_entry(config["second_review"], reviewer)
    if state == "bad-config":
        return fail("bad-config", "dev-loop.local.json second_review has a bad shape")
    if entry is None:
        return skip(state)
    out.update(model=entry["model"], effort=entry["effort"])
    git_dir = devloop.git(repo, "rev-parse", "--path-format=absolute", "--git-dir")
    if git_dir and any((Path(git_dir) / d).exists() for d in ("rebase-merge", "rebase-apply")):
        return skip("rebase")
    out["head"] = sha
    if devloop.git(repo, "merge-base", ref, sha) is None:
        return fail("no-merge-base", f"no merge base of {ref} and {sha}")
    if devloop.git(repo, "rev-list", "--count", f"{ref}..{sha}") == "0":
        return skip("nothing-to-review")
    return _review(repo, reviewer, ref, sha, entry, timeout_minutes, env, out)

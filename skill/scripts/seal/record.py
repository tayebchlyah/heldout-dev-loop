"""The seal record `<git-dir>/dev-loop/seals.json`: each seal commit `seal` made on a branch, and its patch values."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

from seal import PATCH_TRAILER, git_dir, run_git

PATCH = re.compile(rf"^{PATCH_TRAILER}:[ \t]*(\S*)[ \t]*$", re.MULTILINE)
TEXT_KEYS = ("branch", "commit", "seal", "feature")


def record_file(repo: Path) -> Path:
    return git_dir(repo) / "dev-loop" / "seals.json"


def _entry_ok(entry: object) -> bool:
    if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in TEXT_KEYS):
        return False
    patches, paths = entry.get("patches"), entry.get("paths")
    return (isinstance(patches, list) and bool(patches) and all(isinstance(p, str) for p in patches)
            and isinstance(paths, list) and all(isinstance(p, str) for p in paths))


def _read(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(str(error)) from error
    seals = data.get("seals") if isinstance(data, dict) else None
    if not isinstance(seals, list) or not all(_entry_ok(entry) for entry in seals):
        raise ValueError("not a seal record")
    return seals


def read_record(repo: Path) -> list[dict] | None:
    return _read(record_file(repo))


def _update(repo: Path, change, create: bool) -> None:
    path = record_file(repo)
    if not create and not path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name("seals.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        seals = _read(path)
        if seals is None and not create:
            return
        seals = change(seals or [])
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".seals.")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"seals": seals}, out, indent=2)
        os.replace(tmp, path)


def add_seal(repo: Path, entry: dict) -> None:
    _update(repo, lambda seals: [*seals, entry], create=True)


def replace_patch(repo: Path, old: str, entry: dict) -> None:
    new = entry["patches"][-1]

    def change(seals: list[dict]) -> list[dict]:
        matched = False
        for seal in seals:
            if seal["branch"] == entry["branch"] and old in seal["patches"]:
                matched = True
                seal.update(commit=entry["commit"], seal=entry["seal"], paths=entry["paths"],
                            patches=seal["patches"] + ([] if seal["patches"][-1] == new else [new]))
        return seals if matched else [*seals, entry]

    _update(repo, change, create=False)


def record_branch(repo: Path) -> str | None:
    top = git_dir(repo)
    for state in ("rebase-merge", "rebase-apply"):
        head_name = top / state / "head-name"
        if head_name.is_file():
            name = head_name.read_text(encoding="utf-8").strip()
            return name if name.startswith("refs/") else None
    ref = run_git(repo, "symbolic-ref", "-q", "HEAD")
    return ref.stdout.strip() if ref.returncode == 0 else None


def skip_reason(repo: Path) -> str | None:
    top = git_dir(repo)
    if (top / "rebase-merge").is_dir() or (top / "rebase-apply").is_dir():
        return "rebase"
    if (top / "CHERRY_PICK_HEAD").exists():
        return "cherry-pick"
    return None if run_git(repo, "symbolic-ref", "-q", "HEAD").returncode == 0 else "detached"


def patch_carriers(repo: Path) -> dict[str, str]:
    log = run_git(repo, "log", "-z", "--format=%H%n%B", "--fixed-strings", f"--grep={PATCH_TRAILER}:", "HEAD")
    carriers: dict[str, str] = {}
    for item in (part for part in log.stdout.split("\0") if part):
        sha, _, body = item.partition("\n")
        for value in PATCH.findall(re.split(r"\n[ \t]*\n", body.strip())[-1]):
            carriers.setdefault(value, sha)
    return carriers

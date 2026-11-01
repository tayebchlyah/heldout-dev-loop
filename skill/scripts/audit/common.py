"""Shared core of `audit-run`: the run folder, the trusted state, atomic JSON writes, the skill validators, the ledger
write, canonical refs and coverage ids, path rules, the run scope and git paths."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Callable, NoReturn

from devloop import AUDITS, emit
from notes_path import NotesPathError, resolve
from seal import ACCEPTANCE_DIR

STATE = "audit-run.json"
LEDGER = "coverage-ledger.json"
METADATA = "run-metadata.json"
FINDINGS = "findings.json"
PLAN = "plan.json"
FINGERPRINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")
AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
QUICK_SUBSYSTEM = "profile/quick/all-in-scope-subsystems"
PRIOR_STATUSES = ("new", "prior_confirmed_same_source", "prior_confirmed_changed_source", "prior_needs_validation",
                  "prior_deferred", "prior_blocked", "prior_out_of_scope", "prior_covered_same_source",
                  "prior_covered_changed_source", "prior_rejected_claim_changed", "none")
REF_KEYS = ("surface", "boundary", "subsystem", "attack_class")
IGNORABLE = ((0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x3164, 0x3164),
             (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0xE0000, 0xE0FFF))


class StepError(Exception):
    """A step failure: `payload` is the JSON the step prints with exit 1."""

    def __init__(self, payload: dict) -> None:
        super().__init__(payload.get("error"))
        self.payload = payload


def fail(error: str, **fields: object) -> NoReturn:
    """Stop the step with exit 1 and the JSON `{"error": error, **fields}`."""
    raise StepError({"error": error, **fields})


def run_folder(repo: Path, folder: str) -> Path:
    """The run folder `<notes>/Security Audit/Run <N>` named by `--run`, absolute; else `bad-run`."""
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        fail(str(exc))
    path = Path(folder).resolve()
    if not (path.is_dir() and re.fullmatch(r"Run \d+", path.name) and path.parent == (notes / AUDITS).resolve()):
        fail("bad-run", run=folder)
    return path


def run_step(args: argparse.Namespace, body: Callable[[Path, dict | None], dict], seeded: bool = True) -> int:
    """Run one step: the run folder, the state when `seeded` (else `not-seeded`), then `body`; print its JSON."""
    try:
        run = run_folder(Path(args.repo), args.run)
        state = None
        if seeded:
            if not ((run / METADATA).is_file() and (run / STATE).is_file()):
                fail("not-seeded", run=str(run))
            state = load_state(run)
        return emit(body(run, state), 0)
    except StepError as exc:
        return emit(exc.payload, 1)


def load_state(run: Path) -> dict:
    """The trusted state `audit-run.json` of the run."""
    return json.loads((run / STATE).read_text(encoding="utf-8"))


def save_state(run: Path, state: dict) -> None:
    """Write the trusted state by temp file and rename."""
    write_json_atomic(run / STATE, state)


def read_json(path: Path) -> object:
    """The JSON value of `path`; raises `OSError` or `ValueError`."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: object) -> None:
    """Write `value` to a temp file beside `path`, then rename it onto `path`; no temp file is left on an error."""
    temp = path.parent / f".{path.name}.{secrets.token_hex(6)}"
    try:
        with open(temp, "x", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        os.replace(temp, path)
    finally:
        if temp.exists() or temp.is_symlink():
            temp.unlink()


def run_validator(skill: Path, script: str, target: Path) -> str | None:
    """Run the skill validator `script` on `target`: `None` when it passes, else the tail of its output."""
    try:
        done = subprocess.run(["node", str(skill / script), str(target)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{script}: {type(exc).__name__}"
    if done.returncode == 0:
        return None
    return (done.stderr.strip() or done.stdout.strip() or f"{script}: exit {done.returncode}")[-500:]


def read_ledger(run: Path) -> list[dict]:
    """The units of the run ledger."""
    return json.loads((run / LEDGER).read_text(encoding="utf-8"))


def write_ledger(run: Path, skill: Path, units: list[dict]) -> None:
    """Sort the units by id, validate them in a temp file, then rename it onto the ledger; else `ledger-invalid`."""
    temp = run / f".{LEDGER}.{secrets.token_hex(6)}"
    try:
        with open(temp, "x", encoding="utf-8") as handle:
            handle.write(json.dumps(sorted(units, key=lambda u: u["coverage_id"]), indent=2, ensure_ascii=False) + "\n")
        tail = run_validator(skill, "validate-coverage-ledger.cjs", temp)
        if tail is not None:
            fail("ledger-invalid", tail=tail)
        os.replace(temp, run / LEDGER)
    finally:
        if temp.exists() or temp.is_symlink():
            temp.unlink()


def companion_files(units: list[dict]) -> list[str]:
    """The sorted file names of every companion block of the units."""
    return sorted({block.split("#", 1)[0] for u in units for block in u.get("selected_companion_blocks") or []})


def sync_companions(run: Path, units: list[dict]) -> None:
    """Keep `selected_companion_files` of the run metadata equal to the companion files of the units."""
    metadata = json.loads((run / METADATA).read_text(encoding="utf-8"))
    files = companion_files(units)
    if metadata.get("selected_companion_files") != files:
        metadata["selected_companion_files"] = files
        write_json_atomic(run / METADATA, metadata)


def ignorable(char: str) -> bool:
    return any(low <= ord(char) <= high for low, high in IGNORABLE)


def ref_errors(value: object) -> list[str]:
    """Why `value` is no canonical ref: not a string, not NFC, outer whitespace, a hidden character, nothing visible."""
    if not isinstance(value, str):
        return ["not a string"]
    errors = []
    if unicodedata.normalize("NFC", value) != value:
        errors.append("not NFC")
    if value.strip() != value:
        errors.append("leading or trailing whitespace")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp") or ignorable(c) for c in value):
        errors.append("control or invisible character")
    if not any(not c.isspace() and unicodedata.category(c) not in ("Cc", "Cf", "Cs") and not ignorable(c) for c in value):
        errors.append("no visible character")
    return errors


def coverage_id(refs: dict) -> str:
    """The coverage id of canonical refs: each ref percent-encoded (RFC 3986 unreserved kept), joined with `::`."""
    keys = REF_KEYS + (("lifecycle",) if "lifecycle" in refs else ())
    return "::".join(urllib.parse.quote(refs[key], safe="") for key in keys)


def path_error(value: object) -> str | None:
    """Why `value` is no safe relative repo path, else `None`; one trailing `/` is allowed."""
    if not isinstance(value, str) or not value:
        return "not a non-empty string"
    if value.startswith("/"):
        return "absolute path"
    if "\\" in value or any(unicodedata.category(c) == "Cc" for c in value):
        return "backslash or control character"
    parts = (value[:-1] if value.endswith("/") else value).split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "empty, . or .. part"
    return None


def in_scope(scope: dict, paths: list[str]) -> bool:
    """True when one of the paths is in the run scope."""
    for raw in paths:
        path = raw.rstrip("/")
        if scope["kind"] == "whole":
            if path not in scope["exclude"] and path != ACCEPTANCE_DIR and not path.startswith(ACCEPTANCE_DIR + "/"):
                return True
        elif any(p == path or p.startswith(path + "/") for p in scope["paths"]):
            return True
    return False


def git_paths(repo: Path, *args: str) -> list[str] | None:
    """The NUL-split paths a git command prints, unquoted; `None` when git fails."""
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args, "-z"], capture_output=True)
    if done.returncode != 0:
        return None
    return [part for part in done.stdout.decode("utf-8", "surrogateescape").split("\0") if part]


def blank_evidence(unit: dict) -> None:
    """No owner and no evidence on the unit."""
    unit["agent_id"] = None
    for field in ("reviewed_paths", "local_checks", "result_fingerprints", "unresolved"):
        unit[field] = []

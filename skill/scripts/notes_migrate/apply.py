"""`devloop.py notes-migrate`: plan the notes migration, or apply it through a journal (spec D9, D11, D12)."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
import unicodedata
from pathlib import Path

import devloop
from board.writer import inside, worktrees
from notes_migrate.texts import Plan, build_plan, plan_hash
from notes_path import NotesPathError, resolve

LOCK = ".notes-migrate.lock"
TMP = ".notes-migrate-tmp"
LISTS = ("moves", "frontmatter", "outside_links", "outside_skipped", "repo_links", "git_case", "gitignore",
         "ambiguous", "blocked")
RESTART = "restore the planned text, or delete .notes-migrate.json and start a new dry run"
CHECK = "check it, then delete .notes-migrate.json and start a new dry run"
KEY = Path(".config", "dev-loop", "notes-migrate.key")


class Stop(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _report(plan_hash: str | None = None, **fields) -> dict:
    return {"applied": False, "plan_hash": plan_hash, "moves": [], "frontmatter": [], "links": {"files": [], "count": 0},
            "audit_ids": 0, "outside_links": [], "outside_skipped": [], "repo_links": [], "git_case": [],
            "gitignore": [], "ambiguous": [], "blocked": [], "log": None, "error": None, **fields}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact(path: Path) -> bool:
    try:
        names = {unicodedata.normalize("NFC", name) for name in os.listdir(path.parent)}
    except OSError:
        return False
    return unicodedata.normalize("NFC", path.name) in names


def _plan_report(notes: Path, plan: Plan) -> dict:
    rel = lambda path: path.relative_to(notes).as_posix()
    return _report(plan.plan_hash, moves=[{"from": rel(m.src), "to": rel(m.dst)} for m in plan.moves],
                   frontmatter=plan.frontmatter, links=plan.links, audit_ids=plan.audit_ids,
                   outside_links=plan.outside_links, outside_skipped=plan.outside_skipped,
                   repo_links=plan.repo_links, git_case=plan.git_case, gitignore=plan.gitignore,
                   ambiguous=plan.ambiguous, blocked=[{"path": b.path, "reason": b.reason} for b in plan.blocked])


def _sign(journal: dict) -> str:
    path = Path.home() / KEY
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32))
    body = json.dumps({k: v for k, v in journal.items() if k != "hmac"}, sort_keys=True).encode()
    return hmac.new(path.read_bytes(), body, hashlib.sha256).hexdigest()


def _save(path: Path, journal: dict) -> None:
    _put(path, json.dumps({**journal, "hmac": _sign(journal)}).encode())


def _fields(item: object, types: dict) -> bool:
    return isinstance(item, dict) and all(isinstance(item.get(key), kind) for key, kind in types.items())


def _load(notes: Path) -> dict:
    try:
        journal = json.loads((notes / devloop.JOURNAL).read_text())
    except (OSError, ValueError) as exc:
        raise Stop("io", f"{devloop.JOURNAL} unreadable ({exc}): {RESTART}") from None
    if not (_fields(journal, {"host": str, "plan_hash": str, "report": dict, "moves": list, "writes": list})
            and all(_fields(m, {"src": str, "dst": str, "role": str, "sha": (str, type(None))}) for m in journal["moves"])
            and all(_fields(w, {"path": str, "old": str, "new": str, "text": str}) for w in journal["writes"])):
        raise Stop("io", f"{devloop.JOURNAL} is not a notes-migrate journal: {RESTART}")
    root = Path(os.path.realpath(notes))
    for rel in [*(m[key] for m in journal["moves"] for key in ("src", "dst")), *(w["path"] for w in journal["writes"])]:
        real = Path(os.path.realpath(notes / rel))
        if os.path.isabs(rel) or ".." in Path(rel).parts or real == root or not real.is_relative_to(root):
            raise Stop("io", f"{devloop.JOURNAL} step {rel!r} is outside the notes folder: {CHECK}")
    if journal["plan_hash"] != plan_hash([(notes / m["src"], notes / m["dst"]) for m in journal["moves"]],
                                         [(notes / w["path"], w["old"], w["new"]) for w in journal["writes"]]):
        raise Stop("io", f"{devloop.JOURNAL} plan_hash is not the hash of its steps: {CHECK}")
    if journal["host"] != socket.gethostname():
        raise Stop("locked", f"{devloop.JOURNAL} of {journal['host']}: resume it on that host, or delete it and the lock "
                   "after checking that host is idle")
    if not hmac.compare_digest(str(journal.get("hmac")), _sign(journal)):
        raise Stop("locked", f"{devloop.JOURNAL} was not written by notes-migrate on this machine (key ~/{KEY}): {CHECK}")
    return journal


def _resume(notes: Path, planned: str | None) -> dict:
    journal = _load(notes)
    if planned and planned != journal["plan_hash"]:
        raise Stop("plan-changed", f"--plan {planned} is not the plan {journal['plan_hash']} of {devloop.JOURNAL}: "
                   "resume with no --plan, or check it, delete it and start a new dry run")
    return journal


def _journal(notes: Path, plan: Plan) -> dict:
    rel = lambda path: path.relative_to(notes).as_posix()
    report = _plan_report(notes, plan)
    moves = [{"src": rel(m.src), "dst": rel(m.dst), "role": m.role,
              "sha": None if m.role == "folder" or m.src == m.dst else _sha(m.src.read_bytes())} for m in plan.moves]
    writes = [{"path": rel(w.path), "old": w.old_sha, "new": w.new_sha, "text": w.text} for w in plan.writes]
    return {"host": socket.gethostname(), "plan_hash": plan.plan_hash, "report": report, "moves": moves, "writes": writes}


def _put(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + TMP)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _lock(path: Path) -> str | None:
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                held = json.loads(path.read_text())
            except (OSError, ValueError):
                held = {}
            held = held if isinstance(held, dict) else {}
            if held.get("host") == socket.gethostname() and isinstance(held.get("pid"), int):
                try:
                    os.kill(held["pid"], 0)
                except ProcessLookupError:
                    path.unlink(missing_ok=True)
                    continue
                except PermissionError:
                    pass
            return str(held.get("host") or "an unknown host")
        with os.fdopen(fd, "w") as handle:
            json.dump({"host": socket.gethostname(), "pid": os.getpid()}, handle)
        return None
    return "this host"


def _move_all(notes: Path, journal: dict) -> None:
    done: list[tuple[str, str]] = []
    after = {w["path"]: w["new"] for w in journal["writes"]}
    for step in journal["moves"]:
        rel = step["src"]
        for old, new in sorted(done, key=lambda pair: -len(pair[0])):
            if rel == old or rel.startswith(old + "/"):
                rel = new + rel[len(old):]
                break
        src, dst = notes / rel, notes / step["dst"]
        if step["role"] == "folder":
            done.append((step["src"], step["dst"]))
        if step["role"] == "board-merge" and step["src"] == step["dst"]:
            if _exact(src):
                src.unlink()
            continue
        case = src.parent == dst.parent and src.name != dst.name and src.name.lower() == dst.name.lower()
        tmp = src.with_name(src.name + TMP)
        if case and _exact(tmp):
            os.rename(tmp, dst)
        elif _exact(src):
            if step["role"] == "board-merge" and _exact(dst):
                dst.unlink()
            if _exact(dst):
                raise Stop("io", f"{step['dst']} exists: it synced in after the plan; move it away, then run again")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if case:
                os.rename(src, tmp)
                os.rename(tmp, dst)
            else:
                os.rename(src, dst)
        elif not _exact(dst) or (step["sha"] and _sha(dst.read_bytes()) not in (step["sha"], after.get(step["dst"]))):
            raise Stop("io", f"{step['src']} is gone and {step['dst']} is not the planned file: {RESTART}")


def _write_all(repo: Path, notes: Path, journal: dict) -> None:
    common = devloop.git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    lock = Path(common or repo) / "dev-loop" / "decide.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        for step in journal["writes"]:
            path = notes / step["path"]
            try:
                now = _sha(path.read_bytes())
            except FileNotFoundError:
                raise Stop("io", f"{step['path']} is missing: {RESTART}") from None
            if now == step["new"]:
                continue
            if now != step["old"]:
                raise Stop("io", f"{step['path']} changed after the plan: {RESTART}")
            _put(path, step["text"].encode("utf-8", "surrogateescape"))


def _clean(notes: Path) -> None:
    for name in os.listdir(notes):
        if name not in devloop.OLD_NAMES or not (notes / name).is_dir():
            continue
        for root, _, _ in os.walk(notes / name, topdown=False):
            left = os.listdir(root)
            if set(left) <= {".DS_Store"}:
                for entry in left:
                    os.unlink(Path(root, entry))
                os.rmdir(root)


def _log(notes: Path, report: dict) -> str:
    day = time.strftime("%Y%m%d")
    name, n = f"{day} Notes Migration.md", 1
    while _exact(notes / name):
        n += 1
        name = f"{day} Notes Migration {n}.md"
    lines = ["---", "type: note", "---", f"# Notes Migration {day}", "",
             f"Plan `{report['plan_hash']}`. No automatic undo: restore from the vault git history or iCloud.", "",
             "## Moves", ""]
    lines += [f"- `{m['from']}` to `{m['to']}`" if m["from"] != m["to"] else f"- `{m['from']}` removed (older board note)"
              for m in report["moves"]] or ["- none"]
    lines += ["", "## Links outside the notes folder", "", "Never changed: fix each after confirming it.", ""]
    lines += [f"- `{item['file']}` line {item['line']}: `{item['link']}`" for item in report["outside_links"]] or ["- none"]
    lines += ["", "## Repo files naming a moved path", ""]
    lines += [f"- `{item['file']}` line {item['line']}" for item in report["repo_links"]] or ["- none"]
    lines += ["", "## Git index case", "", "Run from the notes folder:", ""]
    lines += [f"- `{command}`" for command in report["git_case"]] or ["- none"]
    _put(notes / name, ("\n".join(lines) + "\n").encode())
    return name


def _apply(repo: Path, notes: Path, journal: dict) -> dict:
    _move_all(notes, journal)
    _write_all(repo, notes, journal)
    _clean(notes)
    report = {**journal["report"], "applied": True}
    if journal["moves"] or journal["writes"]:
        report["log"] = _log(notes, report)
    (notes / devloop.JOURNAL).unlink(missing_ok=True)
    return report


def cmd_notes_migrate(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return devloop.emit(_report(error="notes-path", message=str(exc)), 1)
    common = devloop.git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    roots = [*worktrees(devloop, repo), *([Path(common).resolve()] if common else [])]
    if any(inside(notes.resolve(), root) for root in roots):
        return devloop.emit(_report(error="notes-in-repo", message=f"{notes} is inside the repo or its git dir"), 1)
    journal_path = notes / devloop.JOURNAL
    resuming = _exact(journal_path)
    if args.apply and not args.plan and not resuming:
        return devloop.emit({"error": "--apply needs --plan <plan_hash> of a dry run"}, 2)
    if resuming:
        try:
            journal = _resume(notes, args.plan)
        except Stop as exc:
            return devloop.emit(_report(error=exc.code, message=str(exc)), 1)
        if not args.apply:
            return devloop.emit(journal["report"], 0)
    else:
        manifest = None
        try:
            manifest = json.loads((repo / ".dev-loop" / "MANIFEST.json").read_text())
        except (OSError, ValueError):
            pass
        version = str(manifest.get("version") or "0") if isinstance(manifest, dict) else "0"
        if not args.apply:
            plan = build_plan(repo, notes, version)
            return devloop.emit(_plan_report(notes, plan), 1 if plan.blocked else 0)
    lock = notes / LOCK
    holder = _lock(lock)
    if holder is not None:
        return devloop.emit(_report(error="locked", message=f"{lock} held by {holder}: run notes-migrate there, or delete "
                                    f"the lock and {devloop.JOURNAL} after checking that host is idle"), 1)
    try:
        if not resuming:
            plan = build_plan(repo, notes, version)
            if plan.blocked:
                return devloop.emit(_plan_report(notes, plan), 1)
            if plan.plan_hash != args.plan:
                return devloop.emit({**_plan_report(notes, plan), "error": "plan-changed"}, 1)
            journal = _journal(notes, plan)
            if plan.moves or plan.writes:
                _save(journal_path, journal)
        try:
            return devloop.emit(_apply(repo, notes, journal), 0)
        except Stop as exc:
            return devloop.emit({**journal["report"], "error": exc.code, "message": str(exc)}, 1)
        except OSError as exc:
            return devloop.emit({**journal["report"], "error": "io", "message": f"{exc}: run notes-migrate --apply "
                                 "again to resume"}, 1)
    finally:
        lock.unlink(missing_ok=True)

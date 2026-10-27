"""Release security audit: parse the project note (security items, rulings, audit records).

Shared by `devloop.py` (`release-plan`, `release-record`, `release-check`).
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import ci_skip
from autonomy import write_atomic
from devloop import emit
from merge_check import is_test_or_doc
from notes_path import NotesPathError, resolve

ITEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \(security, (confirmed|needs-validation), ([A-Za-z0-9._-]+)\): (.+)$")
RULING_RE = re.compile(r"^(?: {2,}|\t)\s*- Ruling \d{4}-\d{2}-\d{2}: (\w+) ")
RECORD_RE = re.compile(
    r"^- (\d{4}-\d{2}-\d{2}) (run-\d+) at ([0-9a-f]{40}): confirmed \d+, needs validation \d+, hardening \d+, "
    r"profile \S+, scope (whole|[0-9a-f]{40}\.\.[0-9a-f]{40})$"
)
CLOSING_WORDS = ("accept", "close")
FINGERPRINT_RE = re.compile(r"Fingerprint `([^`]+)`")
SECURITY_RE = re.compile(r"(?i)\bsecurity\b")
TAG_RE = re.compile(r"v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-rc\.[1-9]\d*)?)")
CHECKBOX_RE = re.compile(r"^\[[ x]\] ")


def project_note(notes: Path) -> Path | None:
    """The project note: `Project Note.md`, else `<notes folder name>.md`, else None."""
    for path in (notes / "Project Note.md", notes / f"{notes.name}.md"):
        if path.is_file():
            return path
    return None


def section(text: str, heading: str) -> list[str]:
    """The lines of the `## <heading>` section, up to the next `## ` heading. `###` subsections stay in."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip() == heading
        elif inside:
            out.append(line)
    return out


def security_items(text: str) -> tuple[list[dict], list[str]]:
    """(open items, unparsed lines) of `## Deferred`.

    An item is `{line, verdict, source, fingerprint}`. A `needs-validation` item with a ruling `accept` or `close`
    is closed and left out. A top-level bullet that names security and does not match the item format is unparsed.
    """
    items: list[dict] = []
    unparsed: list[str] = []
    current: dict | None = None
    closed = False

    def finish() -> None:
        if current is not None and not closed:
            items.append(current)

    for line in section(text, "Deferred"):
        if line.startswith("- "):
            finish()
            current, closed = None, False
            body = CHECKBOX_RE.sub("", line[2:])
            match = ITEM_RE.match(body)
            if match:
                fingerprint = FINGERPRINT_RE.search(match.group(4))
                current = {
                    "line": line.rstrip(),
                    "verdict": match.group(2),
                    "source": match.group(3),
                    "fingerprint": fingerprint.group(1) if fingerprint else None,
                }
            elif SECURITY_RE.search(line):
                unparsed.append(line.rstrip())
        elif line.strip() and not line[0].isspace():
            finish()
            current, closed = None, False
        elif current is not None and current["verdict"] == "needs-validation":
            ruling = RULING_RE.match(line)
            if ruling and ruling.group(1) in CLOSING_WORDS:
                closed = True
    finish()
    return items, unparsed


def current_record(text: str) -> dict | None:
    """`{line, run, sha, scope}` of the last record bullet of `## Release audits`, or None."""
    found = None
    for line in section(text, "Release audits"):
        match = RECORD_RE.match(line.rstrip())
        if match:
            found = {"line": line.rstrip(), "run": match.group(2), "sha": match.group(3), "scope": match.group(4)}
    return found


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def mismatch(notes: Path, record: dict) -> str | None:
    """Why the run folder of the record does not back it, else None."""
    path = notes / "security-audit" / record["run"] / "run-metadata.json"
    try:
        meta = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return f"{path.parent.name}/run-metadata.json: {type(exc).__name__}"
    source = meta.get("source_ref") if isinstance(meta, dict) else None
    commit = source.get("commit") if isinstance(source, dict) else None
    if commit != record["sha"]:
        return f"source_ref.commit is {commit}, the record says {record['sha']}"
    if meta.get("run_status") != "complete":
        return f"run_status is {meta.get('run_status')}, not complete"
    return None


def stale_paths(repo: Path, record_sha: str, target: str) -> list[str] | None:
    """Paths that make the audit stale: the code paths changed since the audited commit, all changed paths when it is
    not an ancestor (possibly none). None when the audit is current: only test or doc paths changed, or the
    code paths are all a release or kit upgrade that `ci-skip` allows."""
    ancestor = run_git(repo, "merge-base", "--is-ancestor", record_sha, target).returncode == 0
    diff = run_git(repo, "diff", "--name-only", "-z", "--no-renames", record_sha, target)
    if diff.returncode != 0:  # the record commit is unknown here
        diff = run_git(repo, "ls-tree", "-r", "--name-only", "-z", target)
    paths = [p for p in diff.stdout.split("\0") if p]
    if not ancestor:
        return paths
    code = [p for p in paths if not is_test_or_doc(p)]
    if not code:
        return None
    try:
        blocked = set(ci_skip.blocking(repo, record_sha, target, paths))
    except ci_skip.ConfigError:
        return code
    return code if blocked.intersection(code) else None


def is_release_tag(name: str) -> bool:
    return fnmatch.fnmatchcase(name, "v*")


def check(repo: Path, notes: Path | None, target: str, tag: str | None) -> tuple[dict, int]:
    """The release gate: a current audit record and no open security item. Exit 0 when ok.

    `notes` may be None only for a non-release tag, which is skipped before any note read."""
    resolved = run_git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{target}^{{commit}}")
    if resolved.returncode != 0:
        return {"error": f"cannot resolve {target}"}, 1
    sha = resolved.stdout.strip()
    if tag is not None and not is_release_tag(tag):
        return {"ok": True, "tag": tag, "target": sha, "record": None, "reasons": [], "skipped": "not-a-release-tag"}, 0
    if notes is None:
        return {"error": "notes path unresolved"}, 1
    note = project_note(notes)
    if note is None:
        return {"error": "no-project-note"}, 1
    try:
        text = note.read_text()
    except (OSError, ValueError) as exc:
        return {"error": f"cannot read {note.name}: {type(exc).__name__}"}, 1
    reasons: list[dict] = []
    record = current_record(text)
    if record is None:
        reasons.append({"reason": "no-audit"})
    elif (detail := mismatch(notes, record)) is not None:
        reasons.append({"reason": "record-mismatch", "detail": detail})
    elif record["sha"] != sha and (paths := stale_paths(repo, record["sha"], sha)) is not None:
        reasons.append({"reason": "stale-audit", "paths": paths})
    items, unparsed = security_items(text)
    if items:
        reasons.append({"reason": "open-security", "items": [i["line"] for i in items]})
    if unparsed:
        reasons.append({"reason": "unparsed-security", "lines": unparsed})
    ok = not reasons
    return {"ok": ok, "tag": tag, "target": sha, "record": record["line"] if record else None, "reasons": reasons}, 0 if ok else 1


def skill_dir(harness: str, env: Mapping[str, str]) -> Path:
    """The `security-audit` skill folder of the harness; `DEVLOOP_TEST_SECURITY_AUDIT_DIR` overrides it (tests)."""
    if env.get("DEVLOOP_TEST_SECURITY_AUDIT_DIR"):
        return Path(env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"])
    return Path.home() / (".claude" if harness == "claude" else ".agents") / "skills" / "security-audit"


def docker_up() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def expected_base(repo: Path, record_sha: str, commit: str) -> str | None:
    """The base of an audit of `commit` after the record at `record_sha`: that sha when it is an ancestor of `commit`,
    else their merge base, else None."""
    if run_git(repo, "merge-base", "--is-ancestor", record_sha, commit).returncode == 0:
        return record_sha
    merged = run_git(repo, "merge-base", record_sha, commit)
    return merged.stdout.strip() if merged.returncode == 0 and merged.stdout.strip() else None


KIT_ROOTS = (".agents/skills/", ".claude/agents/", ".claude/skills/", ".codex/agents/", ".codex/rules/", ".dev-loop/hooks/")
MANIFEST = ".dev-loop/MANIFEST.json"


def manifest_at(repo: Path, commit: str) -> dict | None:
    shown = run_git(repo, "show", f"{commit}:{MANIFEST}")
    try:
        data = json.loads(shown.stdout) if shown.returncode == 0 else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def version_at(repo: Path, commit: str) -> str | None:
    data = manifest_at(repo, commit)
    version = data.get("version") if data else None
    return version if isinstance(version, str) and version else None


def kit_copies(repo: Path, commit: str, among: set[str]) -> set[str]:
    data = manifest_at(repo, commit)
    files = data.get("files") if data else None
    if not isinstance(files, dict):
        return set()
    tree = run_git(repo, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries = {}
    for line in tree.stdout.split("\0") if tree.returncode == 0 else []:
        meta, _, path = line.partition("\t")
        if path:
            mode, _, blob = meta.split(" ")
            entries[path] = (mode, blob)
    copies = {MANIFEST}
    for path, expected in files.items():
        if not (isinstance(expected, str) and path in among and path.startswith(KIT_ROOTS) and path in entries):
            continue
        mode, blob = entries[path]
        if mode not in ("100644", "100755", "120000"):
            continue
        read = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", blob], capture_output=True)
        if read.returncode != 0:
            continue
        content = read.stdout
        if mode == "120000":
            actual = "link:" + content.decode("utf-8", "replace")
        else:
            actual = "sha256:" + hashlib.sha256(content).hexdigest()
        if expected == actual:
            copies.add(path)
    return copies


def excluded(repo: Path, record_sha: str, base: str, head: str) -> list[str]:
    if base != record_sha:
        return []
    old, new = version_at(repo, base), version_at(repo, head)
    if not (old and new) or old == new:
        return []
    diff = run_git(repo, "diff", "--name-only", "-z", "--no-renames", base, head)
    changed = {p for p in diff.stdout.split("\0") if p} if diff.returncode == 0 else set()
    upgrades: dict[str, bool] = {}

    def upgraded(commit: str) -> bool:
        if commit not in upgrades:
            mine, parent = version_at(repo, commit), version_at(repo, f"{commit}^")
            upgrades[commit] = mine is not None and parent is not None and mine != parent
        return upgrades[commit]

    def only_upgrades(path: str) -> bool:
        touched = run_git(repo, "rev-list", f"{base}..{head}", "--", path)
        return touched.returncode == 0 and all(upgraded(c) for c in touched.stdout.split())

    return sorted(p for p in kit_copies(repo, head, changed) & changed if only_upgrades(p))


def plan(repo: Path, notes: Path | None, harness: str, profile: str | None = None, budget: int | None = None) -> tuple[dict, int]:
    """The release-audit plan: run folder (created), scope, sandbox and skill. Exit 0, else 1 with `error`, nothing created."""
    if notes is None:
        return {"error": "notes path unresolved"}, 1
    note = project_note(notes)
    if note is None:
        return {"error": "no-project-note"}, 1
    try:
        text = note.read_text()
    except (OSError, ValueError) as exc:
        return {"error": f"cannot read {note.name}: {type(exc).__name__}"}, 1
    status = run_git(repo, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=no")
    if status.returncode != 0:
        return {"error": "git status failed"}, 1
    dirty = [e[3:] for e in status.stdout.split("\0") if e]
    if dirty:
        return {"error": "dirty", "paths": dirty}, 1
    audits = notes.absolute() / "security-audit"
    placeholders = sorted(str(p) for p in audits.rglob(".*.icloud")) if audits.is_dir() else []
    if placeholders:
        return {"error": "placeholder", "files": placeholders}, 1
    head = run_git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:
        return {"error": "cannot resolve HEAD"}, 1
    head_sha = head.stdout.strip()
    record = current_record(text)
    first = record is None
    base = None
    exclude: list[str] = []
    if record is not None:
        base = expected_base(repo, record["sha"], head_sha)
        if base is not None:
            exclude = excluded(repo, record["sha"], base, head_sha)
    runs = sorted(
        (int(p.name[4:]), p) for p in audits.iterdir() if p.is_dir() and re.fullmatch(r"run-\d+", p.name)
    ) if audits.is_dir() else []
    n = runs[-1][0] + 1 if runs else 1
    out = audits / f"run-{n}"
    try:
        out.mkdir(parents=True)
    except FileExistsError:
        return {"error": "run-folder-exists", "output": str(out)}, 1
    top = run_git(repo, "rev-parse", "--show-toplevel").stdout.strip() or str(repo)
    try:
        config = json.loads((Path(top) / "dev-loop.json").read_text())
    except (OSError, ValueError):
        config = {}
    audit = config.get("security_audit") if isinstance(config, dict) else None
    images = audit.get("images") if isinstance(audit, dict) else None
    return {
        "run": f"run-{n}",
        "output": str(out),
        "head": head_sha,
        "first": first,
        "profile": profile or ("quick" if first else "standard"),
        "budget": budget if budget is not None else (100 if first else None),
        "scope": "whole" if base is None else f"{base}..{head_sha}",
        "base": base,
        "exclude": exclude,
        "prior_runs": [str(p) for _, p in runs],
        "sandbox": {
            "engine": docker_up(),
            "images": images if isinstance(images, dict) else {},
            "runner": str(Path(__file__).resolve().parent / "sandbox-run.py"),
        },
        "skill": str(skill_dir(harness, os.environ)),
    }, 0


def one_line(value: object) -> str:
    """A text value on one line: breaks and tabs become a space, backticks an apostrophe (a title cannot forge a
    `Fingerprint` marker or a ruling sub-bullet)."""
    return re.sub(r"[\r\n\t]+", " ", str(value)).replace("`", "'").strip()


def hardening_count(report: Path) -> int:
    """Top-level `- ` bullets under the first heading (any level) of `report` whose text starts with `Hardening`."""
    try:
        lines = report.read_text().splitlines()
    except (OSError, ValueError):
        return 0
    count = 0
    inside = False
    for line in lines:
        if line.startswith("#"):
            if inside:
                break
            inside = line.lstrip("#").strip().startswith("Hardening")
        elif inside and line.startswith("- "):
            count += 1
    return count


def validate_run(skill: Path, run: Path) -> str | None:
    """None when both validators pass, else the tail of what went wrong."""
    for script, target in (("validate-findings.cjs", "findings.json"), ("validate-coverage-ledger.cjs", "coverage-ledger.json")):
        try:
            done = subprocess.run(["node", str(skill / script), str(run / target)], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"{script}: {type(exc).__name__}"
        if done.returncode != 0:
            return (done.stderr or done.stdout)[-500:] or f"{script} exited {done.returncode}"
    return None


def add_lines(lines: list[str], heading: str, new: list[str], blank_before: bool = False) -> None:
    """Insert `new` after the last non-blank line of `## <heading>`; a missing section is appended at the end."""
    start = next((i for i, line in enumerate(lines) if line.startswith("## ") and line[3:].strip() == heading), None)
    if start is None:
        lines.extend(["", f"## {heading}", "", *new])
        return
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    last = max((i for i in range(start + 1, end) if lines[i].strip()), default=start)
    block = ["", *new] if blank_before else new
    lines[last + 1:last + 1] = block
    if end == last + 1 and end < len(lines):  # the section ended right at the next heading
        lines.insert(last + 1 + len(block), "")


def record(repo: Path, notes: Path, run: Path, base: str | None, harness: str, env: Mapping[str, str]) -> tuple[dict, int]:
    """Validate a finished audit run, then write its Deferred lines, Gardener line and record line in one write of the
    project note. Exit 0, else 1 with `error` and the note untouched."""
    run = run.absolute()
    audits = (notes / "security-audit").absolute()
    if run.parent.resolve() != audits.resolve() or not re.fullmatch(r"run-\d+", run.name):
        return {"error": "bad-run"}, 1
    note = project_note(notes)
    if note is None:
        return {"error": "no-project-note"}, 1
    if base is not None and not re.fullmatch(r"[0-9a-f]{40}", base):
        return {"error": "bad-base"}, 1
    try:
        meta = json.loads((run / "run-metadata.json").read_text())
        status = meta.get("run_status")
        if status != "complete":
            return {"error": "run-incomplete", "run_status": status}, 1
        sha = meta["source_ref"]["commit"]
        profile = meta["profile"]
        if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) and isinstance(profile, str) and re.fullmatch(r"\S+", profile)):
            raise ValueError("bad commit or profile")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {"error": "invalid-run", "tail": f"run-metadata.json: {type(exc).__name__}"}, 1
    if (tail := validate_run(skill_dir(harness, env), run)) is not None:
        return {"error": "invalid-run", "tail": tail}, 1
    try:
        data = json.loads((run / "findings.json").read_text())
    except (OSError, ValueError):
        return {"error": "invalid-run", "tail": "findings.json unreadable"}, 1
    findings = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    verdicts = {"confirmed": "confirmed", "needs_validation": "needs-validation"}
    today = date.today().isoformat()
    name = run.name
    scope = "whole" if base is None else f"{base}..{sha}"
    common = run_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    lock = Path(common or repo) / "dev-loop" / "decide.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            text = note.read_text()
        except (OSError, ValueError) as exc:
            return {"error": f"cannot read {note.name}: {type(exc).__name__}"}, 1
        recorded = [line for line in section(text, "Release audits") if RECORD_RE.match(line.rstrip())]
        if any(RECORD_RE.match(line.rstrip()).group(2) == name for line in recorded):
            return {"error": "already-recorded"}, 1
        current = current_record(text)
        if current is not None:
            expected = expected_base(repo, current["sha"], sha)
            if base != expected:
                return {"error": "bad-base", "expected_base": expected}, 1
        open_prints = {i["fingerprint"] for i in security_items(text)[0] if i["fingerprint"]}
        deferred: list[str] = []
        skipped: list[str] = []
        for item in findings:
            verdict = verdicts.get(item.get("verdict"))
            if verdict is None:
                continue
            fingerprint = one_line(item["fingerprint"]) if item.get("fingerprint") else ""
            if fingerprint and fingerprint in open_prints:
                skipped.append(fingerprint)
                continue
            open_prints.add(fingerprint)
            detail = "FINDINGS-DETAIL.md" if verdict == "confirmed" else "NEEDS-VALIDATION.md"
            blockers = item.get("blockers")
            blocker = one_line(blockers[0]) if verdict == "needs-validation" and isinstance(blockers, list) and blockers and blockers[0] else ""
            parts = [f"{one_line(item.get('title') or 'untitled')}."]
            if fingerprint:
                parts.append(f"Fingerprint `{fingerprint}`.")
            if blocker:
                parts.append(f"Blocker: {blocker}.")
            parts.append(f"Details: security-audit/{name}/{detail}.")
            deferred.append(f"- {today} (security, {verdict}, {name}): {' '.join(parts)}")
        counts = {v: sum(1 for r in findings if r.get("verdict") == v) for v in verdicts}
        hardening = hardening_count(run / "REPORT.md")
        gardener = f"- {today} (security, hardening, {name}): {hardening} hardening notes, see security-audit/{name}/REPORT.md" if hardening else None
        line = (f"- {today} {name} at {sha}: confirmed {counts['confirmed']}, needs validation {counts['needs_validation']}, "
                f"hardening {hardening}, profile {profile}, scope {scope}")
        lines = text.split("\n")
        if deferred:
            add_lines(lines, "Deferred", [f"### Release audit {name}, {today}", "", *deferred], blank_before=True)
        if gardener:
            add_lines(lines, "Gardener", [gardener])
        add_lines(lines, "Release audits", [line])
        out = "\n".join(lines)
        write_atomic(note, out if out.endswith("\n") else out + "\n")
    return {"record": line, "deferred": deferred, "skipped": skipped, "gardener": gardener}, 0


def section_body(text: str, version: str) -> list[str] | None:
    lines = text.split("\n")
    for start, line in enumerate(lines):
        if line.startswith("## [") and line[4:].split("]")[0].removeprefix("v") == version and "]" in line:
            body = []
            for following in lines[start + 1 :]:
                if following.startswith("## "):
                    break
                body.append(following)
            while body and not body[0].strip():
                body.pop(0)
            while body and not body[-1].strip():
                body.pop()
            return body
    return None


def cmd_release_notes(args: argparse.Namespace) -> int:
    match = TAG_RE.fullmatch(args.tag)
    if not match:
        return emit({"error": f"{args.tag} is not vX.Y.Z or vX.Y.Z-rc.N"}, 2)
    version = match.group(1)
    fail = {"ok": False, "tag": args.tag, "version": version}
    try:
        text = (Path(args.repo) / "CHANGELOG.md").read_bytes().decode("utf-8", "replace")
    except OSError:
        return emit({**fail, "reason": "no-changelog"}, 1)
    body = section_body(text, version)
    if body is None:
        return emit({**fail, "reason": "no-section"}, 1)
    if not body:
        return emit({**fail, "reason": "empty-section"}, 1)
    if args.output:
        write_atomic(Path(args.output), "\n".join(body) + "\n")
    return emit({"ok": True, "tag": args.tag, "version": version, "output": args.output, "lines": len(body)}, 0)


def cmd_release_check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    notes = None  # a non-release tag is skipped before any note read, so it needs no notes path
    if args.tag is None or is_release_tag(args.tag):
        try:
            notes = resolve(repo, os.environ, Path.home())
        except NotesPathError as exc:
            return emit({"error": str(exc)}, 1)
    return emit(*check(repo, notes, args.tag or args.sha, args.tag))


def cmd_release_plan(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    return emit(*plan(repo, notes, args.harness, args.profile, args.budget))


def cmd_release_record(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    return emit(*record(repo, notes, Path(args.run_folder), args.base, args.harness, os.environ))

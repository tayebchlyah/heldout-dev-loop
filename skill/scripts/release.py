"""Release security audit: parse the project note (security items, rulings, audit records).

Shared by `devloop.py` (`release-plan`, `release-record`, `release-check`).
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import ci_skip
import release_carry
from autonomy import write_atomic
from devloop import AUDITS, emit, old_layout, security_audit_dir
from merge_check import is_test_or_doc, read_config
from notes_path import NotesPathError, resolve

ITEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \(security, (confirmed|needs-validation), ([A-Za-z0-9._-]+(?: [0-9]+)?)\): (.+)$")
RULING_RE = re.compile(r"^(?: {2,}|\t)\s*- Ruling (\d{4}-\d{2}-\d{2}): (\w+) +\S")
RECORD_RE = re.compile(
    r"^- (\d{4}-\d{2}-\d{2}) (Run \d+) at ([0-9a-f]{40}): confirmed \d+, needs validation \d+, hardening \d+, "
    r"profile \S+, scope (whole|[0-9a-f]{40}\.\.[0-9a-f]{40})$"
)
WAVE_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) (Run \d+) fix wave at ([0-9a-f]{40}): (#[1-9]\d*(?:, #[1-9]\d*)*)$")
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


def deferred_items(text: str) -> tuple[list[dict], list[str]]:
    """(every item in file order, unparsed lines) of `## Deferred`.

    An item is `{line, verdict, source, fingerprint, closed, ruling}`. `closed`: a ruling `accept` or `close` under
    it. `ruling`: `{date, word, carried}` of its last ruling line (`carried`: an automatic `accept carried from the` ruling), else None. A top-level bullet that names security and does
    not match the item format is unparsed.
    """
    items: list[dict] = []
    unparsed: list[str] = []
    current: dict | None = None
    for line in section(text, "Deferred"):
        if line.startswith("- "):
            current = None
            body = CHECKBOX_RE.sub("", line[2:])
            match = ITEM_RE.match(body)
            if match:
                fingerprint = FINGERPRINT_RE.search(match.group(4))
                current = {
                    "line": line.rstrip(),
                    "verdict": match.group(2),
                    "source": match.group(3),
                    "fingerprint": fingerprint.group(1) if fingerprint else None,
                    "closed": False,
                    "ruling": None,
                }
                items.append(current)
            elif SECURITY_RE.search(line):
                unparsed.append(line.rstrip())
        elif line.strip() and not line[0].isspace():
            current = None
        elif current is not None:
            ruling = RULING_RE.match(line)
            if ruling:
                carried = line[ruling.start(2):].startswith("accept carried from the ")
                current["ruling"] = {"date": ruling.group(1), "word": ruling.group(2), "carried": carried}
                current["closed"] = current["closed"] or ruling.group(2) in CLOSING_WORDS
    return items, unparsed


def security_items(text: str) -> tuple[list[dict], list[str]]:
    """(open items, unparsed lines) of `## Deferred`: the items of `deferred_items` that are not closed."""
    items, unparsed = deferred_items(text)
    return [i for i in items if not i["closed"]], unparsed


def current_record(text: str) -> dict | None:
    """`{line, run, sha, scope}` of the last record bullet of `## Release audits`, or None."""
    found = None
    for line in section(text, "Release audits"):
        match = RECORD_RE.match(line.rstrip())
        if match:
            found = {"line": line.rstrip(), "run": match.group(2), "sha": match.group(3), "scope": match.group(4)}
    return found


def current_wave(text: str, run: str) -> dict | None:
    """`{line, run, sha, issues}` of the last fix wave bullet of `## Release audits` for `run`, or None."""
    found = None
    for line in section(text, "Release audits"):
        match = WAVE_RE.match(line.rstrip())
        if match and match.group(2) == run:
            issues = [int(n) for n in re.findall(r"#(\d+)", match.group(4))]
            found = {"line": line.rstrip(), "run": run, "sha": match.group(3), "issues": issues}
    return found


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def names_issue(subject: str, n: int) -> bool:
    return re.search(rf"#{n}(?!\d)", subject) is not None


def code_commit(repo: Path, sha: str) -> bool:
    """True when the commit changes, against its first parent, a path that is neither a test or doc path nor allowed
    by the `ci-skip` rules. A root commit: any path. A bad `version_files`: every non-test, non-doc path."""
    parent = run_git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^1")
    if parent.returncode != 0:
        listed = run_git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", sha)
        return bool(listed.stdout.strip("\0"))
    base = parent.stdout.strip()
    diff = run_git(repo, "diff", "--name-only", "-z", "--no-renames", base, sha)
    paths = [p for p in diff.stdout.split("\0") if p]
    code = [p for p in paths if not is_test_or_doc(p)]
    if not code:
        return False
    try:
        return bool(set(ci_skip.blocking(repo, base, sha, paths)).intersection(code))
    except ci_skip.ConfigError:
        return True


def wave_check(repo: Path, record_sha: str, head: str, issues: list[int]) -> dict | None:
    """Why `record_sha..head` is not a fix wave of `issues`: `foreign-commit` (a code commit that names none of them)
    or `unfixed` (an issue no subject names), else None. Subjects first: paths only for a commit that names none."""
    log = run_git(repo, "log", "--format=%H%x00%s", f"{record_sha}..{head}")
    commits = [line.split("\0", 1) for line in log.stdout.splitlines() if "\0" in line]
    foreign = [{"sha": sha, "subject": subject} for sha, subject in commits
               if not any(names_issue(subject, n) for n in issues) and code_commit(repo, sha)]
    if foreign:
        return {"error": "foreign-commit", "commits": foreign}
    unfixed = sorted({n for n in issues if not any(names_issue(subject, n) for _, subject in commits)})
    if unfixed:
        return {"error": "unfixed", "issues": unfixed}
    return None


def mismatch(notes: Path, record: dict) -> str | None:
    """Why the run folder of the record does not back it, else None."""
    path = notes / AUDITS / record["run"] / "run-metadata.json"
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
    code paths are all a release or kit upgrade that `ci-skip` allows, or kit copies equal to the kit the audited
    commit's own installer builds, at any kit version (#185)."""
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
    kit = {p for p in blocked if p.startswith(KIT_ROOTS) or p == MANIFEST}
    if kit and (ref := kit_reference(repo, record_sha)) is not None:
        blocked -= kit_matches(repo, target, kit, ref)
    return code if blocked.intersection(code) else None


def is_release_tag(name: str) -> bool:
    return fnmatch.fnmatchcase(name, "v*")


def wave_ignored(repo: Path, record_sha: str, wave: dict, target: str) -> str | None:
    """Why `release-check` does not measure from the fix wave: `not-ancestor`, `foreign-commit`, `unfixed` or
    `released: <tag>` (a `v*` tag that holds the wave and points elsewhere than the target), else None."""
    sha = wave["sha"]
    if (sha == record_sha or run_git(repo, "merge-base", "--is-ancestor", record_sha, sha).returncode != 0
            or run_git(repo, "merge-base", "--is-ancestor", sha, target).returncode != 0):
        return "not-ancestor"
    if (refused := wave_check(repo, record_sha, sha, wave["issues"])) is not None:
        return refused["error"]
    for tag in run_git(repo, "tag", "--list", "--sort=refname", "v*", "--contains", sha).stdout.split():
        if run_git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").stdout.strip() != target:
            return f"released: {tag}"
    return None


def check(repo: Path, notes: Path | None, target: str, tag: str | None) -> tuple[dict, int]:
    """The release gate: a current audit record and no open security item. Exit 0 when ok.

    `notes` may be None only for a non-release tag, which is skipped before any note read."""
    resolved = run_git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{target}^{{commit}}")
    if resolved.returncode != 0:
        return {"error": f"cannot resolve {target}"}, 1
    sha = resolved.stdout.strip()
    if tag is not None and not is_release_tag(tag):
        return {"ok": True, "tag": tag, "target": sha, "record": None, "reasons": [], "wave": None, "wave_ignored": None,
                "skipped": "not-a-release-tag"}, 0
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
    wave = ignored = None
    record = current_record(text)
    if record is None:
        reasons.append({"reason": "no-audit"})
    elif (detail := mismatch(notes, record)) is not None:
        reasons.append({"reason": "record-mismatch", "detail": detail})
    else:
        base = record["sha"]
        if (found := current_wave(text, record["run"])) is not None:
            ignored = wave_ignored(repo, base, found, sha)
            if ignored is None:
                wave, base = found["line"], found["sha"]
        if base != sha and (paths := stale_paths(repo, base, sha)) is not None:
            reasons.append({"reason": "stale-audit", "paths": paths})
    items, unparsed = security_items(text)
    if items:
        reasons.append({"reason": "open-security", "items": [i["line"] for i in items]})
    if unparsed:
        reasons.append({"reason": "unparsed-security", "lines": unparsed})
    ok = not reasons
    return {"ok": ok, "tag": tag, "target": sha, "record": record["line"] if record else None, "wave": wave,
            "wave_ignored": ignored, "reasons": reasons}, 0 if ok else 1


def skill_dir(harness: str, env: Mapping[str, str]) -> Path:
    """The `security-audit` skill folder of the harness; `DEVLOOP_TEST_SECURITY_AUDIT_DIR` overrides it (tests)."""
    if env.get("DEVLOOP_TEST_SECURITY_AUDIT_DIR"):
        return Path(env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"])
    home = Path.home()
    return security_audit_dir(harness, env, home)


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


KIT_SOURCE = ("lib", "skill", "skills", "claude", "codex", "package.json")
NODE_TIMEOUT = 120
KIT_SCRIPT = """
import { pathToFileURL } from 'node:url'
const [root, harnesses] = process.argv.slice(1)
const { buildKit } = await import(pathToFileURL(root + '/lib/kit.mjs'))
const { buildManifest } = await import(pathToFileURL(root + '/lib/manifest.mjs'))
const kit = buildKit({ packageRoot: root, harnesses: JSON.parse(harnesses) })
console.log(JSON.stringify({
  files: buildManifest('0', kit).files,
  exec: kit.files.filter((f) => f.executable).map((f) => f.path),
  links: kit.links.map((l) => l.path),
}))
"""


def check_archive(tar: tarfile.TarFile) -> None:
    for member in tar.getmembers():
        name = posixpath.normpath(member.name)
        if name.startswith("/") or name == ".." or name.startswith("../") or not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError(member.name)
        if member.issym() or member.islnk():
            origin = posixpath.dirname(name) if member.issym() else ""
            target = posixpath.normpath(posixpath.join(origin, member.linkname))
            if member.linkname.startswith("/") or target == ".." or target.startswith("../"):
                raise ValueError(member.name)


def kit_reference(repo: Path, commit: str) -> dict[str, tuple[str, str]] | None:
    """The kit that `commit`'s own installer builds, as `{path: (MANIFEST entry, git mode)}`; None when the tree
    at `commit` is not the kit source, its `dev-loop.json` has no `harnesses`, or `git` or `node` fails."""
    config = read_config(repo, commit)
    harnesses = config.get("harnesses") if config else None
    if not (isinstance(harnesses, list) and harnesses and all(isinstance(h, str) for h in harnesses)):
        return None
    package = run_git(repo, "show", f"{commit}:package.json")
    try:
        named = json.loads(package.stdout).get("name") == "dev-loop" if package.returncode == 0 else False
    except (ValueError, AttributeError):
        return None
    if not named or any(run_git(repo, "cat-file", "-e", f"{commit}:lib/{f}").returncode != 0 for f in ("kit.mjs", "manifest.mjs")):
        return None
    tmp = tempfile.mkdtemp(prefix="kit-reference-")
    try:
        archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", commit, "--", *KIT_SOURCE], capture_output=True)
        if archive.returncode != 0:
            return None
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            check_archive(tar)
            tar.extractall(tmp)
        built = subprocess.run(["node", "--input-type=module", "-e", KIT_SCRIPT, tmp, json.dumps(harnesses)],
                               capture_output=True, text=True, timeout=NODE_TIMEOUT)
        if built.returncode != 0:
            return None
        data = json.loads(built.stdout)
        executable, links = set(data["exec"]), set(data["links"])
        return {path: (entry, "120000" if path in links else "100755" if path in executable else "100644")
                for path, entry in data["files"].items()}
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def kit_matches(repo: Path, commit: str, among: set[str], ref: dict[str, tuple[str, str]]) -> set[str]:
    """The paths of `among` under `KIT_ROOTS` whose tree entry at `commit` (MANIFEST entry and git mode) equals
    `ref`, plus `MANIFEST` when it is in `among` and its `files` at `commit` equal the entries of `ref`."""
    tree = run_git(repo, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries = {}
    for line in tree.stdout.split("\0") if tree.returncode == 0 else []:
        meta, _, path = line.partition("\t")
        if path:
            mode, _, blob = meta.split(" ")
            entries[path] = (mode, blob)
    found = set()
    for path in among:
        if path not in entries or path not in ref or not path.startswith(KIT_ROOTS):
            continue
        mode, blob = entries[path]
        read = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", blob], capture_output=True)
        if read.returncode != 0:
            continue
        if mode == "120000":
            actual = "link:" + read.stdout.decode("utf-8", "replace")
        else:
            actual = "sha256:" + hashlib.sha256(read.stdout).hexdigest()
        if (actual, mode) == ref[path]:
            found.add(path)
    manifest = manifest_at(repo, commit)
    if MANIFEST in among and manifest and manifest.get("files") == {p: e for p, (e, _) in ref.items()}:
        found.add(MANIFEST)
    return found


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

    ref = kit_reference(repo, base)
    if ref is None:
        return []
    return sorted(p for p in kit_matches(repo, head, changed, ref) & changed if only_upgrades(p))


def plan(repo: Path, notes: Path | None, harness: str, profile: str | None = None, budget: int | None = None,
         require_sandbox: bool = False) -> tuple[dict, int]:
    """The release-audit plan: run folder (created, the plan in its `plan.json`), scope, sandbox and skill. Exit 0, else 1
    with `error`, nothing created.
    `require_sandbox`: `no-sandbox` (`no-images`, else `no-engine`) after every other check."""
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
    audits = notes.absolute() / AUDITS
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
        (int(m.group(1)), p) for p in audits.iterdir() if p.is_dir() and (m := re.fullmatch(r"Run (\d+)", p.name))
    ) if audits.is_dir() else []
    n = runs[-1][0] + 1 if runs else 1
    out = audits / f"Run {n}"
    top = run_git(repo, "rev-parse", "--show-toplevel").stdout.strip() or str(repo)
    try:
        config = json.loads((Path(top) / "dev-loop.json").read_text())
    except (OSError, ValueError):
        config = {}
    audit = config.get("security_audit") if isinstance(config, dict) else None
    images = audit.get("images") if isinstance(audit, dict) else None
    if require_sandbox and not (isinstance(images, dict) and images):
        return {"error": "no-sandbox", "reason": "no-images"}, 1
    engine = docker_up()
    if require_sandbox and not engine:
        return {"error": "no-sandbox", "reason": "no-engine"}, 1
    try:
        out.mkdir(parents=True)
    except FileExistsError:
        return {"error": "run-folder-exists", "output": str(out)}, 1
    result = {
        "run": f"Run {n}",
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
            "engine": engine,
            "images": images if isinstance(images, dict) else {},
            "runner": str(Path(__file__).resolve().parent / "sandbox-run.py"),
        },
        "skill": str(skill_dir(harness, os.environ)),
    }
    (out / "plan.json").write_text(json.dumps(result))
    return result, 0


def one_line(value: object) -> str:
    """A text value on one line: every line break `str.splitlines()` knows and tabs become a space, backticks an
    apostrophe (a title cannot forge a `Fingerprint` marker or a ruling sub-bullet)."""
    return re.sub(r"[\r\n\t\x0b\x0c\x1c-\x1e\x85  ]+", " ", str(value)).replace("`", "'").strip()


def hardening_count(report: Path) -> int:
    """Top-level `- ` bullets under the first heading (any level) of `report` whose text starts with `Hardening`."""
    try:
        lines = report.read_text().split("\n")
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


def note_lock(repo: Path) -> Path:
    """The lock file every project note write of the release commands holds (`decide` holds it too)."""
    common = run_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    lock = Path(common or repo) / "dev-loop" / "decide.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    return lock


def fix_wave(repo: Path, notes: Path, issues: list[int]) -> tuple[dict, int]:
    """Write the fix wave line of the current record at HEAD. Exit 0, else 1 with `error` and the note untouched."""
    note = project_note(notes)
    if note is None:
        return {"error": "no-project-note"}, 1
    head = run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD^{commit}").stdout.strip()
    with open(note_lock(repo), "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            text = note.read_text()
        except (OSError, ValueError) as exc:
            return {"error": f"cannot read {note.name}: {type(exc).__name__}"}, 1
        current = current_record(text)
        if current is None:
            return {"error": "no-audit"}, 1
        if current_wave(text, current["run"]) is not None:
            return {"error": "wave-exists"}, 1
        if head == current["sha"] or run_git(repo, "merge-base", "--is-ancestor", current["sha"], head).returncode != 0:
            return {"error": "not-descendant"}, 1
        if (refused := wave_check(repo, current["sha"], head, issues)) is not None:
            return refused, 1
        line = f"- {date.today().isoformat()} {current['run']} fix wave at {head}: {', '.join(f'#{n}' for n in sorted(set(issues)))}"
        lines = text.split("\n")
        add_lines(lines, "Release audits", [line])
        out = "\n".join(lines)
        write_atomic(note, out if out.endswith("\n") else out + "\n")
    return {"wave": line}, 0


def record(repo: Path, notes: Path, run: Path, base: str | None, harness: str, env: Mapping[str, str]) -> tuple[dict, int]:
    """Validate a finished audit run, then write its Deferred lines, Gardener line and record line in one write of the
    project note. Exit 0, else 1 with `error` and the note untouched."""
    run = run.absolute()
    audits = (notes / AUDITS).absolute()
    if run.parent.resolve() != audits.resolve() or not re.fullmatch(r"Run \d+", run.name):
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
    with open(note_lock(repo), "w") as held:
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
        garden_prints = {m.group(1) for line in section(text, "Gardener") if (m := FINGERPRINT_RE.search(line))}
        deferred: list[str] = []
        block: list[str] = []
        carried: list[dict] = []
        reopened: list[dict] = []
        low: list[str] = []
        skipped: list[str] = []
        for item in findings:
            verdict = verdicts.get(item.get("verdict"))
            if verdict is None:
                continue
            severity = item.get("severity")
            is_low = verdict == "confirmed" and isinstance(severity, dict) and severity.get("overall_severity") in ("low", "informational")
            fingerprint = one_line(item["fingerprint"]) if item.get("fingerprint") else ""
            if fingerprint and (fingerprint in open_prints or is_low and fingerprint in garden_prints):
                skipped.append(fingerprint)
                continue
            (garden_prints if is_low else open_prints).add(fingerprint)
            detail = "FINDINGS-DETAIL.md" if verdict == "confirmed" else "NEEDS-VALIDATION.md"
            blockers = item.get("blockers")
            blocker = one_line(blockers[0]) if verdict == "needs-validation" and isinstance(blockers, list) and blockers and blockers[0] else ""
            parts = [f"{one_line(item.get('title') or 'untitled')}."]
            if fingerprint:
                parts.append(f"Fingerprint `{fingerprint}`.")
            if blocker:
                parts.append(f"Blocker: {blocker}.")
            parts.append(f"Details: {AUDITS}/{name}/{detail}.")
            if is_low:
                low.append(f"- {today} (security, low, {name}): {' '.join(parts)}")
            else:
                deferred.append(f"- {today} (security, {verdict}, {name}): {' '.join(parts)}")
                block.append(deferred[-1])
                found = release_carry.carry(repo, text, fingerprint, item.get("trace"), sha, today) if verdict == "needs-validation" and fingerprint else None
                if found is not None:
                    (carried if found[0] == "carried" else reopened).append(found[1])
                    block.extend([found[2]] if found[2] else [])
        counts = {v: sum(1 for r in findings if r.get("verdict") == v) for v in verdicts}
        hardening = hardening_count(run / "REPORT.md")
        gardener = f"- {today} (security, hardening, {name}): {hardening} hardening notes, see {AUDITS}/{name}/REPORT.md" if hardening else None
        line = (f"- {today} {name} at {sha}: confirmed {counts['confirmed']}, needs validation {counts['needs_validation']}, "
                f"hardening {hardening}, profile {profile}, scope {scope}")
        lines = text.split("\n")
        if deferred:
            add_lines(lines, "Deferred", [f"### Release audit {name}, {today}", "", *block], blank_before=True)
        if low or gardener:
            add_lines(lines, "Gardener", [*low, *([gardener] if gardener else [])])
        add_lines(lines, "Release audits", [line])
        out = "\n".join(lines)
        write_atomic(note, out if out.endswith("\n") else out + "\n")
    return {"record": line, "deferred": deferred, "carried": carried, "reopened": reopened, "low": low, "skipped": skipped,
            "gardener": gardener}, 0


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
    if notes is not None and "security-audit" in old_layout(notes):
        return emit({"error": "old-notes-layout", "fix": "devloop.py notes-migrate"}, 1)
    return emit(*check(repo, notes, args.tag or args.sha, args.tag))


def cmd_release_plan(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    if "security-audit" in old_layout(notes):
        return emit({"error": "old-notes-layout", "fix": "devloop.py notes-migrate"}, 1)
    return emit(*plan(repo, notes, args.harness, args.profile, args.budget, args.require_sandbox))


def cmd_release_record(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    if "security-audit" in old_layout(notes):
        return emit({"error": "old-notes-layout", "fix": "devloop.py notes-migrate"}, 1)
    if args.fix_wave:
        return emit(*fix_wave(repo, notes, args.issues))
    return emit(*record(repo, notes, Path(args.run_folder), args.base, args.harness or "claude", os.environ))

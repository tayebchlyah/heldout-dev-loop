#!/usr/bin/env python3
"""dev-loop CLI. Each command prints one JSON object on stdout.

  devloop.py status [--repo PATH] [--feature SLUG]   stage of the repo or of one feature
  devloop.py merge-check [--repo PATH] [--feature SLUG]   auto or human merge, with reasons
  devloop.py logged [--name NAME] -- CMD...          run CMD, full output to a log file
  devloop.py gate --summary                           run the gate through `logged`, summarize it
  devloop.py ci wait [--pr N] [--timeout MIN]         wait for the PR checks
  devloop.py board [--repo PATH] [--event E] [--claim SLUG] [--no-serve] [--stop-server]
                                                      write the status notes of this machine
  devloop.py seal --feature SLUG --message SUBJECT [--reverts SHA ...]
                                                      commit the staged acceptance tests with the seal trailer
  devloop.py seal --rebase-continue                   write the new seal into the rebase commit, continue it
  devloop.py audit                                    SubagentStop hook JSON on stdin: record a read of a protected file
  devloop.py seal-check [--uncommitted]               the acceptance tests on the branch against the seal commits
  devloop.py verify-init --base-url URL [--name SKILL]
                                                      write the verify skill skeleton and the verify key
  devloop.py drive [--all | --since SHA | --routes-from FILE]
                                                      drive the feature map routes the branch changed, save the evidence
  devloop.py capture --pr N [--before FILE] [--after FILE] | --write-after FILE
                                                      put the before and after screens in the PR body
  devloop.py second-review --harness claude|codex [--timeout MINUTES]
  devloop.py review-merge REVIEWER=FILE [REVIEWER=FILE ...]
                                                      merge the reviewer tables by file and line
  devloop.py acceptance [--failed]                    run the acceptance command, read its JUnit, record the run
  devloop.py release-plan [--repo PATH] [--harness claude|codex] [--profile quick|standard|deep] [--budget N]
                                                      the release audit plan: run folder, scope, sandbox, skill
  devloop.py release-record --run PATH [--base SHA] [--harness claude|codex] [--repo PATH]
                                                      record a finished release audit run in the project note
  devloop.py release-check [--repo PATH] (--tag NAME | --sha REV)
                                                      the release gate: a current audit record, no open security item
  devloop.py metrics [--repo PATH] [--feature SLUG] [--write]
                                                      the token cost of each stage of a feature, its row in Feature Metrics.md
  devloop.py sessions --since DAYS [--repo PATH]       the Claude Code and Codex sessions of the repo, for retro
  devloop.py decide --kind decision|discovery --what T --why T --evidence T [--result T] [--risk] [--feature SLUG]
                                                      append one row to the decision log
  devloop.py pause [--event] [--intent T] [--verified T] [--open T] [--next T] [--agent ID=TASK ...] [--run LOG ...]
  devloop.py pause --clear                            write or delete the pause note of the worktree

Exit 0 on success, 1 on a failure the JSON describes, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Write no __pycache__ into the skill folder: in a project it is a vendored kit folder.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
import autonomy  # noqa: E402
from notes_path import NotesPathError, resolve  # noqa: E402

JUNIT_HINT = (
    "No JUnit XML in $DEVLOOP_REPORT_DIR. Make the gate write it there: "
    'pytest --junitxml="$DEVLOOP_REPORT_DIR/pytest.xml", '
    'vitest run --reporter=junit --outputFile="$DEVLOOP_REPORT_DIR/vitest.xml", '
    'gotestsum --junitfile "$DEVLOOP_REPORT_DIR/go.xml"'
)


def emit(payload: dict, code: int) -> int:
    print(json.dumps(payload))
    return code


GIT_CACHE: dict[tuple[str, ...], str | None] | None = None


def cache_git() -> None:
    """From now on in this process, a repeated `git` call returns its first answer. For `board`, which makes no git write."""
    global GIT_CACHE
    GIT_CACHE = {}


def git(repo: Path, *args: str) -> str | None:
    key = (str(repo), *args)
    if GIT_CACHE is not None and key in GIT_CACHE:
        return GIT_CACHE[key]
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    out = result.stdout.strip() if result.returncode == 0 else None
    if GIT_CACHE is not None:
        GIT_CACHE[key] = out
    return out


# status


def slug_from_title(filename: str) -> str:
    stem = re.sub(r"^\d{8} ", "", re.sub(r"\.md$", "", filename))
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if sep and key and not key[0].isspace():
            fields[key.strip()] = value.strip().strip("\"'")
    return {}


def specs(notes: Path) -> list[tuple[str, Path]]:
    found = []
    for path in sorted((notes / "specs").glob("*.md")):
        found.append(((frontmatter(path).get("feature") or slug_from_title(path.name)).lower(), path))
    return found


def find_plan(notes: Path, spec: Path) -> Path | None:
    plans = [p for p in sorted((notes / "plans").glob("*.md")) if frontmatter(p).get("spec") in (spec.name, spec.stem)]
    return plans[-1] if plans else None


def rebasing(worktree: Path) -> str | None:
    """The branch a rebase in progress in the worktree rewrites ("" when unknown), or None with no rebase."""
    git_dir = git(worktree, "rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return None
    for name in ("rebase-merge", "rebase-apply"):
        state = Path(git_dir) / name
        if state.is_dir():
            head = state / "head-name"
            return head.read_text().strip().removeprefix("refs/heads/") if head.is_file() else ""
    return None


def feature_checkout(repo: Path, slug: str) -> tuple[Path | None, str | None]:
    """The worktree and the branch `<prefix>/<slug>`. A branch with no worktree gives (None, branch).
    A worktree mid-rebase is detached: its branch comes from the rebase state. Branch case is ignored."""
    worktree: Path | None = None
    for line in (git(repo, "worktree", "list", "--porcelain") or "").splitlines():
        if line.startswith("worktree "):
            worktree = Path(line[len("worktree "):])
        elif line.startswith("branch refs/heads/") and line.lower().endswith(f"/{slug}"):
            return worktree, line[len("branch refs/heads/"):]
        elif line == "detached" and worktree and (rebasing(worktree) or "").lower().endswith(f"/{slug}"):
            return worktree, rebasing(worktree)
    for branch in (git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads") or "").splitlines():
        if branch.lower().endswith(f"/{slug}"):
            return None, branch
    return None, None


def cherry(repo: Path, branch: str) -> list[str] | None:
    """`git cherry origin/main <branch>` lines, or None when it fails (no origin/main, for one)."""
    out = git(repo, "cherry", "origin/main", branch)
    return None if out is None else out.splitlines()


def holds_slug(path: str, slug: str) -> bool:
    """An acceptance path (relative to the acceptance folder) that holds the slug as a whole token.
    `-` and `_` count as the same character."""
    token = re.escape(slug.replace("_", "-"))
    return re.search(rf"(?<![a-z0-9]){token}(?![a-z0-9])", path.lower().replace("_", "-")) is not None


def acceptance_commit(repo: Path, slug: str, revs: list[str]) -> bool:
    """A commit in `revs` that touches the acceptance folder and carries the line `Acceptance: <slug>` in its
    message (Stage 3 may only extend earlier features' files). The slug matches whole."""
    out = git(repo, "log", "-1", "--format=%H", "-i", "-E", "--grep", f"^Acceptance: {re.escape(slug)}$",
              *revs, "--", "tests/acceptance")
    return bool(out)


def has_acceptance(worktree: Path, slug: str) -> bool:
    """A file named after the slug, else an acceptance commit anywhere in the history of HEAD."""
    root = worktree / "tests" / "acceptance"
    if any(path.is_file() and holds_slug(str(path.relative_to(root)), slug) for path in root.rglob("*")):
        return True
    return acceptance_commit(worktree, slug, ["HEAD"])


def remote_branch(repo: Path, slug: str) -> str | None:
    """`origin/<prefix>/<slug>`, or None."""
    remotes = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin") or ""
    return next((ref for ref in remotes.splitlines() if ref.lower().endswith(f"/{slug}") and ref.count("/") >= 2), None)


def shipped_without_branch(repo: Path, slug: str) -> bool:
    """An acceptance file on the default branch, or an acceptance commit on it or on origin's. For a feature with
    no branch, local or remote."""
    prefix = "tests/acceptance/"
    files = git(repo, "ls-tree", "-r", "--name-only", DEFAULT_BRANCH, prefix) or ""
    if any(holds_slug(f[len(prefix):], slug) for f in files.splitlines() if f.startswith(prefix)):
        return True
    refs = [ref for ref in (DEFAULT_BRANCH, f"origin/{DEFAULT_BRANCH}")
            if git(repo, "rev-parse", "--verify", "-q", ref)]
    return bool(refs) and acceptance_commit(repo, slug, refs)


def result(stage: int | str, check: str, next_: str, feature: str | None = None,
           track: str | None = None, worktree: Path | None = None) -> dict:
    return {
        "stage": stage,
        "check": check,
        "next": next_,
        "feature": feature,
        "track": track,
        "worktree": str(worktree) if worktree else None,
    }


FETCH = "git fetch origin, then run devloop status again"
REBASE = "finish or abort the rebase, then rerun status"
DEFAULT_BRANCH = "main"
SKIP_ACCEPTANCE = ("regression", "none")  # Spec `acceptance:` values with no Stage 3: the slice changes no flow.


def detect_feature(repo: Path, slug: str, notes: Path) -> dict:
    slug = slug.lower()  # The one slug normalization: spec names, branches and acceptance paths compare in lower case.
    worktree, branch = feature_checkout(repo, slug)
    if worktree is not None and rebasing(worktree) is not None:
        return result("rebase", f"rebase in progress in {worktree}", REBASE, slug, None, worktree)
    matches = [path for name, path in specs(notes) if name == slug]
    if not matches:
        if branch is None:
            return result(1, f"no spec and no branch for {slug}", "Stage 1 Frame", slug)
        lines = cherry(repo, branch)
        if lines is None:
            return result(5, "Track S: git cherry origin/main fails", FETCH, slug, "S", worktree)
        if not lines:
            return result(5, f"Track S: {branch} has no commit ahead of origin/main", "Stage 5 Build", slug, "S", worktree)
        if any(line.startswith("+") for line in lines):
            git_dir = git(worktree, "rev-parse", "--absolute-git-dir") if worktree else None
            marker = Path(git_dir) / "dev-loop" / "review-done" if git_dir else None
            if marker is None or not marker.is_file():
                return result(6, "Track S: no review-done marker in the git dir", "Stage 6 review wave, triage, fix wave, "
                              "then write the review-done marker", slug, "S", worktree)
            return result(7, f"Track S: {branch} has commits not on origin/main", "Stage 7 Ship", slug, "S", worktree)
        return result("shipped", f"git cherry origin/main {branch} lists no + commit", "none", slug, "S", worktree)

    spec = matches[-1]
    fields = frontmatter(spec)
    track = fields.get("track") or None

    def at(stage: int | str, check: str, next_: str) -> dict:
        return result(stage, check, next_, slug, track, worktree)

    if fields.get("shipped"):
        return at("shipped", f"spec {spec.name} has shipped: {fields['shipped']}", "none")
    if fields.get("review_status") != "approved":
        return at(2, f"spec {spec.name} review_status is not approved", f"Stage 2 Spec: finish {spec} and get it approved")
    known = branch or remote_branch(repo, slug)
    if known is None and shipped_without_branch(repo, slug):
        return at("shipped", f"no branch for {slug}, its acceptance file or `Acceptance: {slug}` commit is on {DEFAULT_BRANCH}", "none")
    lines = cherry(repo, known) if known else None
    if lines and not any(line.startswith("+") for line in lines):
        return at("shipped", f"git cherry origin/main {known} lists no + commit", "none")
    if worktree is None or branch is None:  # A worktree always comes with its branch.
        return at(3, f"no worktree on a branch <prefix>/{slug}", f"Stage 3 Acceptance: create the feature worktree for {slug}")
    if fields.get("acceptance", "").lower() not in SKIP_ACCEPTANCE:
        if not has_acceptance(worktree, slug):
            return at(3, f"no acceptance file for {slug} and no commit under tests/acceptance with the line "
                      f"`Acceptance: {slug}`",
                      f"Stage 3 Acceptance: dispatch spec-tester; the commit has the line `Acceptance: {slug}`")
    plan = find_plan(notes, spec)
    if plan is None:
        return at(4, f"no plan with spec: {spec.name}", "Stage 4 Plan")
    ledger = worktree / ".superpowers" / "sdd" / plan.stem / "progress.md"
    if not ledger.is_file():
        next_ = f"Stage 5 Build: run {plan}"
        if any(line.startswith("+") for line in cherry(repo, branch) or []):
            next_ += ". The branch has commits ahead of origin/main: rebuild the ledger from the plan and git log first"
        return at(5, f"no ledger {ledger}", next_)
    text = ledger.read_text()
    tasks = sorted({int(n) for n in re.findall(r"^#{2,3} Task (\d+)\b", plan.read_text(), re.MULTILINE)})
    missing = [n for n in tasks if not re.search(rf"^Task {n}: complete", text, re.MULTILINE)]
    if missing:
        return at(5, f"ledger has no 'Task <N>: complete' line for task {', '.join(map(str, missing))}", f"Stage 5 Build: run {plan}")
    if not re.search(r"^Feature gate: green at", text, re.MULTILINE):
        return at(6, "no 'Feature gate: green at' line in the ledger", "Stage 6 Gate")
    lines = cherry(repo, branch)
    if lines is None:
        return at(7, "git cherry origin/main fails", FETCH)
    if any(line.startswith("+") for line in lines):
        return at(7, f"git cherry origin/main {branch} lists a + commit", "Stage 7 Ship")
    return at("shipped", f"git cherry origin/main {branch} lists no + commit", "none")


def detect(repo: Path, feature: str | None, notes: Path) -> dict:
    top = git(repo, "rev-parse", "--show-toplevel")
    if top is None or git(repo, "rev-parse", "HEAD") is None:
        return result(0, "git rev-parse HEAD fails", "Stage 0 Bootstrap: git init and a first commit")
    if not any((Path(top) / name).is_file() for name in ("GLOSSARY.md", "CONTEXT.md")):
        return result(0, "no glossary: GLOSSARY.md or CONTEXT.md", "Stage 0 Bootstrap: write GLOSSARY.md")
    if feature is None:
        branch = git(repo, "symbolic-ref", "--short", "-q", "HEAD")
        if branch and "/" in branch:
            return detect_feature(repo, branch.rsplit("/", 1)[1], notes)
        head = None if branch else rebasing(repo)
        if head is not None:
            if "/" in head:
                return detect_feature(repo, head.rsplit("/", 1)[1], notes)
            return result("rebase", "rebase in progress", REBASE)
        features = []
        for slug, spec in dict(specs(notes)).items():
            # Approved, no branch, no worktree: left out, no stage computed. `--feature <slug>` still resolves it.
            if (frontmatter(spec).get("review_status") == "approved"
                    and feature_checkout(repo, slug) == (None, None) and remote_branch(repo, slug) is None):
                continue
            found = detect_feature(repo, slug, notes)
            if found["stage"] != "shipped":
                features.append({"feature": slug, "stage": found["stage"], "worktree": found["worktree"]})
        next_ = "Stage 1 Frame for new work"
        if features:
            next_ += ", or run devloop status --feature <slug> for an open feature"
        payload = result(1, f"on {branch or 'a detached HEAD'} with no --feature", next_)
        payload["features"] = features
        return payload
    return detect_feature(repo, feature, notes)


PAUSED_NEXT = "resume from the pause note"


def pause_state(worktree: Path, env: Mapping[str, str], delete: bool) -> dict | None:
    """The pause note of a worktree. None when there is none, or when the current session wrote it on a limit or an
    error (not `manual`): that session wakes its own work, so nothing is lost; `delete` then removes it."""
    git_dir = git(worktree, "rev-parse", "--absolute-git-dir")
    note = autonomy.read_note(Path(git_dir)) if git_dir else None
    if note is None or git_dir is None:
        return None
    session, _ = autonomy.session_id(env)
    if session and note.get("reason") != "manual" and note.get("session") == session:
        if delete:
            autonomy.note_path(Path(git_dir)).unlink(missing_ok=True)
        return None
    return note


def cmd_status(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit(result(0, "no notes path", str(exc)), 0)
    out = detect(repo, args.feature, notes)
    # Only `status` deletes a note: `board`, `decide` and `pause` run in the same session and must keep it.
    top = git(repo, "rev-parse", "--show-toplevel")
    where = out["worktree"] or (top if args.feature is None else None)
    note = pause_state(Path(where), os.environ, delete=True) if where else None
    if note is not None:
        out["paused"], out["next"] = note, PAUSED_NEXT
    for item in out.get("features", []):
        if item["worktree"] and pause_state(Path(item["worktree"]), os.environ, delete=True) is not None:
            item["paused"] = True
    if "features" in out:
        import retro

        due = retro.review_due(repo, notes, date.today())
        if due is not None:
            out["review_due"] = due
    # Lazy, as in cmd_board: the old tests load this file with importlib and must not need the board package.
    sys.path.insert(0, str(Path(__file__).parent))
    from board import state, store, writer

    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    last = state.load(Path(common) / "dev-loop")["last_run"] if common else None
    last = last if isinstance(last, dict) else {}
    skipped = last.get("skipped")
    if isinstance(skipped, list) and any(
            isinstance(s, dict) and s.get("reason") not in ("busy", "rebase", "no-dev-loop", "stage-0")
            for s in skipped):
        out["board"] = {"at": last.get("at"), "skipped": skipped}
    first = next((line[len("worktree "):] for line in (git(repo, "worktree", "list", "--porcelain") or "").splitlines()
                  if line.startswith("worktree ")), None)
    if top and first:
        have, main = writer.kit_version(Path(top)), writer.kit_version(Path(first))
        if store.version_key(have) > store.version_key(main):
            out["kit_drift"] = {"worktree": have, "main": main}
    return emit(out, 0)


# merge-check


def main_ref(repo: Path) -> str | None:
    """The fully qualified ref to read the trusted config from. A short `origin/main` can be shadowed by a local
    tag or branch of that name."""
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        if git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"):
            return ref
    return None


TEST_OR_DOC_NAMES = ("test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*", "README.md", "CHANGELOG.md")


def is_test_or_doc(path: str) -> bool:
    """A segment `tests`, `test` or `docs`, or a test or doc file name. Kit Markdown is code: it is behaviour."""
    *dirs, name = path.split("/")
    return bool({"tests", "test", "docs"} & set(dirs)) or any(fnmatch.fnmatchcase(name, p) for p in TEST_OR_DOC_NAMES)


def read_config(repo: Path, rev: str) -> dict | None:
    """`dev-loop.json` at `rev` as an object, or None when absent, not JSON or not an object."""
    try:  # Bytes: a copy that is not UTF-8 does not parse (UnicodeDecodeError is a ValueError).
        config = json.loads(subprocess.run(["git", "-C", str(repo), "show", f"{rev}:dev-loop.json"],
                                           capture_output=True, check=True).stdout)
    except (subprocess.CalledProcessError, ValueError):
        return None
    return config if isinstance(config, dict) else None


MERGE_VALUES = ("auto", "human")


def config_shape_ok(config: dict) -> bool:
    """A known `merge`, and `merge_human_paths` items non-empty after strip and not ending in `/` (a directory
    pattern never matches under fnmatchcase: write `src/auth/*`)."""
    paths = config.get("merge_human_paths", [])
    return (config.get("merge", "auto") in MERGE_VALUES and isinstance(paths, list)
            and all(isinstance(p, str) and p.strip() and not p.endswith("/") for p in paths))


class GitFailed(Exception):
    """A merge-check git call failed: the command exits 1 with `error`, no `merge` value (fail closed)."""


def git_out(repo: Path, *args: str) -> str:
    """Like `git`, for merge-check: a failure raises GitFailed instead of reading as empty, and bytes that are not
    UTF-8 (a branch path name) read as U+FFFD instead of crashing."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode != 0:
        raise GitFailed(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout.decode(errors="replace")


def changed_paths(repo: Path, *revs: str) -> list[str]:
    """`git diff --name-only --no-renames`, sorted: a rename counts as both paths. `--ignore-submodules=none`: a
    `.gitmodules` or user `ignore = all` cannot hide a submodule pointer move."""
    out = git_out(repo, "diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", *revs)
    return sorted(p for p in out.split("\0") if p)


def patch_id(repo: Path, commit: str) -> str | None:
    """`git patch-id --verbatim` (stable, whitespace kept) of the commit against its first parent: "" for an empty
    diff, None when git fails (no first parent, for one). The diff flags keep `.gitattributes` and user config from
    rendering two different changes the same way."""
    diff = subprocess.run(["git", "-C", str(repo), "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                           "--no-renames", "--ignore-submodules=none", "-a", f"{commit}^1", commit], capture_output=True)
    if diff.returncode != 0:
        return None
    out = subprocess.run(["git", "-C", str(repo), "patch-id", "--verbatim"], input=diff.stdout,
                         capture_output=True)
    if out.returncode != 0:
        return None
    return out.stdout.split()[0].decode() if out.stdout.split() else ""


def reviewed_head(worktree: Path | None, notes: Path, spec: Path | None) -> str | None:
    """The sha of the last `Feature gate: green at` ledger line (Tracks M and L), else of the `review-done` marker."""
    if worktree is None:
        return None
    if spec is not None:
        plan = find_plan(notes, spec)
        ledger = worktree / ".superpowers" / "sdd" / plan.stem / "progress.md" if plan else None
        found = re.findall(r"^Feature gate: green at (\S+)", ledger.read_text(errors="replace"), re.MULTILINE) \
            if ledger and ledger.is_file() else []
        return found[-1] if found else None
    git_dir = git(worktree, "rev-parse", "--absolute-git-dir")
    marker = Path(git_dir) / "dev-loop" / "review-done" if git_dir else None
    lines = marker.read_text(errors="replace").splitlines() if marker and marker.is_file() else []
    return (lines[0].strip() or None) if lines else None


def cmd_merge_check(args: argparse.Namespace) -> int:
    try:
        return merge_check(args)
    except GitFailed as exc:
        return emit({"error": str(exc)}, 1)


def merge_check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    slug = args.feature.lower() if args.feature else None
    if slug is None:
        branch = git(repo, "symbolic-ref", "--short", "-q", "HEAD") or ""
        if "/" not in branch:
            return emit({"error": "no feature: run merge-check on a feature branch or pass --feature"}, 1)
        slug = branch.rsplit("/", 1)[1].lower()
    worktree, branch = feature_checkout(repo, slug)
    if branch is None:
        return emit({"error": f"no feature {slug}: no branch <prefix>/{slug}"}, 1)
    head = (git_out(worktree, "rev-parse", "HEAD") if worktree
            else git_out(repo, "rev-parse", f"refs/heads/{branch}")).strip()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    matches = [path for name, path in specs(notes) if name == slug]
    spec = matches[-1] if matches else None
    track = (frontmatter(spec).get("track") or None) if spec else "S"
    ref = main_ref(repo)
    if ref is None:
        return emit({"error": "no refs/remotes/origin/main and no refs/heads/main"}, 1)

    reasons: list[dict] = []
    config = read_config(repo, ref)
    if config is not None and not config_shape_ok(config):
        config = None  # Fail closed: an unknown value must not merge without a human.
    if config is None:
        reasons.append({"reason": "config-unreadable"})
    elif config.get("merge") == "human":
        reasons.append({"reason": "merge-human"})
    if (track or "").upper() == "L":
        reasons.append({"reason": "track-l"})
    if config is not None:
        patterns = config.get("merge_human_paths", [])
        for path in changed_paths(repo, f"{ref}...{head}"):
            pattern = next((p for p in patterns if fnmatch.fnmatchcase(path, p)), None)
            if pattern is not None:
                reasons.append({"reason": "human-path", "path": path, "pattern": pattern})
        trusted = (config.get("merge"), config.get("merge_human_paths"))
        branch_config = read_config(repo, head)
        if branch_config is None:
            changed = trusted != (None, None)
        else:
            changed = (branch_config.get("merge"), branch_config.get("merge_human_paths")) != trusted
        if changed:
            reasons.append({"reason": "config-changed"})

    reviewed = reviewed_head(worktree, notes, spec)
    # Only a sha counts, resolved to the full sha in the feature worktree: a ref name would follow later commits.
    if reviewed is not None:
        reviewed = git(worktree, "rev-parse", "--verify", "-q", f"{reviewed}^{{commit}}") \
            if re.fullmatch(r"[0-9a-f]{7,40}", reviewed) else None
    minor: list[dict] = []
    if reviewed is None:
        reasons.append({"reason": "no-review"})
    else:
        base = git(repo, "merge-base", reviewed, ref)
        reviewed_pids = {c: patch_id(repo, c) for c in
                         git_out(repo, "rev-list", "--reverse", f"{base}..{reviewed}" if base else reviewed).split()}
        seen = set(reviewed_pids.values()) - {None, ""}
        landing: set[str | None] = set()
        for commit in git_out(repo, "rev-list", "--reverse", f"{ref}..{head}").split():
            pid = patch_id(repo, commit)
            landing.add(pid)
            if pid == "" or pid in seen:
                continue
            if pid is None:  # Fail closed: a diff git cannot compute is unreviewed, whatever its paths.
                try:
                    paths = changed_paths(repo, f"{commit}^1", commit)
                except GitFailed:
                    paths = []
                reasons.append({"reason": "unreviewed", "commit": commit, "paths": paths})
                continue
            paths = changed_paths(repo, f"{commit}^1", commit)
            code = [p for p in paths if not is_test_or_doc(p)]
            if code:
                reasons.append({"reason": "unreviewed", "commit": commit, "paths": code})
            else:
                minor.append({"commit": commit, "paths": paths})
        # `git cherry` marks `-` a reviewed commit main already holds: dropping it in a rebase is fine.
        cherry_out = git_out(repo, "cherry", ref, reviewed, *([base] if base else []))
        upstream = {line[2:] for line in cherry_out.splitlines() if line.startswith("- ")}
        for commit, pid in reviewed_pids.items():
            if pid and pid not in landing and commit not in upstream:
                reasons.append({"reason": "dropped", "commit": commit})
    return emit({"merge": "human" if reasons else "auto", "reasons": reasons, "reviewed": reviewed,
                 "unreviewed_minor": minor, "feature": slug, "track": track}, 0)


# logged and gate


def log_path(cwd: Path, name: str) -> Path | None:
    git_dir = git(cwd, "rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return None
    logs = Path(git_dir) / "dev-loop" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = f"{name}-{datetime.now():%Y%m%dT%H%M%S}"
    log, n = logs / f"{stamp}.log", 1
    while True:
        try:
            log.open("x").close()
            return log
        except FileExistsError:
            n += 1
            log = logs / f"{stamp}-{n}.log"


def run_logged(cmd: list[str], log: Path, cwd: Path, env: dict[str, str] | None = None) -> dict:
    start = time.monotonic()
    with log.open("w") as out:
        try:
            code = subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT).returncode
        except FileNotFoundError as exc:
            out.write(f"{exc}\n")
            code = 127
        except PermissionError as exc:
            out.write(f"{exc}\n")
            code = 126
    return {"exit": code, "log": str(log), "seconds": round(time.monotonic() - start, 1)}


def parse_failures(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        if line.startswith(("FAILED ", "ERROR ", "--- FAIL")) or " FAIL " in line:
            line = line.strip()
            if line not in found:
                found.append(line)
    return found


def slowest(report_dir: Path, n: int = 10) -> list[dict]:
    cases = []
    for path in sorted(report_dir.glob("*.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            try:
                seconds = float(case.get("time") or 0)
            except ValueError:
                continue
            name = case.get("name", "")
            classname = case.get("classname")
            cases.append({"name": f"{classname}::{name}" if classname else name, "seconds": seconds})
    return sorted(cases, key=lambda c: c["seconds"], reverse=True)[:n]


def cmd_logged(args: argparse.Namespace) -> int:
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        return emit({"error": "no command after --"}, 2)
    cwd = Path.cwd()
    log = log_path(cwd, args.name or Path(cmd[0]).name)
    if log is None:
        return emit({"error": "not a git repository"}, 2)
    payload = run_logged(cmd, log, cwd)
    payload["tail"] = log.read_text(errors="replace").splitlines()[-20:]
    return emit(payload, 0 if payload["exit"] == 0 else 1)


def mapped_command(top: Path, key: str) -> str | None:
    try:
        command = json.loads((top / "dev-loop.json").read_text()).get("commands", {}).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    return command if isinstance(command, str) and command else None


def cmd_gate(args: argparse.Namespace) -> int:
    top_out = git(Path.cwd(), "rev-parse", "--show-toplevel")
    log = log_path(Path.cwd(), "gate")
    if top_out is None or log is None:
        return emit({"error": "not a git repository"}, 2)
    top = Path(top_out)
    command = mapped_command(top, "gate")
    cmd = ["sh", "-c", command] if command else [str(top / "scripts" / "gate.sh")]
    reports = log.parent / f"{log.stem}-reports"
    reports.mkdir()
    payload = run_logged(cmd, log, top, {**os.environ, "DEVLOOP_REPORT_DIR": str(reports)})
    payload["failures"] = parse_failures(log.read_text(errors="replace"))
    payload["slowest"] = slowest(reports)
    if not any(reports.glob("*.xml")):
        payload["hint"] = JUNIT_HINT
    sys.path.insert(0, str(Path(__file__).parent))
    import size
    from seal.check import check
    from seal.weakened import weakened

    payload["seal"] = check(top, True)
    payload["weakened"] = weakened(top)
    payload["size"] = size.check(top, "uncommitted")
    ok = payload["exit"] == 0 and payload["seal"]["ok"] and payload["size"]["ok"]
    return emit(payload, 0 if ok else 1)


# ci wait


def cmd_ci_wait(args: argparse.Namespace) -> int:
    import ci

    code, payload = ci.wait(args.pr, args.timeout)
    return emit(payload, code)


# board


def cmd_board(args: argparse.Namespace) -> int:
    raw = os.environ.get("DEVLOOP_BOARD_PORT") or "7777"
    port = int(raw) if raw.isascii() and raw.isdigit() and 0 < int(raw) < 65536 else None
    bad_port = {"state": "error", "url": f"http://127.0.0.1:{raw}/",
                "message": f"DEVLOOP_BOARD_PORT is not a port: {raw}"}
    # Lazy: the old tests load this file with importlib and must not need the board package.
    sys.path.insert(0, str(Path(__file__).parent))
    from board import store, writer  # board.server only when a server is stopped or ensured: its imports are slow.

    if args.stop_server:
        from board import server
        stopped = server.stop(port) if port is not None else bad_port
        return emit({"notes": [], "removed": [], "board_note": None, "skipped": [], "server": stopped}, 0)
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    hook_cwd = hook_session = hook_agent = hook_transcript = None
    if args.event:
        try:
            hook = json.loads(sys.stdin.read())
        except ValueError:
            hook = None
        hook = hook if isinstance(hook, dict) else {}
        cwd, session = hook.get("cwd"), hook.get("session_id")
        hook_cwd = Path(cwd) if isinstance(cwd, str) and cwd else None
        hook_session = session if isinstance(session, str) and session else None  # D12: else no session map.
        agent = hook.get("agent_id")
        hook_agent = agent if isinstance(agent, str) and agent else None
        log = hook.get("transcript_path")
        hook_transcript = log if isinstance(log, str) and log else None

    try:
        code, payload = writer.run(sys.modules[__name__], repo, notes, Path.home(), event=args.event,
                                   hook_cwd=hook_cwd, hook_session=hook_session, hook_agent=hook_agent,
                                   hook_transcript=hook_transcript,
                                   claim=args.claim)
        if code != 0:
            return emit(payload, code)
        whole = {s["reason"] for s in payload["skipped"]} & {"no-dev-loop", "notes-in-repo"}
        if args.no_serve or whole:
            payload["server"] = {"state": "disabled", "url": f"http://127.0.0.1:{raw}/"}
        elif port is None:
            payload["server"] = bad_port
        else:
            from board import server
            machine = store.load_machine(Path.home())
            top = Path(git(repo, "rev-parse", "--show-toplevel") or repo)
            payload["server"] = server.ensure(port, writer.kit_version(top), [store.root_of(notes)], machine["id"],
                                              machine["owner"])
    except Exception as exc:  # A bad machine.json, an undecodable spec, an OSError: JSON, never a traceback.
        error = f"{type(exc).__name__}: {exc}"
        try:  # A hook drops the JSON: `last_run` shows the crash in `status`. Best effort.
            from board import state
            common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if common:
                state.save_last_run(Path(common) / "dev-loop", time.strftime(writer.TIME, time.gmtime()),
                                    [{"feature": None, "reason": "error", "message": error}])
        except Exception:
            pass
        return emit({"error": error}, 1)
    return emit(payload, 0)


# seal


def cmd_seal(args: argparse.Namespace) -> int:
    if args.rebase_continue == bool(args.feature or args.message or args.reverts) or (
            not args.rebase_continue and not (args.feature and args.message)):
        return emit({"error": "seal needs --feature and --message, or --rebase-continue alone"}, 2)
    # Lazy, as `board`: the old tests load this file with importlib.
    sys.path.insert(0, str(Path(__file__).parent))
    from seal import value

    repo = Path.cwd()
    out = value.rebase_continue(repo) if args.rebase_continue else value.seal(
        repo, args.feature, args.message, args.reverts or [])
    return emit(out, 1 if "error" in out else 0)


def cmd_audit(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(Path(__file__).parent))
    from seal import audit

    try:
        hook = json.loads(sys.stdin.read())
    except ValueError:
        hook = None
    if not isinstance(hook, dict):  # An audit error is a warning: exit 0, as on every skip.
        return emit(audit.unparsed(Path.cwd()), 0)
    return emit(audit.audit(hook), 0)


def cmd_seal_check(args: argparse.Namespace) -> int:
    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top is None:
        return emit({"error": "not a git repository"}, 2)
    sys.path.insert(0, str(Path(__file__).parent))
    from seal.check import check

    out = check(Path(top), args.uncommitted)
    return emit(out, 0 if out["ok"] else 1)



def cmd_size_check(args: argparse.Namespace) -> int:
    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top is None:
        return emit({"error": "not a git repository"}, 2)
    sys.path.insert(0, str(Path(__file__).parent))
    import size

    out = size.check(Path(top), "all" if args.all else "uncommitted" if args.uncommitted else "committed")
    return emit(out, 0 if out["ok"] else 1)

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
    from verify import FILL, SKILL_NAME, url_ok

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
    from verify import load_entry

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
    from verify import config_errors, entries, load_entry, skill_dir

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


def cmd_capture(args: argparse.Namespace) -> int:
    from capture import run, write_after

    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top is None:
        return emit({"error": "not a git repository"}, 1)
    if args.write_after:
        return emit(*write_after(Path(top), args.write_after))
    payload, code = run(Path(top), args.pr, args.before, args.after)
    return emit(payload, code)


def cmd_second_review(args: argparse.Namespace) -> int:
    from second_review import run

    top = git(Path.cwd(), "rev-parse", "--show-toplevel")
    return emit(*run(Path(top or "."), args.harness, args.timeout, os.environ))


def cmd_review_merge(args: argparse.Namespace) -> int:
    from feature_gate import review_merge

    return emit(*review_merge(args.pairs, git(Path.cwd(), "rev-parse", "--show-toplevel")))


def cmd_acceptance(args: argparse.Namespace) -> int:
    from feature_gate import acceptance

    return emit(*acceptance(Path.cwd(), args.failed, os.environ))


def cmd_release_check(args: argparse.Namespace) -> int:
    from release import check, is_release_tag

    repo = Path(args.repo).resolve()
    notes = None  # a non-release tag is skipped before any note read, so it needs no notes path
    if args.tag is None or is_release_tag(args.tag):
        try:
            notes = resolve(repo, os.environ, Path.home())
        except NotesPathError as exc:
            return emit({"error": str(exc)}, 1)
    return emit(*check(repo, notes, args.tag or args.sha, args.tag))


def cmd_release_plan(args: argparse.Namespace) -> int:
    from release import plan

    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    return emit(*plan(repo, notes, args.harness, args.profile, args.budget))


def cmd_release_record(args: argparse.Namespace) -> int:
    from release import record

    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    return emit(*record(repo, notes, Path(args.run_folder), args.base, args.harness, os.environ))


def cmd_metrics(args: argparse.Namespace) -> int:
    import metrics
    from board import writer

    repo = Path(args.repo).resolve()
    slug = (args.feature or "").lower() or branch_slug(repo)
    if slug is None:
        return emit({"error": "no feature: run metrics on a feature branch or pass --feature"}, 1)
    worktree, _ = feature_checkout(repo, slug)
    if worktree is None:
        return emit({"error": f"no worktree for {slug}"}, 1)
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    matches = [path for name, path in specs(notes) if name == slug]
    fields = frontmatter(matches[-1]) if matches else {}
    track = (fields.get("track") or "").upper() or "S"
    found = writer.worktrees(sys.modules[__name__], repo)  # a worktree in a rebase (detached) keeps its slug
    paths = list(found)
    features = {name: path for path, name in found.items() if name}
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    items = metrics.attribute(metrics.load(metrics.project_folders(root, paths)), features)
    if not any(c.feature == slug and not c.copy for c in items):
        return emit({"error": f"no transcript records for {slug}"}, 1)
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir") or ""
    bounds, present = metrics.boundaries(Path(common) / "dev-loop" / "board-state.json", slug, bool(matches))
    ref = main_ref(repo)
    payload = {"feature": slug, "track": track, "shipped": fields.get("shipped", ""),
               **metrics.numbers(items, slug, bounds),
               "tests": metrics.test_counts(worktree, ref) if ref else {"unit": 0, "acceptance": 0}}
    payload["row"] = metrics.row(slug, track, payload["shipped"], payload, present)
    payload["file"] = None
    if args.write:
        file = notes / "Feature Metrics.md"
        try:
            metrics.write_row(file, slug, payload["row"], Path(common) / "dev-loop" / "metrics.lock")
        except (OSError, UnicodeError) as exc:
            return emit({"error": f"cannot read or write {file}: {exc}"}, 1)
        payload["file"] = str(file)
    return emit(payload, 0)


def cmd_sessions(args: argparse.Namespace) -> int:
    import retro
    from board import writer

    repo = Path(args.repo).resolve()
    if git(repo, "rev-parse", "--show-toplevel") is None:
        return emit({"error": f"not a git repo: {repo}"}, 1)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        found = retro.sessions(repo, args.since, os.environ, Path.home(), now)
        since = writer.iso((now - timedelta(days=args.since)).timestamp())
    except OverflowError:
        return emit({"error": f"--since too large: {args.since}"}, 2)
    return emit({"since": since, "sessions": found}, 0)


def finite_minutes(value: str) -> float:
    try:
        minutes = float(value)
    except ValueError:
        minutes = float("nan")
    if not math.isfinite(minutes) or minutes <= 0:
        raise argparse.ArgumentTypeError(f"not a finite number above 0: {value}")
    return minutes


def positive_int(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"not a positive integer: {value}")
    return int(value)


# decide


def branch_slug(repo: Path) -> str | None:
    """The slug of the checked-out branch `<prefix>/<slug>`, or None."""
    branch = git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    return branch.rsplit("/", 1)[1].lower() if branch and "/" in branch else None


def log_target(repo: Path, slug: str, notes: Path) -> Path | None:
    """The decision log of a feature: its spec, else (Track S) `decisions.md` in its worktree git dir."""
    matches = [path for name, path in specs(notes) if name == slug]
    if matches:
        return matches[-1]
    worktree, _ = feature_checkout(repo, slug)
    git_dir = git(worktree, "rev-parse", "--absolute-git-dir") if worktree else None
    return Path(git_dir) / "dev-loop" / "decisions.md" if git_dir else None


def cmd_decide(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    for name in ("what", "why", "evidence"):
        if not getattr(args, name).strip():
            return emit({"error": f"--{name} is empty"}, 1)
    slug = (args.feature or "").lower() or branch_slug(repo)
    if slug is None:
        return emit({"error": "no feature: pass --feature or run on a branch <prefix>/<slug>"}, 1)
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    try:  # A spec that cannot be read (an iCloud placeholder, a mode change) fails before any write.
        target = log_target(repo, slug, notes)
        common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if target is None or common is None:
            return emit({"error": f"no spec and no branch worktree for {slug}"}, 1)
        session, _ = autonomy.session_id(os.environ)
        stage = detect_feature(repo, slug, notes)["stage"]
        values = [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), str(stage), (session or "")[:8], args.kind,
                  "risk" if args.risk else "", args.what, args.why, args.evidence, args.result or ""]
        row = "| " + " | ".join(autonomy.cell(v) for v in values) + " |"
        autonomy.append_row(target, row, Path(common) / "dev-loop" / "decide.lock")
    except OSError as exc:
        return emit({"error": f"cannot read or write the decision log: {exc}"}, 1)
    return emit({"file": str(target), "row": row}, 0)


# pause

INTENT_FIELDS = ("intent", "verified", "open", "next")


def cmd_pause(args: argparse.Namespace) -> int:
    given = [name for name in (*INTENT_FIELDS, "agent", "runs") if getattr(args, name)]
    if args.clear and (given or args.event):
        return emit({"error": "--clear takes no other option"}, 2)
    if args.event and given:
        return emit({"error": "--event takes no intent, agent or run option"}, 2)
    agents = []
    for value in args.agent or []:
        agent_id, sep, task = value.partition("=")
        if not sep or not agent_id:
            return emit({"error": f"--agent takes <id>=<task>: {value}"}, 2)
        agents.append({"id": agent_id, "task": task})
    event: dict = {}
    if args.event:
        try:
            event = json.loads(sys.stdin.read())
        except ValueError:
            event = None  # type: ignore[assignment]
        if not isinstance(event, dict):
            return emit({"skipped": "bad-input"}, 0)
        if not isinstance(event.get("cwd"), str) or not autonomy.is_feature_worktree(Path(event["cwd"])):
            return emit({"skipped": "not-a-feature-worktree"}, 0)
        repo = Path(event["cwd"]).resolve()
    else:
        repo = Path(args.repo).resolve()
    git_dir = git(repo, "rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return emit({"error": f"not a git repository: {repo}"}, 1)
    path = autonomy.note_path(Path(git_dir))
    if args.clear:
        found = path.is_file()
        if found:
            path.unlink()
        return emit({"cleared": found}, 0)

    if args.event:
        session, harness = event.get("session_id"), "claude"
        transcript = event.get("transcript_path")
        reason = event.get("error") or "unknown"
        message = event.get("last_assistant_message") or event.get("error_details")
    else:
        session, harness = autonomy.session_id(os.environ)
        transcript = autonomy.find_transcript(session, harness, os.environ, Path.home()) if session and harness else None
        reason, message = "manual", None
    slug = branch_slug(repo)
    stage = ledger_last = last_decision = None
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError:
        notes = None
    try:  # An unreadable spec, plan or ledger (an iCloud placeholder) must not cost the note.
        if notes is not None:
            stage = detect(repo, None, notes)["stage"]
            target = log_target(repo, slug, notes) if slug else None
            if target is not None and target.is_file():
                rows = autonomy.log_rows(target.read_text())
                last_decision = rows[-1]["row"] if rows else None
                plan = find_plan(notes, target) if target.suffix == ".md" and target.parent == notes / "specs" else None
                top = git(repo, "rev-parse", "--show-toplevel")
                ledger = Path(top) / ".superpowers" / "sdd" / plan.stem / "progress.md" if plan and top else None
                if ledger is not None and ledger.is_file():
                    ledger_last = next((line for line in reversed(ledger.read_text().splitlines()) if line.strip()),
                                       None)
    except OSError:
        stage = ledger_last = last_decision = None
    # Not git(): its strip() eats the leading space of a first ` M <path>` line.
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z"], capture_output=True, text=True)
    entries = iter(status.stdout.split("\0") if status.returncode == 0 else [])
    dirty = []
    for entry in entries:
        if len(entry) > 3:
            dirty.append(entry[3:])
            if entry[0] in "RC":
                next(entries, None)  # a rename or copy: the source path follows, keep the destination
    note = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
        "message": message,
        "harness": harness,
        "session": session,
        "transcript": transcript,
        "feature": slug,
        "stage": stage,
        "ledger_last": ledger_last,
        "dirty": dirty,
        "last_decision": last_decision,
        "agents": agents,
        "runs": list(args.runs or []),
        **{name: getattr(args, name) for name in INTENT_FIELDS},
    }
    autonomy.write_atomic(path, json.dumps(note, indent=2) + "\n")
    return emit({"file": str(path), "note": note}, 0)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        print(json.dumps({"error": message}))
        sys.exit(2)


def main() -> int:
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("--repo", default=".")
    status.add_argument("--feature")
    status.set_defaults(run=cmd_status)

    merge_check = commands.add_parser("merge-check")
    merge_check.add_argument("--repo", default=".")
    merge_check.add_argument("--feature")
    merge_check.set_defaults(run=cmd_merge_check)

    logged = commands.add_parser("logged")
    logged.add_argument("--name")
    logged.add_argument("cmd", nargs=argparse.REMAINDER)
    logged.set_defaults(run=cmd_logged)

    gate = commands.add_parser("gate")
    gate.add_argument("--summary", action="store_true", required=True)
    gate.set_defaults(run=cmd_gate)

    ci = commands.add_parser("ci").add_subparsers(dest="ci_command", required=True)
    wait = ci.add_parser("wait")
    wait.add_argument("--pr", type=int)
    wait.add_argument("--timeout", type=finite_minutes, default=30, help="minutes")
    wait.set_defaults(run=cmd_ci_wait)

    board = commands.add_parser("board")
    board.add_argument("--repo", default=".")
    board.add_argument("--event", choices=["session-start", "prompt", "stop", "subagent-start", "subagent-stop", "post-tool"])
    board.add_argument("--claim")
    board.add_argument("--no-serve", action="store_true")
    board.add_argument("--stop-server", action="store_true")
    board.set_defaults(run=cmd_board)

    seal = commands.add_parser("seal")
    seal.add_argument("--feature")
    seal.add_argument("--message")
    seal.add_argument("--reverts", action="extend", nargs="+")
    seal.add_argument("--rebase-continue", action="store_true")
    seal.set_defaults(run=cmd_seal)

    audit = commands.add_parser("audit")
    audit.set_defaults(run=cmd_audit)

    seal_check = commands.add_parser("seal-check")
    seal_check.add_argument("--uncommitted", action="store_true")
    seal_check.set_defaults(run=cmd_seal_check)

    size_check = commands.add_parser("size-check")
    size_mode = size_check.add_mutually_exclusive_group()
    size_mode.add_argument("--uncommitted", action="store_true")
    size_mode.add_argument("--all", action="store_true")
    size_check.set_defaults(run=cmd_size_check)

    verify_init = commands.add_parser("verify-init")
    verify_init.add_argument("--base-url", required=True)
    verify_init.add_argument("--name")
    verify_init.set_defaults(run=cmd_verify_init)

    drive = commands.add_parser("drive")
    scope = drive.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--since")
    scope.add_argument("--routes-from")
    drive.set_defaults(run=cmd_drive)

    capture = commands.add_parser("capture")
    capture.add_argument("--pr", type=positive_int)
    capture.add_argument("--before")
    capture.add_argument("--after")
    capture.add_argument("--write-after")
    capture.set_defaults(run=cmd_capture)

    second = commands.add_parser("second-review")
    second.add_argument("--harness", choices=["claude", "codex"], required=True)
    second.add_argument("--timeout", type=finite_minutes, default=15)
    second.set_defaults(run=cmd_second_review)

    review = commands.add_parser("review-merge")
    review.add_argument("pairs", nargs="+")
    review.set_defaults(run=cmd_review_merge)

    accept = commands.add_parser("acceptance")
    accept.add_argument("--failed", action="store_true")
    accept.set_defaults(run=cmd_acceptance)

    release = commands.add_parser("release-check")
    release.add_argument("--repo", default=".")
    target = release.add_mutually_exclusive_group(required=True)
    target.add_argument("--tag")
    target.add_argument("--sha")
    release.set_defaults(run=cmd_release_check)

    plan = commands.add_parser("release-plan")
    plan.add_argument("--repo", default=".")
    plan.add_argument("--harness", choices=["claude", "codex"], default="claude")
    plan.add_argument("--profile", choices=["quick", "standard", "deep"])
    plan.add_argument("--budget", type=positive_int)
    plan.set_defaults(run=cmd_release_plan)

    rec = commands.add_parser("release-record")
    rec.add_argument("--repo", default=".")
    rec.add_argument("--run", dest="run_folder", required=True)
    rec.add_argument("--base")
    rec.add_argument("--harness", choices=["claude", "codex"], default="claude")
    rec.set_defaults(run=cmd_release_record)

    metrics = commands.add_parser("metrics")
    metrics.add_argument("--repo", default=".")
    metrics.add_argument("--feature")
    metrics.add_argument("--write", action="store_true")
    metrics.set_defaults(run=cmd_metrics)

    sessions = commands.add_parser("sessions")
    sessions.add_argument("--since", type=positive_int, required=True)
    sessions.add_argument("--repo", default=".")
    sessions.set_defaults(run=cmd_sessions)

    decide = commands.add_parser("decide")
    decide.add_argument("--kind", choices=["decision", "discovery"], required=True)
    decide.add_argument("--what", required=True)
    decide.add_argument("--why", required=True)
    decide.add_argument("--evidence", required=True)
    decide.add_argument("--result")
    decide.add_argument("--risk", action="store_true")
    decide.add_argument("--feature")
    decide.add_argument("--repo", default=".")
    decide.set_defaults(run=cmd_decide)

    pause = commands.add_parser("pause")
    pause.add_argument("--event", action="store_true")
    pause.add_argument("--clear", action="store_true")
    for name in INTENT_FIELDS:
        pause.add_argument(f"--{name}")
    pause.add_argument("--agent", action="append")
    pause.add_argument("--run", action="append", dest="runs")
    pause.add_argument("--repo", default=".")
    pause.set_defaults(run=cmd_pause)

    args = parser.parse_args()
    if args.command == "capture":
        if args.write_after and (args.pr or args.before or args.after):
            parser.error("--write-after takes no --pr, --before or --after")
        if not args.write_after and not args.pr:
            parser.error("--pr is required unless --write-after is given")
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())

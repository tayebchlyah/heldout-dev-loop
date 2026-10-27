"""`status`: the stage of a feature, or the open features on main, with the next action."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import autonomy
from devloop import emit, find_plan, frontmatter, git, specs
from notes_path import NotesPathError, resolve


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
            out["kit_drift"] = {"worktree": have, "main": main, "direction": "worktree-newer"}
        else:
            from merge_check import main_ref

            ref = main_ref(Path(top))
            try:
                remote = json.loads(git(Path(top), "show", f"{ref}:.dev-loop/MANIFEST.json") or "")["version"] if ref else None
            except (ValueError, KeyError, TypeError):
                remote = None
            if isinstance(remote, str) and store.version_key(remote) > store.version_key(have):
                out["kit_drift"] = {"worktree": have, "main": remote, "direction": "main-newer"}
    return emit(out, 0)

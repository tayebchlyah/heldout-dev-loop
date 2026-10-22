#!/usr/bin/env python3
"""dev-loop CLI. Each command prints one JSON object on stdout.

  devloop.py status [--repo PATH] [--feature SLUG]   stage of the repo or of one feature
  devloop.py logged [--name NAME] -- CMD...          run CMD, full output to a log file
  devloop.py gate --summary                           run the gate through `logged`, summarize it
  devloop.py ci wait [--pr N] [--timeout MIN]         wait for the PR checks
  devloop.py board [--repo PATH] [--event E] [--claim SLUG] [--no-serve] [--stop-server]
                                                      write the status notes of this machine

Exit 0 on success, 1 on a failure the JSON describes, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# Write no __pycache__ into the skill folder: in a project it is a vendored kit folder.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
from notes_path import NotesPathError, resolve  # noqa: E402

CHECKS_RETRY_SECONDS = 15
FAILED_CHECK_STATES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"}
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


def acceptance_commit(repo: Path, slug: str, revs: str) -> bool | None:
    """A commit in `revs` that touches the acceptance folder and names the slug in its subject (Stage 3 may only
    extend earlier features' files). None when git fails (`revs` names a missing ref)."""
    subjects = git(repo, "log", "--format=%s", revs, "--", "tests/acceptance")
    return None if subjects is None else any(holds_slug(s, slug) for s in subjects.splitlines())


def has_acceptance(worktree: Path, slug: str) -> bool | None:
    """A file named after the slug, else an acceptance commit not on origin/main; None when origin/main is missing."""
    root = worktree / "tests" / "acceptance"
    if any(path.is_file() and holds_slug(str(path.relative_to(root)), slug) for path in root.rglob("*")):
        return True
    return acceptance_commit(worktree, slug, "origin/main..HEAD")


def remote_branch(repo: Path, slug: str) -> str | None:
    """`origin/<prefix>/<slug>`, or None."""
    remotes = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin") or ""
    return next((ref for ref in remotes.splitlines() if ref.lower().endswith(f"/{slug}") and ref.count("/") >= 2), None)


def shipped_without_branch(repo: Path, slug: str) -> bool:
    """An acceptance file or acceptance commit for the slug on the default branch. For a feature with no branch,
    local or remote."""
    prefix = "tests/acceptance/"
    files = git(repo, "ls-tree", "-r", "--name-only", DEFAULT_BRANCH, prefix) or ""
    if any(holds_slug(f[len(prefix):], slug) for f in files.splitlines() if f.startswith(prefix)):
        return True
    return bool(acceptance_commit(repo, slug, DEFAULT_BRANCH))


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
        return at("shipped", f"no branch for {slug}, its acceptance file is on {DEFAULT_BRANCH}", "none")
    lines = cherry(repo, known) if known else None
    if lines and not any(line.startswith("+") for line in lines):
        return at("shipped", f"git cherry origin/main {known} lists no + commit", "none")
    if worktree is None or branch is None:  # A worktree always comes with its branch.
        return at(3, f"no worktree on a branch <prefix>/{slug}", f"Stage 3 Acceptance: create the feature worktree for {slug}")
    if fields.get("acceptance", "").lower() not in SKIP_ACCEPTANCE:
        found = has_acceptance(worktree, slug)
        if found is None:
            return at(3, f"no acceptance file for {slug} and no origin/main to read branch commits", FETCH)
        if not found:
            return at(3, f"no acceptance file for {slug} and no branch commit under tests/acceptance naming it",
                      f"Stage 3 Acceptance: dispatch spec-tester; the commit subject names {slug}")
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


def cmd_status(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit(result(0, "no notes path", str(exc)), 0)
    out = detect(repo, args.feature, notes)
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
    top = git(repo, "rev-parse", "--show-toplevel")
    first = next((line[len("worktree "):] for line in (git(repo, "worktree", "list", "--porcelain") or "").splitlines()
                  if line.startswith("worktree ")), None)
    if top and first:
        have, main = writer.kit_version(Path(top)), writer.kit_version(Path(first))
        if store.version_key(have) > store.version_key(main):
            out["kit_drift"] = {"worktree": have, "main": main}
    return emit(out, 0)


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


def mapped_gate(top: Path) -> str | None:
    try:
        command = json.loads((top / "dev-loop.json").read_text()).get("commands", {}).get("gate")
    except (OSError, ValueError, AttributeError):
        return None
    return command if isinstance(command, str) and command else None


def cmd_gate(args: argparse.Namespace) -> int:
    top_out = git(Path.cwd(), "rev-parse", "--show-toplevel")
    log = log_path(Path.cwd(), "gate")
    if top_out is None or log is None:
        return emit({"error": "not a git repository"}, 2)
    top = Path(top_out)
    command = mapped_gate(top)
    cmd = ["sh", "-c", command] if command else [str(top / "scripts" / "gate.sh")]
    reports = log.parent / f"{log.stem}-reports"
    reports.mkdir()
    payload = run_logged(cmd, log, top, {**os.environ, "DEVLOOP_REPORT_DIR": str(reports)})
    payload["failures"] = parse_failures(log.read_text(errors="replace"))
    payload["slowest"] = slowest(reports)
    if not any(reports.glob("*.xml")):
        payload["hint"] = JUNIT_HINT
    return emit(payload, 0 if payload["exit"] == 0 else 1)


# ci wait


def first_error(log: str) -> str | None:
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if "error" in line.lower() or "FAIL" in line:
            return "\n".join(lines[i:i + 30])
    return None


def gh(*args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)


def cmd_ci_wait(args: argparse.Namespace) -> int:
    payload: dict = {"state": "no-pr", "failed_job": None, "first_error": None, "url": None}
    view = gh("pr", "view", *([str(args.pr)] if args.pr else []), "--json", "number,url")
    if view.returncode != 0:
        if "no pull requests found" not in view.stderr:
            payload.update(state="error", message=view.stderr.strip())
        return emit(payload, 1)
    pr = json.loads(view.stdout)
    number, payload["url"] = str(pr["number"]), pr["url"]
    deadline = time.monotonic() + args.timeout * 60
    while True:
        try:
            watch = gh("pr", "checks", number, "--watch", "--interval", "30", timeout=max(deadline - time.monotonic(), 0))
        except subprocess.TimeoutExpired:
            watch = None
        # Right after a push no check is registered yet: gh exits 1 with "no checks reported".
        if watch is not None and "no checks reported" in watch.stderr and time.monotonic() + CHECKS_RETRY_SECONDS < deadline:
            time.sleep(CHECKS_RETRY_SECONDS)
            continue
        if watch is None or "no checks reported" in watch.stderr:
            payload["state"] = "timeout"
            return emit(payload, 1)
        break
    if watch.returncode == 0:
        payload["state"] = "pass"
        return emit(payload, 0)
    payload["state"] = "fail"
    try:
        checks = json.loads(gh("pr", "checks", number, "--json", "name,state,link").stdout)
    except ValueError:
        checks = []
    failed = next((c for c in checks if c.get("state") in FAILED_CHECK_STATES), None)
    if failed:
        payload["failed_job"] = failed.get("name")
        payload["url"] = failed.get("link") or payload["url"]
        run = re.search(r"/runs/(\d+)", failed.get("link") or "")
        if run:
            payload["first_error"] = first_error(gh("run", "view", run.group(1), "--log-failed").stdout)
    return emit(payload, 1)


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
    hook_cwd = hook_session = hook_agent = None
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

    try:
        code, payload = writer.run(sys.modules[__name__], repo, notes, Path.home(), event=args.event,
                                   hook_cwd=hook_cwd, hook_session=hook_session, hook_agent=hook_agent,
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
    wait.add_argument("--timeout", type=float, default=30, help="minutes")
    wait.set_defaults(run=cmd_ci_wait)

    board = commands.add_parser("board")
    board.add_argument("--repo", default=".")
    board.add_argument("--event", choices=["session-start", "prompt", "stop", "subagent-start", "subagent-stop", "post-tool"])
    board.add_argument("--claim")
    board.add_argument("--no-serve", action="store_true")
    board.add_argument("--stop-server", action="store_true")
    board.set_defaults(run=cmd_board)

    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())

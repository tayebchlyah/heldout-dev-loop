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
import importlib
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
    from status import feature_checkout
    matches = [path for name, path in specs(notes) if name == slug]
    if matches:
        return matches[-1]
    worktree, _ = feature_checkout(repo, slug)
    git_dir = git(worktree, "rev-parse", "--absolute-git-dir") if worktree else None
    return Path(git_dir) / "dev-loop" / "decisions.md" if git_dir else None


def cmd_decide(args: argparse.Namespace) -> int:
    from status import detect_feature
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
    from status import detect
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
    sys.modules.setdefault("devloop", sys.modules["__main__"])
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("--repo", default=".")
    status.add_argument("--feature")
    status.set_defaults(run=("status", "cmd_status"))

    merge_check = commands.add_parser("merge-check")
    merge_check.add_argument("--repo", default=".")
    merge_check.add_argument("--feature")
    merge_check.set_defaults(run=("merge_check", "cmd_merge_check"))

    logged = commands.add_parser("logged")
    logged.add_argument("--name")
    logged.add_argument("cmd", nargs=argparse.REMAINDER)
    logged.set_defaults(run=("gate", "cmd_logged"))

    gate = commands.add_parser("gate")
    gate.add_argument("--summary", action="store_true", required=True)
    gate.set_defaults(run=("gate", "cmd_gate"))

    ci = commands.add_parser("ci").add_subparsers(dest="ci_command", required=True)
    wait = ci.add_parser("wait")
    wait.add_argument("--pr", type=int)
    wait.add_argument("--timeout", type=finite_minutes, default=30, help="minutes")
    wait.set_defaults(run=("ci", "cmd_ci_wait"))

    board = commands.add_parser("board")
    board.add_argument("--repo", default=".")
    board.add_argument("--event", choices=["session-start", "prompt", "stop", "subagent-start", "subagent-stop", "post-tool"])
    board.add_argument("--claim")
    board.add_argument("--no-serve", action="store_true")
    board.add_argument("--stop-server", action="store_true")
    board.set_defaults(run=("board.cli", "cmd_board"))

    seal = commands.add_parser("seal")
    seal.add_argument("--feature")
    seal.add_argument("--message")
    seal.add_argument("--reverts", action="extend", nargs="+")
    seal.add_argument("--rebase-continue", action="store_true")
    seal.set_defaults(run=("seal.value", "cmd_seal"))

    audit = commands.add_parser("audit")
    audit.set_defaults(run=("seal.audit", "cmd_audit"))

    seal_check = commands.add_parser("seal-check")
    seal_check.add_argument("--uncommitted", action="store_true")
    seal_check.set_defaults(run=("seal.check", "cmd_seal_check"))

    size_check = commands.add_parser("size-check")
    size_mode = size_check.add_mutually_exclusive_group()
    size_mode.add_argument("--uncommitted", action="store_true")
    size_mode.add_argument("--all", action="store_true")
    size_check.set_defaults(run=("size", "cmd_size_check"))

    verify_init = commands.add_parser("verify-init")
    verify_init.add_argument("--base-url", required=True)
    verify_init.add_argument("--name")
    verify_init.set_defaults(run=("verify", "cmd_verify_init"))

    drive = commands.add_parser("drive")
    scope = drive.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--since")
    scope.add_argument("--routes-from")
    drive.set_defaults(run=("verify", "cmd_drive"))

    capture = commands.add_parser("capture")
    capture.add_argument("--pr", type=positive_int)
    capture.add_argument("--before")
    capture.add_argument("--after")
    capture.add_argument("--write-after")
    capture.set_defaults(run=("capture", "cmd_capture"))

    second = commands.add_parser("second-review")
    second.add_argument("--harness", choices=["claude", "codex"], required=True)
    second.add_argument("--timeout", type=finite_minutes, default=15)
    second.set_defaults(run=("second_review", "cmd_second_review"))

    review = commands.add_parser("review-merge")
    review.add_argument("pairs", nargs="+")
    review.set_defaults(run=("feature_gate", "cmd_review_merge"))

    accept = commands.add_parser("acceptance")
    accept.add_argument("--failed", action="store_true")
    accept.set_defaults(run=("feature_gate", "cmd_acceptance"))

    release = commands.add_parser("release-check")
    release.add_argument("--repo", default=".")
    target = release.add_mutually_exclusive_group(required=True)
    target.add_argument("--tag")
    target.add_argument("--sha")
    release.set_defaults(run=("release", "cmd_release_check"))

    plan = commands.add_parser("release-plan")
    plan.add_argument("--repo", default=".")
    plan.add_argument("--harness", choices=["claude", "codex"], default="claude")
    plan.add_argument("--profile", choices=["quick", "standard", "deep"])
    plan.add_argument("--budget", type=positive_int)
    plan.set_defaults(run=("release", "cmd_release_plan"))

    rec = commands.add_parser("release-record")
    rec.add_argument("--repo", default=".")
    rec.add_argument("--run", dest="run_folder", required=True)
    rec.add_argument("--base")
    rec.add_argument("--harness", choices=["claude", "codex"], default="claude")
    rec.set_defaults(run=("release", "cmd_release_record"))

    metrics = commands.add_parser("metrics")
    metrics.add_argument("--repo", default=".")
    metrics.add_argument("--feature")
    metrics.add_argument("--write", action="store_true")
    metrics.set_defaults(run=("metrics", "cmd_metrics"))

    sessions = commands.add_parser("sessions")
    sessions.add_argument("--since", type=positive_int, required=True)
    sessions.add_argument("--repo", default=".")
    sessions.set_defaults(run=("retro", "cmd_sessions"))

    decide = commands.add_parser("decide")
    decide.add_argument("--kind", choices=["decision", "discovery"], required=True)
    decide.add_argument("--what", required=True)
    decide.add_argument("--why", required=True)
    decide.add_argument("--evidence", required=True)
    decide.add_argument("--result")
    decide.add_argument("--risk", action="store_true")
    decide.add_argument("--feature")
    decide.add_argument("--repo", default=".")
    decide.set_defaults(run=("devloop", "cmd_decide"))

    pause = commands.add_parser("pause")
    pause.add_argument("--event", action="store_true")
    pause.add_argument("--clear", action="store_true")
    for name in INTENT_FIELDS:
        pause.add_argument(f"--{name}")
    pause.add_argument("--agent", action="append")
    pause.add_argument("--run", action="append", dest="runs")
    pause.add_argument("--repo", default=".")
    pause.set_defaults(run=("devloop", "cmd_pause"))

    args = parser.parse_args()
    if args.command == "capture":
        if args.write_after and (args.pr or args.before or args.after):
            parser.error("--write-after takes no --pr, --before or --after")
        if not args.write_after and not args.pr:
            parser.error("--pr is required unless --write-after is given")
    module, name = args.run
    return getattr(importlib.import_module(module), name)(args)


if __name__ == "__main__":
    sys.exit(main())

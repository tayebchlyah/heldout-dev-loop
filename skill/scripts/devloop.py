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
  devloop.py search-wall                              PreToolUse hook JSON on stdin: deny a subagent search that reaches the held-out suite
  devloop.py verify-init --base-url URL [--name SKILL]
                                                      write the verify skill skeleton and the verify key
  devloop.py drive [--all | --since SHA | --routes-from FILE]
                                                      drive the feature map routes the branch changed, save the evidence
  devloop.py capture --pr N [--before FILE] [--after FILE] | --write-after FILE
                                                      put the before and after screens in the PR body
  devloop.py second-review --harness claude|codex [--timeout MINUTES]
  devloop.py review-merge REVIEWER=FILE [REVIEWER=FILE ...]
                                                      merge the reviewer tables by file and line
  devloop.py review-save --reviewer NAME | --done    save a reviewer table (stdin) or the Track S marker in the git dir
  devloop.py acceptance [--failed]                    run the acceptance command, read its JUnit, record the run
  devloop.py release-plan [--repo PATH] [--harness claude|codex] [--profile quick|standard|deep] [--budget N] [--require-sandbox]
                                                      the release audit plan: run folder, scope, sandbox, skill
  devloop.py release-record --run PATH [--base SHA] [--harness claude|codex] [--repo PATH]
                                                      record a finished release audit run in the project note
  devloop.py release-record --fix-wave --issue N [--issue N ...] [--repo PATH]
                                                      record the end of the fix wave of the current audit
  devloop.py audit-run <step> --run FOLDER [--repo PATH] ...
                                                      the parent-side steps of a release audit run (reference/cli/audit-run.md)
  devloop.py release-check [--repo PATH] (--tag NAME | --sha REV)
                                                      the release gate: a current audit record, no open security item
  devloop.py ci-skip --base REV [--head REV] [--repo PATH] [--github-output]
                                                      may CI skip this diff: only a release or a kit upgrade
  devloop.py release-notes --tag TAG [--repo PATH] [--output FILE]
                                                      the CHANGELOG.md section body of a release tag
  devloop.py metrics [--repo PATH] [--feature SLUG] [--write]
                                                      the token cost of each stage of a feature, its row in Feature Metrics.md
  devloop.py sessions --since DAYS [--repo PATH]       the Claude Code and Codex sessions of the repo, for retro
  devloop.py decide --kind decision|discovery --what T --why T --evidence T [--result T] [--risk] [--feature SLUG]
                                                      append one row to the decision log
  devloop.py pause [--event] [--intent T] [--verified T] [--open T] [--next T] [--agent ID=TASK ...] [--run LOG ...]
  devloop.py pause --clear                            write or delete the pause note of the worktree
  devloop.py notes-migrate [--repo PATH] [--apply [--plan HASH]]
                                                      move the notes to the feature folder layout, rewrite the links

Exit 0 on success, 1 on a failure the JSON describes, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

# Write no __pycache__ into the skill folder: in a project it is a vendored kit folder.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))


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
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
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


FEATURES = "Features"
ANSWERS = "Answers"
MAPS = "Maps"
AUDITS = "Security Audit"
BOARD = "Dev Loop Board"
BOARD_FEATURES = "Features"
OLD_NAMES = ("specs", "plans", "framing", "answers", "maps", "security-audit", "dev-loop-board")
JOURNAL = ".notes-migrate.json"


def old_layout(notes: Path) -> list[str]:
    """The entries of the notes folder the old layout left behind; exact names, so a new `Answers` is not one."""
    try:
        names = os.listdir(notes)
    except OSError:
        return []
    return sorted(name for name in names if name in OLD_NAMES or name == JOURNAL)


def specs(notes: Path) -> list[tuple[str, Path]]:
    found = []
    for path in sorted((notes / FEATURES).glob("*/Spec.md")):
        found.append(((frontmatter(path).get("feature") or slug_from_title(path.parent.name)).lower(), path))
    if "specs" in old_layout(notes):
        for path in sorted((notes / "specs").glob("*.md")):
            found.append(((frontmatter(path).get("feature") or slug_from_title(path.name)).lower(), path))
    return found


def is_spec(notes: Path, path: Path) -> bool:
    return any(path == spec for _, spec in specs(notes))


def find_plan(notes: Path, spec: Path) -> Path | None:
    if spec.name == "Spec.md" and spec.parent.parent == notes / FEATURES:
        plan = spec.parent / "Plan.md"
        return plan if plan.is_file() else None
    plans = [p for p in sorted((notes / "plans").glob("*.md")) if frontmatter(p).get("spec") in (spec.name, spec.stem)]
    return plans[-1] if plans else None


def ledger(top: Path, plan: Path) -> Path | None:
    """The `progress.md` of the sdd folder whose `plan-path` marker names this plan; the folder name is free."""
    want = os.path.realpath(plan)
    for marker in sorted((top / ".superpowers" / "sdd").glob("*/plan-path")):
        try:
            named = Path(marker.read_text().strip())
        except OSError:
            continue
        if os.path.realpath(top / named) == want:
            progress = marker.parent / "progress.md"
            return progress if progress.is_file() else None
    folder = top / ".superpowers" / "sdd" / plan.stem
    if folder.is_dir() and not (folder / "plan-path").exists():
        progress = folder / "progress.md"
        return progress if progress.is_file() else None
    return None


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


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        print(json.dumps({"error": message}))
        sys.exit(2)


def main() -> int:
    if __name__ in sys.modules:
        sys.modules.setdefault("devloop", sys.modules[__name__])
    from autonomy import INTENT_FIELDS

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

    search_wall = commands.add_parser("search-wall")
    search_wall.set_defaults(run=("seal.search", "cmd_search_wall"))

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

    save = commands.add_parser("review-save")
    which = save.add_mutually_exclusive_group(required=True)
    which.add_argument("--reviewer")
    which.add_argument("--done", action="store_true")
    save.set_defaults(run=("feature_gate", "cmd_review_save"))

    accept = commands.add_parser("acceptance")
    accept.add_argument("--failed", action="store_true")
    accept.set_defaults(run=("feature_gate", "cmd_acceptance"))

    release = commands.add_parser("release-check")
    release.add_argument("--repo", default=".")
    target = release.add_mutually_exclusive_group(required=True)
    target.add_argument("--tag")
    target.add_argument("--sha")
    release.set_defaults(run=("release", "cmd_release_check"))

    skip = commands.add_parser("ci-skip")
    skip.add_argument("--base", required=True)
    skip.add_argument("--head", default="HEAD")
    skip.add_argument("--repo", default=".")
    skip.add_argument("--github-output", action="store_true")
    skip.set_defaults(run=("ci_skip", "cmd_ci_skip"))

    notes = commands.add_parser("release-notes")
    notes.add_argument("--tag", required=True)
    notes.add_argument("--repo", default=".")
    notes.add_argument("--output")
    notes.set_defaults(run=("release", "cmd_release_notes"))

    plan = commands.add_parser("release-plan")
    plan.add_argument("--repo", default=".")
    plan.add_argument("--harness", choices=["claude", "codex"], default="claude")
    plan.add_argument("--profile", choices=["quick", "standard", "deep"])
    plan.add_argument("--budget", type=positive_int)
    plan.add_argument("--require-sandbox", action="store_true")
    plan.set_defaults(run=("release", "cmd_release_plan"))

    rec = commands.add_parser("release-record")
    rec.add_argument("--repo", default=".")
    rec.add_argument("--run", dest="run_folder")
    rec.add_argument("--base")
    rec.add_argument("--harness", choices=["claude", "codex"])
    rec.add_argument("--fix-wave", action="store_true")
    rec.add_argument("--issue", dest="issues", action="append", type=positive_int)
    rec.set_defaults(run=("release", "cmd_release_record"))

    audit_steps = commands.add_parser("audit-run").add_subparsers(dest="audit_step", required=True)

    def step(name: str, module: str) -> argparse.ArgumentParser:
        sub = audit_steps.add_parser(name)
        sub.add_argument("--run", dest="run_folder", required=True)
        sub.add_argument("--repo", default=".")
        sub.set_defaults(run=(f"audit.{module}", "cmd_" + name.replace("-", "_")))
        return sub

    step("seed", "seed")
    units = step("units", "units").add_mutually_exclusive_group(required=True)
    units.add_argument("--add")
    units.add_argument("--update")
    brief = step("brief", "brief")
    brief.add_argument("--kind", choices=["hunter", "critic", "verifier", "recheck"], required=True)
    brief.add_argument("--units", nargs="+")
    brief.add_argument("--final", action="store_true")
    brief.add_argument("--fingerprint")
    step("check", "check").add_argument("--agent", required=True)
    step("promote", "promote").add_argument("--agent", required=True)
    fold = step("fold", "fold")
    fold.add_argument("--agent", required=True)
    fold.add_argument("--discard")
    step("findings", "close").add_argument("--incomplete")
    step("status", "close")

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
    decide.set_defaults(run=("autonomy", "cmd_decide"))

    pause = commands.add_parser("pause")
    pause.add_argument("--event", action="store_true")
    pause.add_argument("--clear", action="store_true")
    for name in INTENT_FIELDS:
        pause.add_argument(f"--{name}")
    pause.add_argument("--agent", action="append")
    pause.add_argument("--run", action="append", dest="runs")
    pause.add_argument("--repo", default=".")
    pause.set_defaults(run=("autonomy", "cmd_pause"))

    migrate = commands.add_parser("notes-migrate")
    migrate.add_argument("--repo", default=".")
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument("--plan")
    migrate.set_defaults(run=("notes_migrate.apply", "cmd_notes_migrate"))

    args = parser.parse_args()
    if args.command == "capture":
        if args.write_after and (args.pr or args.before or args.after):
            parser.error("--write-after takes no --pr, --before or --after")
        if not args.write_after and not args.pr:
            parser.error("--pr is required unless --write-after is given")
    if args.command == "release-record":
        if args.fix_wave and any(v is not None for v in (args.run_folder, args.base, args.harness)):
            parser.error("--fix-wave takes no --run, --base or --harness")
        if args.fix_wave and not args.issues:
            parser.error("--fix-wave needs at least one --issue")
        if args.issues and not args.fix_wave:
            parser.error("--issue needs --fix-wave")
        if not args.fix_wave and args.run_folder is None:
            parser.error("--run is required unless --fix-wave is given")
    if args.command == "audit-run" and args.audit_step == "brief":
        if args.kind == "hunter" and (not args.units or args.fingerprint or args.final):
            parser.error("--kind hunter takes --units, no --fingerprint or --final")
        if args.kind == "critic" and (args.units or args.fingerprint):
            parser.error("--kind critic takes no --units or --fingerprint")
        if args.kind in ("verifier", "recheck") and (not args.fingerprint or args.units or args.final):
            parser.error(f"--kind {args.kind} takes --fingerprint, no --units or --final")
    module, name = args.run
    return getattr(importlib.import_module(module), name)(args)


if __name__ == "__main__":
    sys.exit(main())

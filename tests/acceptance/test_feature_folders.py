#!/usr/bin/env python3
"""Held-out acceptance flows of the feature folders layout (spec: Feature Folders).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>) it
proves. Seams: `devloop.py notes-migrate`, `status`, `board`, `release-plan`, `release-record`, `release-check` and
`check_setup.py` as processes on scratch repos and scratch notes folders (stdout JSON, exit code, the notes files),
the board server `GET /api/status`, and `bin/install.mjs init` for flow 1. Every name in here is invented.

Fixture choices the spec leaves open:
- Notes path: `DEV_LOOP_NOTES`; flow 1 installs the kit with `init --notes`.
- Each scratch repo has `dev-loop.json` and a committed `.dev-loop/MANIFEST.json` at the `package.json` version (the
  running kit), so no worktree is an older kit unless a case makes it one.
- An `outside_links` item's `line` is the 1-based line number; its `file` ends with the vault-relative path.
- A stop in the middle: one note to rewrite sits in a folder with no write permission, so `--apply` fails with
  `io` after the moves; the permission comes back before the resume.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import Board, git, health, note_text, request, status_note
from feature_folders_support import NewLayout
from release_security_audit_support import (FINDINGS, REPORT, GitRepo, item_text, reasons_named, record_line,
                                            section, todays)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
KIT_VERSION = json.loads((REPO_ROOT / "package.json").read_text())["version"]
NOTES_NAME = "Acme Widgets"
FOLDER = "Features/20261019 Demo Work"
EMPTY_LISTS = ("moves", "frontmatter", "outside_links", "outside_skipped", "repo_links", "git_case", "gitignore",
               "ambiguous", "blocked")
GIT_QUIET = {"GIT_CONFIG_COUNT": "3", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false",
             "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false",
             "GIT_CONFIG_KEY_2": "gc.auto", "GIT_CONFIG_VALUE_2": "0"}

SPEC = ("---\ntype: spec\nfeature: demo-work\ntrack: M\nreview_status: approved\nacceptance:\nshipped:\n---\n\n"
        "# Demo Work\n\n## Goal\n\nA demo for the widget shop.\n")
PLAN = "---\nspec: 20261020 Demo Work.md\n---\n\n# Demo Work Plan\n\n### Task 1: Build it\n"
FRAMING = "---\ntype: note\n---\n\n# Demo work framing\n\nQ1: which widgets? Blue ones.\n"


def put(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)


def old_layout(notes: Path, spec: str = SPEC, plan: str = PLAN) -> None:
    """Oracle 12: a spec, its plan and its framing note in the old folders."""
    put(notes / "specs" / "20261020 Demo Work.md", spec)
    put(notes / "plans" / "20261021 Demo Work.md", plan)
    put(notes / "framing" / "20261019 demo-work.md", FRAMING)


def snapshot(base: Path) -> dict[str, tuple]:
    """Every folder, file and link under base, `.git` left out: kind, then link target or bytes."""
    found: dict[str, tuple] = {}
    if not base.exists():
        return found
    for top, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in [*dirs, *files]:
            path = Path(top) / name
            rel = str(path.relative_to(base))
            if path.is_symlink():
                found[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                found[rel] = ("dir",)
            else:
                found[rel] = ("file", path.read_bytes())
    return found


def run_json(args: list[str], cwd: Path, env: dict[str, str], timeout: float = 180) -> tuple[int, dict]:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise AssertionError(f"{args[1:3]}: stdout is not one JSON object (exit {result.returncode}): "
                             f"{result.stdout[-600:]!r} {result.stderr[-600:]!r}")
    if not isinstance(payload, dict):
        raise AssertionError(f"{args[1:3]}: stdout is not a JSON object: {result.stdout[-600:]!r}")
    return result.returncode, payload


def moves(payload: dict) -> set[tuple[str, str]]:
    return {(m.get("from"), m.get("to")) for m in payload.get("moves") or []}


def blocked(payload: dict, reason: str) -> list[dict]:
    return [b for b in payload.get("blocked") or [] if isinstance(b, dict) and b.get("reason") == reason]


def frontmatter(text: str) -> list[str]:
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"no frontmatter: {text!r}"
    return lines[1:lines.index("---", 1)]


def kept_lines(text: str) -> list[str]:
    """Every line but the kit keys D16 may set."""
    return [line for line in text.splitlines(keepends=True) if not line.startswith(("type:", "feature:", "spec:"))]


def log_notes(notes: Path) -> list[Path]:
    days = {day.replace("-", "") for day in todays()}
    return sorted(p for p in notes.glob("* Notes Migration*.md") if p.name[:8] in days)


def scratch_env(home: Path, tmp: Path, notes: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX", "GIT_"))}
    env.update(HOME=str(home), TMPDIR=str(tmp), XDG_CONFIG_HOME=str(home / ".config"), DEV_LOOP_NOTES=str(notes),
               GIT_CONFIG_NOSYSTEM="1", GIT_EDITOR="true", GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1",
               LANG="C.UTF-8", LC_ALL="C.UTF-8", **GIT_QUIET)
    return env


class Sandbox:
    """A git repo `acme-widgets` with `dev-loop.json` and the kit MANIFEST, and a notes folder
    `<root>/vault/Projects/Acme Widgets` outside it; `vault=True` makes `<root>/vault` a vault root."""

    def __init__(self, root: Path, vault: bool = False) -> None:
        self.root = root
        self.home = root / "home"
        self.vault = root / "vault"
        self.notes = self.vault / "Projects" / NOTES_NAME
        for folder in (self.home, root / "tmp", self.notes):
            folder.mkdir(parents=True)
        if vault:
            (self.vault / ".obsidian").mkdir()
        put(self.home / ".gitconfig", "[user]\n\tname = Robin Quill\n\temail = robin@example.invalid\n"
                                      "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n")
        self.env = scratch_env(self.home, root / "tmp", self.notes)
        self.repo = root / "acme-widgets"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main")
        for path, text in {"dev-loop.json": json.dumps({"version": KIT_VERSION, "harnesses": ["claude"]}) + "\n",
                           ".dev-loop/MANIFEST.json": json.dumps({"version": KIT_VERSION, "files": {}}) + "\n",
                           "GLOSSARY.md": "# Glossary\n\n**Widget**:\nA thing the shop sells.\n",
                           "README.md": "# Acme widgets\n"}.items():
            put(self.repo / path, text)
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "base")

    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def worktree(self, slug: str) -> Path:
        path = self.root / "worktrees" / f"acme-widgets-{slug}"
        self.git(self.repo, "worktree", "add", "-q", "-b", f"feat/{slug}", str(path))
        return path.resolve()

    def migrate(self, *args: str) -> tuple[int, dict]:
        return run_json([sys.executable, str(DEVLOOP), "notes-migrate", *args], self.repo, self.env)


class MigrateCase(unittest.TestCase):
    def sandbox(self, vault: bool = False) -> Sandbox:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Sandbox(Path(tmp.name).resolve(), vault)

    def dry_run(self, migrate: Callable[..., tuple[int, dict]]) -> dict:
        code, payload = migrate()
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("applied"), False, payload)
        self.assertIsInstance(payload.get("plan_hash"), str, payload)
        return payload

    def apply(self, migrate: Callable[..., tuple[int, dict]], plan_hash: str) -> dict:
        code, payload = migrate("--apply", "--plan", plan_hash)
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("applied"), True, payload)
        return payload


class UpgradeFlow(unittest.TestCase):
    def test_f1_a_project_upgrades(self) -> None:
        """F1 (O9, O11, O12, O13, O1): an installed project on the old layout with an open feature at Stage 5. The
        kit still finds the spec and the plan; `check_setup.py` names the old folders and `devloop.py notes-migrate`;
        the dry run lists the 3 moves and changes nothing; `--apply --plan <hash>` moves them into
        `Features/20261019 Demo Work/` with the D16 keys, writes the dated log and leaves no journal; `check_setup.py`
        has no old layout line; `status` finds the feature on the new layout."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        home, notes = root / "home", root / "notes" / NOTES_NAME
        for folder in (home, notes, root / "tmp"):
            folder.mkdir(parents=True)
        put(home / ".gitconfig", "[user]\n\tname = Robin Quill\n\temail = robin@example.invalid\n"
                                 "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n")
        env = scratch_env(home, root / "tmp", notes)
        env.pop("DEV_LOOP_NOTES")
        project = root / "acme-widgets"
        project.mkdir()

        def ok(*args: str, cwd: Path = project) -> str:
            done = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(done.returncode, 0, f"{args}: {done.stdout[-600:]} {done.stderr[-600:]}")
            return done.stdout

        ok("git", "init", "-q", "-b", "main")
        put(project / "GLOSSARY.md", "# Glossary\n\n**Widget**:\nA thing the shop sells.\n")
        ok("node", str(INSTALLER), "init", "--yes", "--harness=claude", f"--notes={notes}")
        ok("git", "add", "-A")
        ok("git", "commit", "-q", "-m", "dev-loop kit")
        scripts = project / ".agents" / "skills" / "dev-loop" / "scripts"
        self.assertTrue((scripts / "devloop.py").is_file(), "init did not install devloop.py")
        old_layout(notes)
        wt = root / "worktrees" / "acme-widgets-demo-work"
        ok("git", "worktree", "add", "-q", "-b", "feat/demo-work", str(wt))
        wt = wt.resolve()
        put(wt / "tests" / "acceptance" / "test_demo_work.py", "def test_flow():\n    assert True\n")

        def setup_lines() -> list[str]:
            done = subprocess.run([sys.executable, str(scripts / "check_setup.py"), "--harness", "claude",
                                   "--home", str(home), "--repo", str(project)], cwd=project, env=env,
                                  capture_output=True, text=True, timeout=180)
            output = done.stdout + done.stderr
            return [line for line in output.splitlines() if "notes-migrate" in line or "old-notes-layout" in line]

        def stage() -> object:
            code, payload = run_json([sys.executable, str(scripts / "devloop.py"), "status", "--feature", "demo-work"],
                                     wt, env)
            self.assertEqual(code, 0, payload)
            return payload.get("stage")

        def migrate(*args: str) -> tuple[int, dict]:
            return run_json([sys.executable, str(scripts / "devloop.py"), "notes-migrate", *args], project, env)

        lines = setup_lines()
        named = [line for line in lines if "devloop.py notes-migrate" in line]
        self.assertTrue(named, lines)
        for entry in ("specs", "plans", "framing"):
            self.assertTrue([line for line in named if entry in line], f"no line names {entry}: {lines}")
        self.assertEqual(stage(), 5, "the transition read of specs/ and plans/ does not find the spec and plan")

        before = snapshot(notes)
        code, dry = migrate()
        self.assertEqual(code, 0, dry)
        self.assertIs(dry.get("applied"), False, dry)
        self.assertIsInstance(dry.get("plan_hash"), str, dry)
        self.assertEqual(moves(dry), {("specs/20261020 Demo Work.md", f"{FOLDER}/Spec.md"),
                                      ("plans/20261021 Demo Work.md", f"{FOLDER}/Plan.md"),
                                      ("framing/20261019 demo-work.md", f"{FOLDER}/Framing.md")}, dry)
        self.assertEqual(snapshot(notes), before, "the dry run changed the notes folder")

        code, done = migrate("--apply", "--plan", dry["plan_hash"])
        self.assertEqual(code, 0, done)
        self.assertIs(done.get("applied"), True, done)
        names = os.listdir(notes)
        for old in ("specs", "plans", "framing", ".notes-migrate.json", ".notes-migrate.lock"):
            self.assertNotIn(old, names)
        folder = notes / FOLDER
        self.assertEqual(sorted(os.listdir(folder)), ["Framing.md", "Plan.md", "Spec.md"])
        self.assertEqual((folder / "Spec.md").read_text(), SPEC)
        framing, plan = (folder / "Framing.md").read_text(), (folder / "Plan.md").read_text()
        self.assertIn("type: framing", frontmatter(framing))
        self.assertIn("feature: demo-work", frontmatter(framing))
        self.assertIn("spec: Spec.md", frontmatter(plan))
        self.assertEqual(kept_lines(framing), kept_lines(FRAMING))
        self.assertEqual(kept_lines(plan), kept_lines(PLAN))
        logs = log_notes(notes)
        self.assertEqual(len(logs), 1, sorted(os.listdir(notes)))
        log = logs[0].read_text()
        for source, target in moves(dry):
            self.assertIn(source.removesuffix(".md"), log)
            self.assertIn(target.removesuffix(".md"), log)

        self.assertEqual(setup_lines(), [])
        self.assertEqual(stage(), 5, "status does not find the feature in its feature folder")


class LinkFlow(MigrateCase):
    def test_f2_links_survive_the_move(self) -> None:
        """F2 (O18, O19, O20, O41, O23): short, vault-root, relative Markdown and relative wikilink forms follow
        the moved notes; a link from a vault note outside the notes folder is listed and left unchanged, and an
        evicted file there is listed in `outside_skipped`."""
        box = self.sandbox(vault=True)
        notes = box.notes
        old_layout(notes, spec=SPEC + "\nSee [note](<../Project Note.md>) and [x](<20261001 Other.md>).\n"
                                      "Back to [[../Project Note]].\n",
                   plan=PLAN + "\nSpec: [[../specs/20261020 Demo Work]]\n")
        put(notes / "specs" / "20261001 Other.md",
            "---\ntype: spec\nfeature: other\ntrack: M\nreview_status: approved\n---\n\n# Other\n")
        put(notes / "Project Note.md",
            "# Acme Widgets\n\n- [[specs/20261020 Demo Work]]\n- [[20261020 Demo Work#Goal|the spec]]\n"
            "- [plan](<plans/20261021 Demo Work.md>)\n- [[Projects/Acme Widgets/specs/20261020 Demo Work|Demo]]\n")
        outside = box.vault / "Other" / "Note.md"
        put(outside, "# Other\n\nSee [[specs/20261020 Demo Work]] for the demo.\n")
        put(box.vault / "Other" / ".Evicted.md.icloud", b"\x00\x01")
        outside_before = outside.read_bytes()

        dry = self.dry_run(box.migrate)
        self.assertEqual(dry.get("blocked"), [], dry)
        listed = [item for item in dry.get("outside_links") or []
                  if str(item.get("file", "")).endswith("Other/Note.md")]
        self.assertEqual(len(listed), 1, dry)
        self.assertEqual(listed[0].get("line"), 3, dry)
        self.assertIn("specs/20261020 Demo Work", str(listed[0].get("link")), dry)
        self.assertTrue([f for f in dry.get("outside_skipped") or [] if ".Evicted.md.icloud" in str(f)], dry)
        self.apply(box.migrate, dry["plan_hash"])

        self.assertEqual((notes / "Project Note.md").read_text().splitlines()[2:], [
            "- [[Features/20261019 Demo Work/Spec|specs/20261020 Demo Work]]",
            "- [[Features/20261019 Demo Work/Spec#Goal|the spec]]",
            "- [plan](<Features/20261019 Demo Work/Plan.md>)",
            "- [[Projects/Acme Widgets/Features/20261019 Demo Work/Spec|Demo]]",
        ])
        spec = (notes / FOLDER / "Spec.md").read_text()
        self.assertIn("See [note](<../../Project Note.md>) and [x](<../20261001 Other/Spec.md>).\n", spec)
        self.assertIn("Back to [[../../Project Note]].\n", spec)
        self.assertIn("Spec: [[Spec|../specs/20261020 Demo Work]]\n", (notes / FOLDER / "Plan.md").read_text())
        self.assertTrue((notes / "Features" / "20261001 Other" / "Spec.md").is_file())
        self.assertEqual(outside.read_bytes(), outside_before)


class AuditFlow(MigrateCase):
    def test_f3_release_audit_across_the_rename(self) -> None:
        """F3 (O11, O26, O7): a record at A and open items in the old `security-audit/run-3`. Before the
        migration the 3 release commands refuse with `old-notes-layout` and create nothing; after it the ids read
        `Run 3`, `release-check` reads the record and the same open items; a new `Run 4` record adds 3 items."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        r = GitRepo(Path(tmp.name).resolve() / "r1")
        old_run = r.notes / "security-audit" / "run-3"
        put(old_run / "run-metadata.json", json.dumps({"source_ref": {"commit": r.A}, "profile": "quick",
                                                       "scope_paths": [], "run_status": "complete"}) + "\n")
        put(old_run / "REPORT.md", REPORT)
        kept_item = "- 2026-10-25 (security, needs-validation, release-security-audit): y"
        r.write_note(f"# {NOTES_NAME}\n\n## Release audits\n\n"
                     f"- 2026-10-25 run-3 at {r.A}: confirmed 1, needs validation 0, hardening 3, profile quick, "
                     "scope whole\n\n## Deferred\n\n- 2026-10-25 (security, confirmed, run-3): x\n"
                     f"{kept_item}\n\n## Gardener\n\n- 2026-10-25 (security, hardening, run-3): 3 hardening notes, "
                     "see security-audit/run-3/REPORT.md\n")
        put(r.notes / "Gardener Log.md",
            "# Gardener Log\n\n- 2026-10-25: hardening in security-audit/run-3/REPORT.md\n")

        skill = r.root / "security-audit-skill"
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            put(skill / name, "process.exit(0)\n")
        r.env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"] = str(skill)

        before = snapshot(r.notes)
        for args in (("release-plan",), ("release-record", "--run", str(old_run)), ("release-check", "--sha", r.A)):
            with self.subTest(command=args[0]):
                code, payload = r.devloop(*args)
                self.assertEqual((code, payload.get("error")), (1, "old-notes-layout"), payload)
                self.assertEqual(snapshot(r.notes), before)

        def migrate(*args: str) -> tuple[int, dict]:
            return run_json([sys.executable, str(DEVLOOP), "notes-migrate", *args], r.repo, r.env)

        self.apply(migrate, self.dry_run(migrate)["plan_hash"])
        self.assertTrue((r.notes / "Security Audit" / "Run 3" / "run-metadata.json").is_file())
        self.assertNotIn("security-audit", os.listdir(r.notes))
        text = r.note_path().read_text()
        self.assertIn(f"- 2026-10-25 Run 3 at {r.A}: confirmed 1", text)
        self.assertIn("- 2026-10-25 (security, confirmed, Run 3): x\n", text)
        self.assertIn("see Security Audit/Run 3/REPORT.md", text)
        self.assertIn(kept_item + "\n", text)
        self.assertIn("hardening in Security Audit/Run 3/REPORT.md", (r.notes / "Gardener Log.md").read_text())

        code, check = r.devloop("release-check", "--sha", r.A)
        self.assertEqual(code, 1, check)
        self.assertIn(f"Run 3 at {r.A}", check.get("record") or "", check)
        self.assertEqual(reasons_named(check, "record-mismatch"), [], check)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        items = [item_text(i) for i in found[0].get("items") or []]
        self.assertEqual(len(items), 2, check)
        self.assertTrue([i for i in items if "(security, confirmed, Run 3): x" in i], check)
        self.assertTrue([i for i in items if "release-security-audit" in i], check)

        run = r.notes / "Security Audit" / "Run 4"
        put(run / "run-metadata.json", json.dumps({"source_ref": {"commit": r.B}, "profile": "standard",
                                                   "scope_paths": [], "run_status": "complete"}) + "\n")
        for name, text_ in (("findings.json", json.dumps(FINDINGS) + "\n"), ("coverage-ledger.json", "{}\n"),
                            ("REPORT.md", REPORT), ("FINDINGS-DETAIL.md", "# Findings\n"),
                            ("NEEDS-VALIDATION.md", "# Needs validation\n")):
            put(run / name, text_)
        code, recorded = r.devloop("release-record", "--run", str(run))
        self.assertEqual(code, 0, recorded)
        text = r.note_path().read_text()
        bullets = [line for line in section(text, "## Release audits") if line.startswith("- ")]
        today = bullets[-1][2:12]
        self.assertIn(today, todays(), bullets)
        self.assertEqual(bullets[-1], record_line(4, r.B, profile="standard", date=today, confirmed=1, needs=2,
                                                  hardening=3))
        deferred = section(text, "## Deferred")
        self.assertEqual(len([line for line in deferred if "(security, confirmed, Run 4): " in line]), 1, deferred)
        self.assertEqual(len([line for line in deferred if "(security, needs-validation, Run 4): " in line]), 2,
                         deferred)
        gardener = [line for line in section(text, "## Gardener") if "(security, hardening, Run 4)" in line]
        self.assertEqual(len(gardener), 1, text)
        self.assertIn("see Security Audit/Run 4/REPORT.md", gardener[0])

        code, check = r.devloop("release-check", "--sha", r.B)
        self.assertEqual(code, 1, check)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        items = [item_text(i) for i in found[0].get("items") or []]
        self.assertEqual(len(items), 5, check)
        self.assertEqual(len([i for i in items if "Run 4)" in i]), 3, check)


class BoardFlow(NewLayout, Board):
    def setUp(self) -> None:
        super().setUp()
        (self.vault / ".obsidian").mkdir()
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        try:
            self.board("--stop-server", timeout=30)
        except (AssertionError, subprocess.TimeoutExpired):
            pass
        self.kill_stray_server()

    def test_f4_board_across_kit_versions(self) -> None:
        """F4 (O25, O5): this project's notes on both board folder names migrate into `Dev Loop Board/Features/`;
        another project on an older kit keeps `dev-loop-board/features/`; one server lists the notes of both
        roots, each once."""
        put(self.repo / ".dev-loop" / "MANIFEST.json", json.dumps({"version": KIT_VERSION, "files": {}}) + "\n")
        git(self.repo, "commit", "-q", "-am", "kit")
        texts = {"a.md": note_text(status_note(feature="alpha-a", title="Alpha A")),
                 "c.md": note_text(status_note(feature="gamma-c", title="Gamma C")),
                 "b.md": note_text(status_note(feature="beta-b", title="Beta B"))}
        put(self.notes / "dev-loop-board" / "features" / "a.md", texts["a.md"])
        put(self.notes / "dev-loop-board" / "c.md", texts["c.md"])
        put(self.features_dir / "b.md", texts["b.md"])

        def migrate(*args: str) -> dict:
            code, payload = run_json([sys.executable, str(DEVLOOP), "notes-migrate", *args], self.repo, self.env)
            self.assertEqual(code, 0, payload)
            return payload

        migrate("--apply", "--plan", migrate()["plan_hash"])
        self.assertNotIn("dev-loop-board", os.listdir(self.notes))
        self.assertEqual(sorted(os.listdir(self.features_dir)), ["a.md", "b.md", "c.md"])
        for name, text in texts.items():
            self.assertEqual((self.features_dir / name).read_text(), text, name)

        notes_two = self.root / "vault-two" / "Zephyr"
        put(notes_two / "dev-loop-board" / "features" / "z.md",
            note_text(status_note(project="zephyr-app", feature="zephyr-old", title="Zephyr Old")))
        origin_two = self.root / "remotes" / "zephyr-app.git"
        git(self.root, "init", "-q", "--bare", str(origin_two))
        repo_two = self.root / "zephyr-app"
        self.make_repo(repo_two, origin_two)
        self.worktree("tiny", prefix="fix")
        self.worktree("tiny", prefix="fix", repo=repo_two)
        self.ok(serve=True, timeout=90)
        self.ok(cwd=repo_two, env={**self.env, "DEV_LOOP_NOTES": str(notes_two)}, serve=True, timeout=90)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            found = health(self.port) or {}
            if notes_two in [Path(p).resolve() for p in found.get("roots") or []]:
                break
            time.sleep(0.1)
        status, _, body = request(self.port, "/api/status")
        self.assertEqual(status, 200, body)
        listed = [f.get("feature") for f in json.loads(body)["features"]]
        for feature in ("alpha-a", "beta-b", "gamma-c", "zephyr-old"):
            self.assertEqual(listed.count(feature), 1, f"{feature}: {listed}")


class ResumeFlow(MigrateCase):
    def test_f5_stop_in_the_middle_then_resume(self) -> None:
        """F5 (O28, O27, O43): `--apply` stops with `io` after the moves, its journal stays; the same command again
        resumes the journal's plan, writes the notes left, keeps the written ones (no second `../`) and removes
        the journal. A dry run and an `--apply` after that change nothing and write no second log."""
        box = self.sandbox()
        notes = box.notes
        old_layout(notes, spec=SPEC + "\nSee [note](<../Project Note.md>).\n")
        put(notes / "Project Note.md", "# Acme Widgets\n\n- [[specs/20261020 Demo Work]]\n")
        locked = notes / "Zeta"
        put(locked / "Links.md", "# Links\n\n- [[specs/20261020 Demo Work]]\n")

        def writable(on: bool) -> None:
            (locked / "Links.md").chmod(stat.S_IRUSR | (stat.S_IWUSR if on else 0))
            locked.chmod(stat.S_IRWXU if on else stat.S_IRUSR | stat.S_IXUSR)

        writable(False)
        self.addCleanup(writable, True)
        dry = self.dry_run(box.migrate)
        code, stopped = box.migrate("--apply", "--plan", dry["plan_hash"])
        self.assertEqual((code, stopped.get("error")), (1, "io"), stopped)
        self.assertTrue((notes / ".notes-migrate.json").is_file(), "the journal is gone after the stop")
        self.assertTrue((notes / FOLDER / "Spec.md").is_file(), "the moves did not run before the stop")

        writable(True)
        self.apply(box.migrate, dry["plan_hash"])
        names = os.listdir(notes)
        self.assertNotIn(".notes-migrate.json", names)
        self.assertNotIn(".notes-migrate.lock", names)
        self.assertIn("See [note](<../../Project Note.md>).\n", (notes / FOLDER / "Spec.md").read_text())
        link = "- [[Features/20261019 Demo Work/Spec|specs/20261020 Demo Work]]\n"
        self.assertIn(link, (notes / "Project Note.md").read_text())
        self.assertIn(link, (locked / "Links.md").read_text())
        self.assertEqual(len(log_notes(notes)), 1, names)

        before = snapshot(notes)
        code, payload = box.migrate("--apply", "--plan", self.dry_run(box.migrate)["plan_hash"])
        self.assertEqual(code, 0, payload)
        for key in EMPTY_LISTS:
            self.assertEqual(payload.get(key), [], f"{key}: {payload}")
        self.assertEqual(snapshot(notes), before)


class AbuseFlow(MigrateCase):
    def test_f6_abuse_cases_stop_the_run_with_no_change(self) -> None:
        """F6 (O29, O30, O31, O32, O33): an existing target, a symbolic link, an iCloud placeholder, an older kit
        worktree, an open ledger and an audit folder whose new name is not ignored each block the dry run and the
        `--apply` of its plan: exit 1, the `blocked` reason, nothing changed."""

        def existing_target(box: Sandbox) -> list[Path]:
            put(box.notes / FOLDER / "Spec.md", "# Already here\n")
            return []

        def symbolic_link(box: Sandbox) -> list[Path]:
            real = box.root / "elsewhere" / "specs"
            real.parent.mkdir()
            shutil.move(str(box.notes / "specs"), str(real))
            (box.notes / "specs").symlink_to(real)
            return [real]

        def placeholder(box: Sandbox) -> list[Path]:
            (box.notes / "specs" / "20261020 Demo Work.md").unlink()
            put(box.notes / "specs" / ".20261020 Demo Work.md.icloud", b"\x00\x01")
            return []

        def old_kit_worktree(box: Sandbox) -> list[Path]:
            wt = box.worktree("older")
            put(wt / ".dev-loop" / "MANIFEST.json", json.dumps({"version": "0.0.1", "files": {}}) + "\n")
            return [wt]

        def open_ledger(box: Sandbox) -> list[Path]:
            wt = box.worktree("demo-work")
            put(wt / ".superpowers" / "sdd" / "20261020 Demo Work" / "progress.md", "# Ledger\nTask 1: complete\n")
            return [wt]

        def unignored_audit_folder(box: Sandbox) -> list[Path]:
            box.git(box.vault, "init", "-q")
            put(box.vault / "Projects" / ".gitignore", "security-audit/\n")
            put(box.notes / "security-audit" / "run-1" / "REPORT.md", "# Report\n")
            return []

        cases = (("target-exists", existing_target), ("link", symbolic_link), ("placeholder", placeholder),
                 ("old-kit-worktree", old_kit_worktree), ("open-ledger", open_ledger),
                 ("gitignore", unignored_audit_folder))
        for reason, make in cases:
            with self.subTest(reason):
                box = self.sandbox()
                old_layout(box.notes)
                named = make(box)
                watched = [box.notes, *[p for p in named if p != box.notes]]
                before = {str(p): snapshot(p) for p in watched}
                code, dry = box.migrate()
                self.assertEqual(code, 1, dry)
                self.assertTrue(blocked(dry, reason), dry)
                self.assertIsInstance(dry.get("plan_hash"), str, dry)
                code, payload = box.migrate("--apply", "--plan", dry["plan_hash"])
                self.assertEqual(code, 1, payload)
                items = blocked(payload, reason)
                self.assertTrue(items, payload)
                self.assertNotEqual(payload.get("applied"), True, payload)
                self.assertEqual({str(p): snapshot(p) for p in watched}, before)
                if reason == "link":
                    self.assertTrue((box.notes / "specs").is_symlink())
                if reason in ("old-kit-worktree", "open-ledger"):
                    self.assertTrue([i for i in items if named[0].name in str(i.get("path"))], payload)
                if reason == "gitignore":
                    self.assertTrue([g for g in payload.get("gitignore") or [] if "Security Audit/" in str(g)], payload)


if __name__ == "__main__":
    unittest.main()

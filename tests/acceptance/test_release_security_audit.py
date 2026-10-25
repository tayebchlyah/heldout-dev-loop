#!/usr/bin/env python3
"""Held-out oracle tests for the release security audit (spec: Release Security Audit).

One test per `## Acceptance oracle` line (O<n>), the variants a line lists as subtests, plus one test per
`## Acceptance flows` item (F<n>). The tests use only the declared seams: the stdout JSON and exit code of
`devloop.py release-check`, `release-plan` and `release-record`; `skill/scripts/sandbox-run.py` (its stdout,
stderr, exit code and the host scratch folder) against a real Docker engine; the kit `git-guard.sh` hooks fed
hook JSON on stdin; `check_setup.py` output; `bin/install.mjs init` and `upgrade`; the kit text. Every name in
the temp repos is invented.

Fixture choices the spec leaves open:
- Notes path: `DEV_LOOP_NOTES` (release commands), the `init --notes` local file (guard, setup checks).
- The `security-audit` validators are stub `.cjs` files in a folder named by `DEVLOOP_TEST_SECURITY_AUDIT_DIR`.
- `findings.json` verdicts are `confirmed`, `needs_validation`, `rejected` (the skill's schema names).
- `<today>` is the local date or the UTC date (a run at midnight may see either).
- Docker cases use `busybox:1.37` pinned by digest, pulled once per suite run. They skip only when
  `docker info` fails and `DEVLOOP_REQUIRE_DOCKER` is unset; with it set, a missing engine fails them.
- O40 "holds only `f`": the only file that is not a folder is the regular file `f` (the runner itself makes
  the `home`, `tmp` and `cache` folders under `/scratch`).
"""

from __future__ import annotations

import datetime as dt
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
_GIT_CONFIG_COUNT = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
if not any(
    os.environ.get(f"GIT_CONFIG_KEY_{i}") == "core.fsmonitor" and os.environ.get(f"GIT_CONFIG_VALUE_{i}") == "false"
    for i in range(_GIT_CONFIG_COUNT)
):
    os.environ.update({
        f"GIT_CONFIG_KEY_{_GIT_CONFIG_COUNT}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{_GIT_CONFIG_COUNT}": "false",
        "GIT_CONFIG_COUNT": str(_GIT_CONFIG_COUNT + 1),
    })

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
SANDBOX = REPO_ROOT / "skill" / "scripts" / "sandbox-run.py"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
SKILL = REPO_ROOT / "skill" / "SKILL.md"
CLAUDE_REF = REPO_ROOT / "skill" / "references" / "claude.md"
CODEX_REF = REPO_ROOT / "skill" / "references" / "codex.md"
AUDIT_SKILL = REPO_ROOT / "skills" / "release-audit" / "SKILL.md"
AUDIT_OPENAI = REPO_ROOT / "skills" / "release-audit" / "agents" / "openai.yaml"
AGENTS = REPO_ROOT / "AGENTS.md"

IMAGE = "busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
IMAGE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ABSENT_IMAGES = ("busybox:1.31.1-uclibc", "busybox:1.30.0-musl", "busybox:1.29.3-glibc")
SANDBOX_LABEL = "label=devloop.sandbox=1"

APP_A = "def colour(theme):\n    return 'red'\n"
APP_B = "def colour(theme):\n    return theme or 'blue'\n"
BASE_CONFIG = {"version": "2.1.1", "harnesses": ["claude", "codex"]}
NOTES_NAME = "Acme Widgets"


def put(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)


def todays() -> set[str]:
    return {dt.date.today().isoformat(), dt.datetime.now(dt.timezone.utc).date().isoformat()}


def base_env(home: Path, tmp: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))}
    env.update({"HOME": str(home), "TMPDIR": str(tmp), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"})
    return env


def tools_dir(root: Path, *names: str) -> Path:
    """A folder with links to the named tools only (no docker, no node unless named)."""
    folder = root / ("tools-" + "-".join(names))
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        found = sys.executable if name == "python3" else shutil.which(name)
        if found and not (folder / name).exists():
            os.symlink(found, folder / name)
    return folder


def record_line(run: int, sha: str, scope: str = "whole", profile: str = "quick", date: str = "2026-10-25",
                confirmed: int = 0, needs: int = 0, hardening: int = 0) -> str:
    return (f"- {date} run-{run} at {sha}: confirmed {confirmed}, needs validation {needs}, "
            f"hardening {hardening}, profile {profile}, scope {scope}")


def note_text(records: list[str] | None = None, deferred: list[str] | None = None,
              gardener: list[str] | None = None) -> str:
    parts = [f"# {NOTES_NAME}\n\nThe widget service.\n"]
    if records is not None:
        parts.append("## Release audits\n\n" + "".join(line + "\n" for line in records))
    if deferred is not None:
        parts.append("## Deferred\n\n" + "".join(line + "\n" for line in deferred))
    if gardener is not None:
        parts.append("## Gardener\n\n" + "".join(line + "\n" for line in gardener))
    return "\n".join(parts)


def section(text: str, heading: str) -> list[str]:
    """The lines of a `## ` section, up to the next `## ` heading."""
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return []
    out = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        out.append(line)
    return out


def subsection(lines: list[str], heading: str) -> list[str]:
    try:
        start = lines.index(heading)
    except ValueError:
        return []
    out = []
    for line in lines[start + 1:]:
        if line.startswith("#"):
            break
        out.append(line)
    return out


def item_text(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("line", json.dumps(item)))
    return str(item)


def reasons_named(payload: dict, name: str) -> list[dict]:
    return [r for r in payload.get("reasons") or [] if isinstance(r, dict) and r.get("reason") == name]


def hook_decision(result: subprocess.CompletedProcess[str]) -> tuple[str | None, str]:
    """The permissionDecision and the reason text a guard gave (JSON on stdout, else exit 2 and stderr)."""
    decision, reason = None, ""
    try:
        out = json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:
        out = {}
    if isinstance(out, dict):
        specific = out.get("hookSpecificOutput") or {}
        decision = specific.get("permissionDecision") or out.get("decision")
        reason = str(specific.get("permissionDecisionReason") or out.get("reason") or "")
    if decision is None and result.returncode == 2:
        decision = "deny"
    return decision, reason + "\n" + result.stderr


class GitRepo:
    """A git repo with commits A then B (B changes src/app.py), and a notes folder outside it."""

    def __init__(self, root: Path, config: dict | None = None) -> None:
        self.root = root
        self.home = root / "home"
        self.tmp = root / "tmp"
        self.notes = root / "notes" / NOTES_NAME
        for folder in (self.home, self.tmp, self.notes):
            folder.mkdir(parents=True, exist_ok=True)
        self.env = base_env(self.home, self.tmp)
        self.env["DEV_LOOP_NOTES"] = str(self.notes)
        self.repo = root / "acme-widgets"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        self.Z = self.commit({"dev-loop.json": json.dumps(config or BASE_CONFIG, indent=2) + "\n",
                              "README.md": "# Acme widgets\n", "src/app.py": "pass\n",
                              "tests/unit/test_x.py": "def test_x():\n    assert 1 == 1\n"}, "Z")
        self.A = self.commit({"src/app.py": APP_A}, "A")
        self.B = self.commit({"src/app.py": APP_B}, "B")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def commit(self, files: dict[str, str], message: str) -> str:
        for path, text in files.items():
            put(self.repo / path, text)
        self.git("add", "-A", "--", *files)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def side_commit(self, start: str, files: dict[str, str], message: str) -> str:
        """A commit on its own branch from `start`; the checkout returns to main."""
        branch = f"side-{uuid.uuid4().hex[:8]}"
        self.git("checkout", "-q", "-b", branch, start)
        sha = self.commit(files, message)
        self.git("checkout", "-q", "main")
        return sha

    def tag(self, name: str, sha: str) -> None:
        self.git("tag", "-a", "-m", f"Release {name}", name, sha)

    def note_path(self, name: str = "Project Note.md") -> Path:
        return self.notes / name

    def write_note(self, text: str, name: str = "Project Note.md") -> Path:
        path = self.note_path(name)
        put(path, text)
        return path

    def run_folder(self, n: int, sha: str, status: str = "complete", profile: str = "quick") -> Path:
        folder = self.notes / "security-audit" / f"run-{n}"
        folder.mkdir(parents=True, exist_ok=True)
        put(folder / "run-metadata.json", json.dumps({"source_ref": {"commit": sha}, "profile": profile,
                                                      "scope_paths": [], "run_status": status}, indent=2) + "\n")
        return folder

    def devloop(self, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None,
                timeout: float = 120) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd or self.repo,
                                env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=timeout)
        try:
            payload = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {"unparsed_stdout": result.stdout}
        if not isinstance(payload, dict):
            payload = {"unparsed_stdout": result.stdout}
        payload.setdefault("_stderr", result.stderr)
        return result.returncode, payload


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.count = 0

    def repo(self, config: dict | None = None) -> GitRepo:
        self.count += 1
        return GitRepo(self.root / f"r{self.count}", config)

    def audited(self, deferred: list[str] | None = None, gardener: list[str] | None = None) -> GitRepo:
        """The release-check base case: tag v1.0.0 on A, record line at A with its run folder."""
        r = self.repo()
        r.tag("v1.0.0", r.A)
        r.run_folder(1, r.A)
        r.write_note(note_text([record_line(1, r.A)], deferred if deferred is not None else [], gardener))
        return r


# ---------------------------------------------------------------------------------------------------------------
# release-check (O1 to O15)
# ---------------------------------------------------------------------------------------------------------------

class ReleaseCheckTests(Case):

    def check(self, r: GitRepo, *args: str) -> tuple[int, dict]:
        return r.devloop("release-check", *args)

    def test_o01_audited_tag_passes(self) -> None:
        r = self.audited()
        code, payload = self.check(r, "--tag", "v1.0.0")
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertEqual(payload.get("reasons"), [], payload)
        self.assertEqual(payload.get("target"), r.A, payload)
        self.assertEqual(payload.get("tag"), "v1.0.0", payload)
        self.assertIn(f"run-1 at {r.A}", payload.get("record") or "", payload)

    def test_o02_no_release_audits_section(self) -> None:
        r = self.repo()
        r.tag("v1.0.0", r.A)
        r.run_folder(1, r.A)
        r.write_note(note_text(None, []))
        code, payload = self.check(r, "--tag", "v1.0.0")
        self.assertEqual(code, 1, payload)
        self.assertIs(payload.get("ok"), False, payload)
        self.assertTrue(reasons_named(payload, "no-audit"), payload)

    def test_o03_test_and_doc_changes_after_the_record_pass(self) -> None:
        r = self.audited()
        c = r.side_commit(r.A, {"tests/unit/test_x.py": "def test_x():\n    assert 2 == 2\n",
                                "README.md": "# Acme widgets\n\nNow with docs.\n"}, "C")
        r.tag("v1.1.0", c)
        code, payload = self.check(r, "--tag", "v1.1.0")
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)

    def test_o04_code_change_after_the_record_is_stale(self) -> None:
        r = self.audited()
        r.tag("v1.1.0", r.B)
        code, payload = self.check(r, "--tag", "v1.1.0")
        self.assertEqual(code, 1, payload)
        stale = reasons_named(payload, "stale-audit")
        self.assertTrue(stale, payload)
        self.assertEqual(stale[0].get("paths"), ["src/app.py"], payload)

    def test_o05_record_not_an_ancestor_is_stale_with_every_path(self) -> None:
        r = self.repo()
        x = r.side_commit(r.A, {"lib/side.py": "SIDE = 1\n"}, "X")
        r.run_folder(1, x)
        r.write_note(note_text([record_line(1, x)], []))
        r.tag("v1.1.0", r.B)
        code, payload = self.check(r, "--tag", "v1.1.0")
        self.assertEqual(code, 1, payload)
        stale = reasons_named(payload, "stale-audit")
        self.assertTrue(stale, payload)
        self.assertEqual(sorted(stale[0].get("paths") or []), ["lib/side.py", "src/app.py"], payload)

    def test_o06_last_record_line_is_current(self) -> None:
        r = self.repo()
        x = r.side_commit(r.Z, {"lib/old.py": "OLD = 1\n"}, "old")
        r.run_folder(1, x)
        r.run_folder(2, r.A, profile="standard")
        r.write_note(note_text([record_line(1, x), record_line(2, r.A, scope=f"{x}..{r.A}", profile="standard")], []))
        r.tag("v1.0.0", r.A)
        code, payload = self.check(r, "--tag", "v1.0.0")
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertIn("run-2", payload.get("record") or "", payload)

    def test_o07_record_without_a_matching_complete_run_folder(self) -> None:
        for case in ("other commit", "incomplete", "no run folder"):
            with self.subTest(case=case):
                r = self.repo()
                r.tag("v1.0.0", r.A)
                if case == "other commit":
                    r.run_folder(1, r.B)
                elif case == "incomplete":
                    r.run_folder(1, r.A, status="incomplete")
                r.write_note(note_text([record_line(1, r.A)], []))
                code, payload = self.check(r, "--tag", "v1.0.0")
                self.assertEqual(code, 1, payload)
                self.assertTrue(reasons_named(payload, "record-mismatch"), payload)

    def assert_open(self, r: GitRepo, line: str | None) -> None:
        code, payload = self.check(r, "--tag", "v1.0.0")
        found = reasons_named(payload, "open-security")
        if line is None:
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("reasons"), [], payload)
        else:
            self.assertEqual(code, 1, payload)
            self.assertTrue(found, payload)
            items = [item_text(i) for i in found[0].get("items") or []]
            self.assertTrue([i for i in items if line.lstrip("- ").strip() in i], payload)

    def test_o08_needs_validation_rulings(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, run-1): x"
        for ruling, still_open in ((None, True), ("  - Ruling 2026-10-26: accept the risk", False),
                                   ("  - Ruling 2026-10-26: close not reachable", False),
                                   ("  - Ruling 2026-10-26: fix in a slice", True)):
            with self.subTest(ruling=ruling):
                r = self.audited([item] + ([ruling] if ruling else []))
                self.assert_open(r, item if still_open else None)

    def test_o09_confirmed_item_is_not_closed_by_a_ruling(self) -> None:
        item = "- 2026-10-25 (security, confirmed, run-1): x"
        r = self.audited([item, "  - Ruling 2026-10-26: accept"])
        self.assert_open(r, item)

    def test_o10_checkbox_item_under_a_subsection(self) -> None:
        item = "- [ ] 2026-10-25 (security, needs-validation, review-wave-measurement): y"
        r = self.audited(["### Some gate", "", item])
        self.assert_open(r, "2026-10-25 (security, needs-validation, review-wave-measurement): y")

    def test_o11_unparsed_security_bullet_blocks(self) -> None:
        line = "- Security: the token is logged"
        with self.subTest(case="in Deferred"):
            r = self.audited([line])
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual(code, 1, payload)
            unparsed = reasons_named(payload, "unparsed-security")
            self.assertTrue(unparsed, payload)
            self.assertTrue([i for i in unparsed[0].get("lines") or [] if "the token is logged" in item_text(i)],
                            payload)
        with self.subTest(case="in Gardener"):
            r = self.audited([], [line])
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual((code, payload.get("reasons")), (0, []), payload)
        with self.subTest(case="word boundary"):
            r = self.audited(["- Insecurely named thing"])
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual((code, payload.get("reasons")), (0, []), payload)

    def test_o12_other_tag_name_is_skipped(self) -> None:
        r = self.audited()
        r.tag("release-1", r.B)
        code, payload = self.check(r, "--tag", "release-1")
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertEqual(payload.get("skipped"), "not-a-release-tag", payload)

    def test_o13_sha_without_a_tag(self) -> None:
        r = self.audited()
        code, payload = self.check(r, "--sha", r.A)
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.assertEqual(payload.get("reasons"), [], payload)
        self.assertEqual(payload.get("target"), r.A, payload)
        self.assertIn("tag", payload)
        self.assertIsNone(payload.get("tag"), payload)

    def test_o14_project_note_rule(self) -> None:
        good = note_text([record_line(1, "{sha}")], [])
        bare = note_text(None, [])
        with self.subTest(case="folder-named note only"):
            r = self.audited()
            r.note_path().unlink()
            r.write_note(good.replace("{sha}", r.A), f"{NOTES_NAME}.md")
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual((code, payload.get("ok")), (0, True), payload)
        with self.subTest(case="both: Project Note.md wins"):
            r = self.audited()
            r.write_note(bare, f"{NOTES_NAME}.md")
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual((code, payload.get("ok")), (0, True), payload)
            r.write_note(bare)
            r.write_note(good.replace("{sha}", r.A), f"{NOTES_NAME}.md")
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual(code, 1, payload)
            self.assertTrue(reasons_named(payload, "no-audit"), payload)
        with self.subTest(case="neither"):
            r = self.audited()
            r.note_path().unlink()
            code, payload = self.check(r, "--tag", "v1.0.0")
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload.get("error"), "no-project-note", payload)
            self.assertNotIn("ok", payload)

    def test_o15_bad_tag_and_usage(self) -> None:
        r = self.audited()
        code, payload = self.check(r, "--tag", "v9")
        self.assertEqual(code, 1, payload)
        self.assertTrue(payload.get("error"), payload)
        self.assertNotIn("ok", payload)
        code, payload = self.check(r, "--tag", "v1.0.0", "--sha", r.A)
        self.assertEqual(code, 2, payload)
        code, payload = self.check(r)
        self.assertEqual(code, 2, payload)


# ---------------------------------------------------------------------------------------------------------------
# release-plan (O16 to O20)
# ---------------------------------------------------------------------------------------------------------------

class ReleasePlanTests(Case):

    def audit_dir(self, r: GitRepo) -> Path:
        return r.notes / "security-audit"

    def listing(self, r: GitRepo) -> list[str]:
        folder = self.audit_dir(r)
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def test_o16_first_audit(self) -> None:
        r = self.repo()
        r.write_note(note_text(None, []))
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 0, payload)
        expected = (self.audit_dir(r) / "run-1").resolve()
        self.assertEqual(payload.get("run"), "run-1", payload)
        self.assertIs(payload.get("first"), True, payload)
        self.assertEqual(payload.get("profile"), "quick", payload)
        self.assertEqual(payload.get("budget"), 100, payload)
        self.assertEqual(payload.get("scope"), "whole", payload)
        self.assertIn("base", payload)
        self.assertIsNone(payload.get("base"), payload)
        self.assertEqual(payload.get("prior_runs"), [], payload)
        self.assertEqual(payload.get("head"), r.B, payload)
        self.assertTrue(Path(payload.get("output") or "").is_absolute(), payload)
        self.assertEqual(Path(payload["output"]).resolve(), expected, payload)
        self.assertTrue(expected.is_dir(), "the run-1 folder was not created")

    def test_o17_later_audit(self) -> None:
        r = self.repo()
        (self.audit_dir(r) / "run-1").mkdir(parents=True)
        r.run_folder(3, r.A)
        r.write_note(note_text([record_line(3, r.A)], []))
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload.get("run"), "run-4", payload)
        self.assertIs(payload.get("first"), False, payload)
        self.assertEqual(payload.get("profile"), "standard", payload)
        self.assertIn("budget", payload)
        self.assertIsNone(payload.get("budget"), payload)
        self.assertEqual(payload.get("scope"), f"{r.A}..{r.B}", payload)
        self.assertEqual(payload.get("base"), r.A, payload)
        self.assertEqual([Path(p).resolve() for p in payload.get("prior_runs") or []],
                         [(self.audit_dir(r) / "run-1").resolve(), (self.audit_dir(r) / "run-3").resolve()], payload)
        self.assertTrue((self.audit_dir(r) / "run-4").is_dir())

    def test_o17a_merge_base_and_overrides(self) -> None:
        r = self.repo()
        rewritten = r.side_commit(r.A, {"src/other.py": "OTHER = 1\n"}, "R")
        r.run_folder(1, rewritten)
        r.write_note(note_text([record_line(1, rewritten)], []))
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload.get("base"), r.A, payload)
        self.assertEqual(payload.get("scope"), f"{r.A}..{r.B}", payload)
        code, payload = r.devloop("release-plan", "--profile", "deep", "--budget", "40")
        self.assertEqual(code, 0, payload)
        self.assertEqual((payload.get("profile"), payload.get("budget")), ("deep", 40), payload)
        code, payload = r.devloop("release-plan", "--budget", "0")
        self.assertEqual(code, 2, payload)

    def test_o18_icloud_placeholder(self) -> None:
        r = self.repo()
        r.write_note(note_text(None, []))
        put(self.audit_dir(r) / "run-1" / ".findings.json.icloud", b"\x00")
        before = self.listing(r)
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("error"), "placeholder", payload)
        self.assertTrue([f for f in payload.get("files") or [] if ".findings.json.icloud" in str(f)], payload)
        self.assertEqual(self.listing(r), before)

    def test_o19_dirty_worktree(self) -> None:
        r = self.repo()
        r.write_note(note_text(None, []))
        put(r.repo / "src" / "app.py", APP_B + "# local edit\n")
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("error"), "dirty", payload)
        self.assertTrue([p for p in payload.get("paths") or [] if "src/app.py" in str(p)], payload)
        self.assertEqual(self.listing(r), [])
        r.git("checkout", "--", "src/app.py")
        put(r.repo / "scratch-notes.txt", "untracked\n")
        code, payload = r.devloop("release-plan")
        self.assertEqual(code, 0, payload)

    def test_o20_sandbox_block(self) -> None:
        for images in (None, {"t": IMAGE}):
            with self.subTest(images=images):
                config = dict(BASE_CONFIG)
                if images is not None:
                    config["security_audit"] = {"images": images}
                r = self.repo(config)
                r.write_note(note_text(None, []))
                path = tools_dir(self.root, "git")
                code, payload = r.devloop("release-plan", env={"PATH": str(path)})
                self.assertEqual(code, 0, payload)
                sandbox = payload.get("sandbox") or {}
                self.assertIs(sandbox.get("engine"), False, payload)
                self.assertEqual(sandbox.get("images"), images or {}, payload)
                runner = sandbox.get("runner") or ""
                self.assertTrue(Path(runner).is_absolute(), payload)
                self.assertTrue(runner.endswith("scripts/sandbox-run.py"), payload)


# ---------------------------------------------------------------------------------------------------------------
# release-record (O21 to O31)
# ---------------------------------------------------------------------------------------------------------------

FINDINGS = [
    {"verdict": "confirmed", "fingerprint": "f-c", "title": "Session token written to the debug log",
     "blockers": []},
    {"verdict": "needs_validation", "fingerprint": "f-n1", "title": "Upload path join may leave the media root",
     "blockers": ["fact one", "fact two"]},
    {"verdict": "needs_validation", "fingerprint": "f-n2", "title": "Unbounded body size on the import route",
     "blockers": ["fact one", "fact two"]},
    {"verdict": "rejected", "fingerprint": "f-r", "title": "Rejected lead about the colour cache",
     "blockers": []},
]
REPORT = ("# Security audit report\n\n## Summary\n\nTwo subsystems.\n\n### Hardening (not findings)\n\n"
          "- Pin the base image.\n- Add a body size limit.\n- Drop the debug flag.\n\n## Coverage\n\n- not counted\n")
REPORT_NO_HARDENING = "# Security audit report\n\n## Summary\n\nTwo subsystems.\n\n### Hardening (not findings)\n\nNone.\n"
BASE_NOTE = note_text([], ["- Rename the parser module"], ["- 2026-10-01 (cleanup): tidy the fixtures"])


class RecordCase(Case):

    def setup_record(self, findings: list[dict] | None = None, status: str = "complete", report: str = REPORT,
                     note: str | None = BASE_NOTE, validator_exit: int = 0) -> tuple[GitRepo, Path]:
        r = self.repo()
        run = r.run_folder(2, r.A, status=status, profile="standard")
        put(run / "findings.json", json.dumps(FINDINGS if findings is None else findings, indent=2) + "\n")
        put(run / "coverage-ledger.json", "{}\n")
        put(run / "REPORT.md", report)
        put(run / "FINDINGS-DETAIL.md", "# Findings\n")
        put(run / "NEEDS-VALIDATION.md", "# Needs validation\n")
        skill = r.root / "security-audit-skill"
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            put(skill / name, f"process.exit({validator_exit})\n")
        r.env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"] = str(skill)
        if note is not None:
            r.write_note(note)
        return r, run

    def record(self, r: GitRepo, run: Path, *extra: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
        return r.devloop("release-record", "--run", str(run), *extra, env=env)

    def today_of(self, line: str) -> str:
        match = re.match(r"- (\d{4}-\d{2}-\d{2}) ", line)
        self.assertIsNotNone(match, line)
        self.assertIn(match.group(1), todays(), line)
        return match.group(1)

    def new_subsection(self, r: GitRepo) -> list[str]:
        deferred = section(r.note_path().read_text(), "## Deferred")
        heads = [line for line in deferred if re.fullmatch(r"### Release audit run-2, \d{4}-\d{2}-\d{2}", line)]
        self.assertEqual(len(heads), 1, deferred)
        self.assertIn(heads[0].rsplit(", ", 1)[1], todays())
        return [line for line in subsection(deferred, heads[0]) if line.strip()]


class ReleaseRecordTests(RecordCase):

    def test_o21_record_line_with_base(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run, "--base", r.Z)
        self.assertEqual(code, 0, payload)
        bullets = [line for line in section(r.note_path().read_text(), "## Release audits") if line.startswith("- ")]
        self.assertTrue(bullets, r.note_path().read_text())
        today = self.today_of(bullets[-1])
        self.assertEqual(bullets[-1], record_line(2, r.A, scope=f"{r.Z}..{r.A}", profile="standard", date=today,
                                                  confirmed=1, needs=2, hardening=3))
        self.assertIn(f"run-2 at {r.A}", payload.get("record") or "", payload)

    def test_o22_record_line_without_base(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        bullets = [line for line in section(r.note_path().read_text(), "## Release audits") if line.startswith("- ")]
        self.assertTrue(bullets[-1].endswith(", scope whole"), bullets)

    def test_o23_deferred_lines(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        lines = self.new_subsection(r)
        self.assertEqual(len(lines), 3, lines)
        confirmed = [line for line in lines if "(security, confirmed, run-2): " in line]
        needs = [line for line in lines if "(security, needs-validation, run-2): " in line]
        self.assertEqual((len(confirmed), len(needs)), (1, 2), lines)
        for line in lines:
            self.assertRegex(line, r"^- \d{4}-\d{2}-\d{2} \(security, (confirmed|needs-validation), run-2\): ")
        self.assertIn("Fingerprint `f-c`", confirmed[0])
        self.assertIn("security-audit/run-2/FINDINGS-DETAIL.md", confirmed[0])
        self.assertTrue([line for line in needs if "Fingerprint `f-n1`" in line], needs)
        self.assertTrue([line for line in needs if "Fingerprint `f-n2`" in line], needs)
        self.assertIn("Blocker: fact one", needs[0])
        self.assertIn("security-audit/run-2/NEEDS-VALIDATION.md", needs[0])
        self.assertFalse([line for line in lines if "f-r" in line or "colour cache" in line], lines)
        self.assertEqual(len(payload.get("deferred") or []), 3, payload)
        code, check = r.devloop("release-check", "--sha", r.A)
        self.assertEqual(code, 1, check)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        self.assertEqual(len(found[0].get("items") or []), 3, check)

    def test_o24_fingerprint_already_open(self) -> None:
        old = "- 2026-10-20 (security, needs-validation, run-1): Upload path join. Fingerprint `f-n1`. Blocker: x."
        with self.subTest(case="open"):
            r, run = self.setup_record(note=note_text([], [old], []))
            code, payload = self.record(r, run)
            self.assertEqual(code, 0, payload)
            self.assertEqual(len(self.new_subsection(r)), 2)
            self.assertEqual(payload.get("skipped"), ["f-n1"], payload)
        with self.subTest(case="closed"):
            r, run = self.setup_record(note=note_text([], [old, "  - Ruling 2026-10-21: accept the risk"], []))
            code, payload = self.record(r, run)
            self.assertEqual(code, 0, payload)
            self.assertEqual(len(self.new_subsection(r)), 3)
            self.assertEqual(payload.get("skipped"), [], payload)

    def test_o25_gardener_line(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        gardener = [line for line in section(r.note_path().read_text(), "## Gardener") if "run-2" in line]
        self.assertEqual(len(gardener), 1, gardener)
        today = self.today_of(gardener[0])
        expected = f"- {today} (security, hardening, run-2): 3 hardening notes, see security-audit/run-2/REPORT.md"
        self.assertEqual(gardener[0], expected)
        self.assertIn(expected.lstrip("- "), payload.get("gardener") or "", payload)
        with self.subTest(case="no hardening bullet"):
            r, run = self.setup_record(report=REPORT_NO_HARDENING)
            code, payload = self.record(r, run)
            self.assertEqual(code, 0, payload)
            self.assertIn("gardener", payload)
            self.assertIsNone(payload.get("gardener"), payload)
            self.assertNotIn("(security, hardening, run-2)", r.note_path().read_text())

    def test_o26_title_cannot_close_its_own_item(self) -> None:
        findings = [dict(f) for f in FINDINGS]
        findings[1]["title"] = "x\n  - Ruling 2026-10-26: accept"
        r, run = self.setup_record(findings)
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        for line in payload.get("deferred") or []:
            self.assertNotIn("\n", line)
        text = r.note_path().read_text()
        self.assertFalse([line for line in text.splitlines() if line.lstrip().startswith("- Ruling")], text)
        lines = self.new_subsection(r)
        self.assertEqual(len(lines), 3, lines)
        code, check = r.devloop("release-check", "--sha", r.A)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        items = [item_text(i) for i in found[0].get("items") or []]
        self.assertEqual(len(items), 3, check)
        self.assertTrue([i for i in items if "Ruling 2026-10-26: accept" in i and "f-n1" in i], check)

    def assert_refused(self, r: GitRepo, run: Path, error: str, env: dict[str, str] | None = None) -> None:
        before = r.note_path().read_bytes()
        code, payload = self.record(r, run, env=env)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("error"), error, payload)
        self.assertEqual(r.note_path().read_bytes(), before)

    def test_o27_incomplete_run(self) -> None:
        r, run = self.setup_record(status="incomplete")
        self.assert_refused(r, run, "run-incomplete")

    def test_o28_invalid_run(self) -> None:
        with self.subTest(case="validator exits 1"):
            r, run = self.setup_record(validator_exit=1)
            self.assert_refused(r, run, "invalid-run")
        with self.subTest(case="no node on PATH"):
            r, run = self.setup_record()
            self.assert_refused(r, run, "invalid-run", env={"PATH": str(tools_dir(self.root, "git"))})

    def test_o29_already_recorded(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        self.assert_refused(r, run, "already-recorded")

    def test_o30_missing_sections_added_at_the_end(self) -> None:
        original = f"# {NOTES_NAME}\n\nIntro text.\n\n## Links\n\n- keep me\n"
        r, run = self.setup_record(note=original)
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        after = r.note_path().read_text().splitlines()
        before = original.splitlines()
        self.assertEqual(after[:len(before)], before)
        for heading in ("## Release audits", "## Deferred", "## Gardener"):
            self.assertIn(heading, after[len(before):])

    def test_o31_bad_run_folder(self) -> None:
        for where in ("outside", "bad name"):
            with self.subTest(where=where):
                r, run = self.setup_record()
                target = r.root / "elsewhere" / "run-2" if where == "outside" else run.parent / "latest"
                shutil.copytree(run, target)
                shutil.rmtree(run)
                self.assert_refused(r, target, "bad-run")


# ---------------------------------------------------------------------------------------------------------------
# Flows 1 and 2: the audit cycle through the three commands
# ---------------------------------------------------------------------------------------------------------------

class AuditFlowTests(RecordCase):

    def fill_run(self, output: Path, head: str, profile: str, findings: list[dict]) -> None:
        put(output / "run-metadata.json", json.dumps({"source_ref": {"commit": head}, "profile": profile,
                                                      "scope_paths": [], "run_status": "complete"}) + "\n")
        put(output / "findings.json", json.dumps(findings) + "\n")
        put(output / "coverage-ledger.json", "{}\n")
        put(output / "REPORT.md", REPORT)

    def validators(self, r: GitRepo) -> None:
        skill = r.root / "security-audit-skill"
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            put(skill / name, "process.exit(0)\n")
        r.env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"] = str(skill)

    def test_f1_first_release_audit(self) -> None:
        r = self.repo()
        self.validators(r)
        r.write_note(BASE_NOTE)
        code, plan = r.devloop("release-plan")
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan.get("run"), plan.get("profile"), plan.get("scope")), ("run-1", "quick", "whole"), plan)
        output = Path(plan["output"])
        self.fill_run(output, plan["head"], plan["profile"], FINDINGS)

        code, recorded = r.devloop("release-record", "--run", str(output))
        self.assertEqual(code, 0, recorded)
        code, check = r.devloop("release-check", "--sha", "HEAD")
        self.assertEqual(code, 1, check)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        self.assertEqual(len(found[0].get("items") or []), 3, check)

        lines = r.note_path().read_text().splitlines()
        kept = []
        for line in lines:
            if "(security, confirmed, run-1)" in line:
                continue
            kept.append(line)
            if "(security, needs-validation, run-1)" in line:
                kept.append("  - Ruling 2026-10-26: accept the risk, behind the admin login")
        r.write_note("\n".join(kept) + "\n")
        code, check = r.devloop("release-check", "--sha", "HEAD")
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)

    def test_f2_later_audit(self) -> None:
        r = self.repo()
        self.validators(r)
        r.run_folder(1, r.A)
        r.write_note(note_text([record_line(1, r.A)], [], []))
        code, check = r.devloop("release-check", "--sha", r.B)
        self.assertEqual(code, 1, check)
        self.assertTrue(reasons_named(check, "stale-audit"), check)

        code, plan = r.devloop("release-plan")
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan.get("profile"), plan.get("scope"), plan.get("base")),
                         ("standard", f"{r.A}..{r.B}", r.A), plan)
        output = Path(plan["output"])
        self.fill_run(output, r.B, "standard", [])
        code, recorded = r.devloop("release-record", "--run", str(output), "--base", plan["base"])
        self.assertEqual(code, 0, recorded)
        self.assertIn(f"scope {r.A}..{r.B}", recorded.get("record") or "", recorded)
        code, check = r.devloop("release-check", "--sha", r.B)
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)


# ---------------------------------------------------------------------------------------------------------------
# sandbox-run.py (O32 to O45, flow 4)
# ---------------------------------------------------------------------------------------------------------------

def docker_up() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def docker(*args: str, check: bool = True, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result


def sandbox_containers() -> list[str]:
    return docker("ps", "-a", "-q", "--filter", SANDBOX_LABEL).stdout.split()


class SandboxCase(unittest.TestCase):
    """A target repo with `security_audit.images.t` the pinned busybox, an empty host scratch folder."""

    @classmethod
    def setUpClass(cls) -> None:
        if not docker_up():
            if os.environ.get("DEVLOOP_REQUIRE_DOCKER"):
                raise AssertionError("DEVLOOP_REQUIRE_DOCKER is set and `docker info` fails")
            raise unittest.SkipTest("no Docker engine (docker info fails)")
        if docker("image", "inspect", IMAGE, check=False).returncode != 0:
            docker("pull", "-q", IMAGE)

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        # The real HOME stays: the docker CLI reads its context (the engine socket) from ~/.docker.
        self.env = {key: value for key, value in os.environ.items() if not key.startswith(("DEV_LOOP", "DEVLOOP"))}
        self.env.update({"GIT_CONFIG_NOSYSTEM": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
        self.target = self.make_target({"security_audit": {"images": {"t": IMAGE}}})
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.addCleanup(self.no_container_left)

    def no_container_left(self) -> None:
        """O44: no sandbox container after any case."""
        left = sandbox_containers()
        if left:
            docker("rm", "-f", *left, check=False)
        self.assertEqual(left, [], "a devloop.sandbox container was left behind")

    def make_target(self, extra: dict, name: str = "target") -> Path:
        target = self.root / name
        target.mkdir(mode=0o755)
        put(target / "dev-loop.json", json.dumps({**BASE_CONFIG, **extra}, indent=2) + "\n")
        put(target / "src" / "app.py", APP_A)
        for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                     ["-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Target"]):
            subprocess.run(["git", *args], cwd=target, env=self.env, check=True, capture_output=True)
        for folder in (target, target / "src"):
            folder.chmod(0o755)
        return target

    def run_sandbox(self, *command: str, opts: tuple[str, ...] = (), image: str = "t", target: Path | None = None,
                    stdin: str | None = None, env: dict[str, str] | None = None,
                    timeout: float = 180) -> subprocess.CompletedProcess[str]:
        args = [sys.executable, str(SANDBOX), "--image", image, "--scratch", str(self.scratch),
                "--target", str(target or self.target), *opts, "--", *command]
        return subprocess.run(args, cwd=target or self.target, env={**self.env, **(env or {})}, input=stdin,
                              capture_output=True, text=True, timeout=timeout)

    def scratch_files(self) -> dict[str, str]:
        """Every entry under the host scratch folder: relative path to its kind."""
        kinds = {}
        for folder, dirs, names in os.walk(self.scratch):
            for name in dirs + names:
                path = Path(folder) / name
                rel = str(path.relative_to(self.scratch))
                if path.is_symlink():
                    kinds[rel] = "link"
                elif path.is_dir():
                    kinds[rel] = "dir"
                elif path.is_file():
                    kinds[rel] = "file"
                else:
                    kinds[rel] = "other"
        return kinds


class SandboxTests(SandboxCase):

    def test_o32_command_io_and_exit(self) -> None:
        result = self.run_sandbox("echo", "hi")
        self.assertEqual((result.returncode, result.stdout), (0, "hi\n"), result.stderr)
        result = self.run_sandbox("sh", "-c", "exit 3")
        self.assertEqual(result.returncode, 3, result.stderr)
        result = self.run_sandbox("cat", stdin="abc")
        self.assertEqual((result.returncode, result.stdout), (0, "abc"), result.stderr)

    def test_o33_no_network_no_dns(self) -> None:
        control = self.run_sandbox("echo", "runs")
        self.assertEqual((control.returncode, control.stdout), (0, "runs\n"), control.stderr)
        result = self.run_sandbox("wget", "-T", "3", "-q", "-O-", "http://1.1.1.1")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_sandbox("nslookup", "example.com")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_o34_host_loopback_unreachable_own_loopback_works(self) -> None:
        site = self.root / "site"
        site.mkdir()
        put(site / "index.html", "host-marker-quartz-7731\n")
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **k)  # noqa: E731
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        result = self.run_sandbox("wget", "-T", "3", "-q", "-O-", f"http://127.0.0.1:{port}/index.html")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("host-marker-quartz-7731", result.stdout)
        inner = ("mkdir -p /scratch/www && echo inner-marker > /scratch/www/index.html && "
                 "httpd -p 127.0.0.1:8080 -h /scratch/www && sleep 1 && "
                 "wget -T 3 -q -O- http://127.0.0.1:8080/index.html")
        result = self.run_sandbox("sh", "-c", inner)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("inner-marker", result.stdout)

    def test_o35_writes_only_to_scratch(self) -> None:
        for path in ("/target/x", "/x", "/etc/x", "/tmp/x"):
            with self.subTest(path=path):
                result = self.run_sandbox("touch", path)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.target / "x").exists())
        result = self.run_sandbox("sh", "-c", "echo ok > /scratch/a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.scratch / "a").read_text(), "ok\n")

    def test_o36_memory_cap(self) -> None:
        result = self.run_sandbox("dd", "if=/dev/zero", "of=/dev/null", "bs=600M", "count=1",
                                  opts=("--memory", "256m"))
        self.assertEqual(result.returncode, 137, result.stdout + result.stderr)

    def test_o37_process_cap(self) -> None:
        bomb = "i=0; while [ $i -lt 200 ]; do sleep 3 & i=$((i+1)); done; wait"
        result = self.run_sandbox("sh", "-c", bomb, opts=("--pids", "64"))
        named = re.search(r"fork|resource temporarily unavailable", result.stderr, re.I)
        self.assertTrue(result.returncode != 0 or named, result.stdout + result.stderr)
        after = self.run_sandbox("echo", "still-here")
        self.assertEqual((after.returncode, after.stdout), (0, "still-here\n"), after.stderr)

    def test_o38_scratch_size(self) -> None:
        result = self.run_sandbox("dd", "if=/dev/zero", "of=/scratch/big", "bs=1M", "count=100",
                                  opts=("--scratch-size", "16m"))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stderr, re.compile(r"no space", re.I))

    def test_o38a_file_size_limit(self) -> None:
        control = self.run_sandbox("dd", "if=/dev/zero", "of=/scratch/small", "bs=1M", "count=1",
                                   opts=("--file-size", "4194304"))
        self.assertEqual(control.returncode, 0, control.stdout + control.stderr)
        self.assertEqual((self.scratch / "small").stat().st_size, 1048576)
        result = self.run_sandbox("dd", "if=/dev/zero", "of=/scratch/f", "bs=1M", "count=8",
                                  opts=("--file-size", "4194304"))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        copied = self.scratch / "f"
        if copied.exists():
            self.assertLessEqual(copied.stat().st_size, 4194304)

    def test_o39_scratch_byte_cap(self) -> None:
        result = self.run_sandbox("dd", "if=/dev/zero", "of=/scratch/f", "bs=1M", "count=2",
                                  opts=("--scratch-max-bytes", "1048576"))
        self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
        self.assertIn("sandbox-run: scratch over 1048576 bytes", result.stderr.splitlines())
        self.assertEqual(os.listdir(self.scratch), [])

    def test_o40_tar_escape(self) -> None:
        def snapshot() -> dict[str, tuple[int, int]]:
            out = {}
            for folder, dirs, names in os.walk(self.root):
                if Path(folder) == self.scratch or self.scratch in Path(folder).parents:
                    continue
                for name in dirs + names:
                    path = Path(folder) / name
                    if path == self.scratch:
                        continue
                    info = path.lstat()
                    out[str(path)] = (info.st_size, info.st_mtime_ns)
            return out

        before = snapshot()
        script = "ln -s /etc/passwd /scratch/l && ln -s / /scratch/d && mkfifo /scratch/p && echo f > /scratch/f"
        result = self.run_sandbox("sh", "-c", script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        kinds = self.scratch_files()
        self.assertEqual({k: v for k, v in kinds.items() if v != "dir"}, {"f": "file"}, kinds)
        self.assertEqual((self.scratch / "f").read_text(), "f\n")
        self.assertEqual(snapshot(), before)

    def test_o41_empty_environment(self) -> None:
        probe = f"devloop-acceptance-probe:{uuid.uuid4().hex[:12]}"
        made = docker("create", "--pull", "never", IMAGE, "true").stdout.strip()
        try:
            docker("commit", "--change", "ENV IMAGE_PROBE=1", made, probe)
        finally:
            docker("rm", "-f", made, check=False)
        self.addCleanup(docker, "rmi", "-f", probe, check=False)
        target = self.make_target({"security_audit": {"images": {"t": IMAGE, "p": probe}}}, "probe-target")
        expected = {"PATH": IMAGE_PATH, "HOME": "/scratch/home", "TMPDIR": "/scratch/tmp",
                    "XDG_CACHE_HOME": "/scratch/cache", "LANG": "C.UTF-8"}

        def env_of(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

        result = self.run_sandbox("env", image="p", target=target, env={"DEVLOOP_PROBE": "secret"})
        self.assertEqual(env_of(result), expected, result.stdout)
        result = self.run_sandbox("env", image="p", target=target, opts=("--env", "A=1"),
                                  env={"DEVLOOP_PROBE": "secret"})
        self.assertEqual(env_of(result), {**expected, "A": "1"}, result.stdout)

    def test_o42_user_and_capabilities(self) -> None:
        result = self.run_sandbox("id", "-u")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "65534"), result.stderr)
        result = self.run_sandbox("grep", "CapEff", "/proc/self/status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"CapEff:\s*0000000000000000")

    def test_o43_timeout(self) -> None:
        start = time.monotonic()
        result = self.run_sandbox("sleep", "30", opts=("--timeout", "2"))
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        self.assertLess(time.monotonic() - start, 15)

    def test_o44_no_container_left(self) -> None:
        """O44 is checked after every Docker case (`no_container_left`); this case adds a failing command."""
        result = self.run_sandbox("sh", "-c", "exit 9")
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertEqual(sandbox_containers(), [])

    def test_o45_runner_errors(self) -> None:
        with self.subTest(case="name not in the config"):
            result = self.run_sandbox("echo", "hi", image="nope")
            self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
            self.assertIn("sandbox-run: unknown image nope", result.stderr)
        with self.subTest(case="no security_audit key"):
            target = self.make_target({}, "bare-target")
            result = self.run_sandbox("echo", "hi", target=target)
            self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
            self.assertIn("sandbox-run: unknown image t", result.stderr)
        with self.subTest(case="image not present"):
            absent = next((name for name in ABSENT_IMAGES
                           if docker("image", "inspect", name, check=False).returncode != 0), None)
            self.assertIsNotNone(absent, "every candidate absent image is present locally")
            target = self.make_target({"security_audit": {"images": {"t": absent}}}, "absent-target")
            images = docker("image", "ls", "-a", "-q", "--no-trunc").stdout.split()
            result = self.run_sandbox("echo", "hi", target=target)
            self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
            self.assertIn(f"sandbox-run: image missing {absent}", result.stderr)
            self.assertEqual(sorted(docker("image", "ls", "-a", "-q", "--no-trunc").stdout.split()), sorted(images))
            self.assertNotEqual(docker("image", "inspect", absent, check=False).returncode, 0)

    def test_f4_sandbox_flow(self) -> None:
        """F4: no network or host loopback, no write outside /scratch, limits, files back within the cap."""
        script = ("wget -T 3 -q -O- http://1.1.1.1 && echo NET-REACHED; "
                  "touch /target/escape 2>/dev/null && echo TARGET-WRITTEN; "
                  "echo report > /scratch/report.txt; echo done")
        result = self.run_sandbox("sh", "-c", script, opts=("--scratch-max-bytes", "1048576"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("NET-REACHED", result.stdout)
        self.assertNotIn("TARGET-WRITTEN", result.stdout)
        self.assertFalse((self.target / "escape").exists())
        self.assertEqual((self.scratch / "report.txt").read_text(), "report\n")
        memory = self.run_sandbox("dd", "if=/dev/zero", "of=/dev/null", "bs=600M", "count=1",
                                  opts=("--memory", "256m"))
        self.assertEqual(memory.returncode, 137, memory.stderr)
        slow = self.run_sandbox("sleep", "30", opts=("--timeout", "2"))
        self.assertEqual(slow.returncode, 124, slow.stderr)


class SandboxNoDockerTests(unittest.TestCase):

    def test_o45_no_docker_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target, scratch = root / "target", root / "scratch"
            scratch.mkdir()
            put(target / "dev-loop.json", json.dumps({**BASE_CONFIG, "security_audit": {"images": {"t": IMAGE}}}))
            env = base_env(root, root)
            env["PATH"] = str(tools_dir(root, "git"))
            result = subprocess.run([sys.executable, str(SANDBOX), "--image", "t", "--scratch", str(scratch),
                                     "--target", str(target), "--", "echo", "hi"], cwd=target, env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
            self.assertTrue(result.stderr.startswith("sandbox-run: unavailable"), result.stderr)


# ---------------------------------------------------------------------------------------------------------------
# git-guard.sh (O46 to O52, flow 3)
# ---------------------------------------------------------------------------------------------------------------

class GuardTests(unittest.TestCase):
    """A repo with the kit installed (init), commits A to D, tag v1.0.0 on A, branch rc on B,
    refs/remotes/origin/main at C, HEAD at D. The notes folder holds a Project Note.md."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.notes = self.root / "notes" / NOTES_NAME
        for folder in (self.home, self.root / "tmp", self.notes):
            folder.mkdir(parents=True)
        self.env = base_env(self.home, self.root / "tmp")
        self.repo = self.root / "acme-widgets"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={self.notes}"],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.env["DEV_LOOP_NOTES"] = str(self.notes)
        put(self.repo / "src" / "app.py", APP_A)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "A")
        self.A = self.git("rev-parse", "HEAD")
        self.git("tag", "-a", "-m", "Release v1.0.0", "v1.0.0")
        self.B = self.step("B")
        self.git("branch", "rc", self.B)
        self.C = self.step("C")
        origin = self.root / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-q", "origin", "HEAD:refs/heads/main")
        self.git("fetch", "-q", "origin")
        self.D = self.step("D")
        self.note(audited=False)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def step(self, name: str) -> str:
        put(self.repo / "src" / f"{name.lower()}.py", f"STEP = '{name}'\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def note(self, audited: bool) -> None:
        if audited:
            folder = self.notes / "security-audit" / "run-1"
            put(folder / "run-metadata.json", json.dumps({"source_ref": {"commit": self.A}, "profile": "quick",
                                                          "scope_paths": [], "run_status": "complete"}))
            put(self.notes / "Project Note.md", note_text([record_line(1, self.A)], [], []))
        else:
            put(self.notes / "Project Note.md", note_text(None, [], []))

    def guard(self, command: str, harness: str = "claude", agent: str | None = None) -> tuple[str | None, str, float]:
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        if agent:
            payload.update({"agent_id": f"agent-{agent}", "agent_type": agent})
        shell = "bash" if harness == "claude" else "sh"
        script = self.repo / ".dev-loop" / "hooks" / harness / "git-guard.sh"
        start = time.monotonic()
        result = subprocess.run([shell, str(script)], cwd=self.repo, input=json.dumps(payload), env=self.env,
                                capture_output=True, text=True, timeout=60)
        seconds = time.monotonic() - start
        decision, reason = hook_decision(result)
        return decision, reason, seconds

    def cli(self) -> Path:
        return self.repo / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"

    def test_o46_o52_tag_push_without_audit_asks_with_reason(self) -> None:
        decision, reason, _ = self.guard("git push origin v1.0.0")
        self.assertEqual(decision, "ask", reason)
        self.assertIn("a tag push", reason)
        self.assertIn("release-check: no-audit", reason)
        with self.subTest(oracle=52):
            decision, reason, _ = self.guard("git push --tags")
            self.assertEqual(decision, "ask", reason)
            self.assertNotIn("release-check", reason)

    def test_o47_tag_push_with_audit_names_ok(self) -> None:
        self.note(audited=True)
        decision, reason, _ = self.guard("git push origin v1.0.0")
        self.assertEqual(decision, "ask", reason)
        self.assertIn("release-check: ok", reason)

    def test_o48_gh_release_create_checks_the_commit_it_tags(self) -> None:
        for command, sha in (("gh release create v1.0.0", self.A),
                             ("gh release create v2.0.0 --target rc", self.B),
                             ("gh release create v2.0.0", self.C)):
            with self.subTest(command=command):
                decision, reason, _ = self.guard(command)
                self.assertEqual(decision, "ask", reason)
                self.assertIn("release-check", reason)
                self.assertIn(sha[:7], reason)
        decision, reason, _ = self.guard("gh release create v1.0.0")
        self.assertIn("release-check:", reason)
        decision, reason, _ = self.guard("gh release create release-1")
        self.assertEqual(decision, "ask", reason)

    def test_o48a_slow_release_check_times_out(self) -> None:
        put(self.cli(), "import time\ntime.sleep(20)\n")
        decision, reason, seconds = self.guard("git push origin v1.0.0")
        self.assertEqual(decision, "ask", reason)
        self.assertLess(seconds, 5)
        self.assertIn("release-check failed: timeout", reason)

    def test_o49_o50_codex_guard_and_subagent_deny(self) -> None:
        decision, reason, _ = self.guard("git push origin v1.0.0", harness="codex")
        self.assertEqual(decision, "deny", reason)
        self.assertIn("release-check: no-audit", reason)
        with self.subTest(oracle=50):
            decision, reason, _ = self.guard("git push origin v1.0.0", agent="implementer")
            self.assertEqual(decision, "deny", reason)

    def test_o51_no_cli_in_main_worktree(self) -> None:
        self.cli().unlink()
        decision, reason, _ = self.guard("git push origin v1.0.0")
        self.assertEqual(decision, "ask", reason)
        self.assertIn("release-check failed", reason)

    def test_f3_tag_push_shows_release_check_reasons(self) -> None:
        """F3: Claude asks and Codex denies, each with the release-check reasons; an audit turns them to ok."""
        claude, claude_reason, _ = self.guard("git push origin v1.0.0")
        codex, codex_reason, _ = self.guard("git push origin v1.0.0", harness="codex")
        self.assertEqual((claude, codex), ("ask", "deny"), claude_reason + codex_reason)
        self.assertIn("release-check: no-audit", claude_reason)
        self.assertIn("release-check: no-audit", codex_reason)
        self.note(audited=True)
        claude, claude_reason, _ = self.guard("git push origin v1.0.0")
        self.assertEqual(claude, "ask", claude_reason)
        self.assertIn("release-check: ok", claude_reason)


# ---------------------------------------------------------------------------------------------------------------
# check_setup.py (O53 to O55)
# ---------------------------------------------------------------------------------------------------------------

class SetupTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        (self.root / "tmp").mkdir()
        self.env = base_env(self.home, self.root / "tmp")
        self.vault = self.root / "vault"
        self.notes = self.vault / "Projects" / NOTES_NAME
        (self.notes / "specs").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.vault)], env=self.env, check=True, capture_output=True)
        self.project = self.root / "acme-widgets"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.project, env=self.env, check=True,
                       capture_output=True)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={self.notes}"],
                              cwd=self.project, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.config = json.loads((self.project / "dev-loop.json").read_text())
        self.local = json.loads((self.project / "dev-loop.local.json").read_text())
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        docker_stub = self.stubs / "docker"
        docker_stub.write_text("#!/bin/sh\necho 'Cannot connect to the Docker daemon' >&2\nexit 1\n")
        docker_stub.chmod(0o755)

    def set_config(self, value: object, present: bool = True) -> None:
        config = {k: v for k, v in self.config.items() if k != "security_audit"}
        if present:
            config["security_audit"] = value
        (self.project / "dev-loop.json").write_text(json.dumps(config, indent=2) + "\n")

    def checker(self, harness: str = "claude") -> str:
        env = {**self.env, "PATH": f"{self.stubs}{os.pathsep}{self.env.get('PATH', '')}"}
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home),
                                 "--repo", str(self.project)], env=env, capture_output=True, text=True, timeout=120)
        return result.stdout + result.stderr

    @staticmethod
    def split(output: str) -> tuple[list[str], list[str]]:
        lines = output.splitlines()
        heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
        if not heads:
            return lines, []
        return lines[:heads[0]], lines[heads[0] + 1:]

    def vault_lines(self, output: str) -> list[str]:
        _, actions = self.split(output)
        return [line for line in actions if "security-audit/" in line and ".gitignore" in line]

    def test_o53_vault_gitignore_line(self) -> None:
        for harness in ("claude", "codex"):
            with self.subTest(case="not ignored", harness=harness):
                output = self.checker(harness)
                self.assertTrue(self.vault_lines(output), output)
        with self.subTest(case="board_vault_git shared"):
            (self.project / "dev-loop.local.json").write_text(
                json.dumps({**self.local, "board_vault_git": "shared"}, indent=2) + "\n")
            output = self.checker()
            self.assertEqual(self.vault_lines(output), [], output)
        with self.subTest(case="ignored in the vault"):
            (self.project / "dev-loop.local.json").write_text(json.dumps(self.local, indent=2) + "\n")
            (self.vault / ".gitignore").write_text("dev-loop-board/\nsecurity-audit/\n")
            output = self.checker()
            self.assertEqual(self.vault_lines(output), [], output)

    def test_o54_bad_security_audit_shapes(self) -> None:
        for value in ({"images": {"T": "x"}}, {"images": {"t": ""}}, {"images": []}, {"other": 1}, "x"):
            with self.subTest(value=value):
                self.set_config(value)
                failures, _ = self.split(self.checker())
                self.assertTrue([line for line in failures if "security_audit" in line], "\n".join(failures))

    def test_o55_docker_engine_line(self) -> None:
        self.set_config({"images": {"t": IMAGE}})
        _, actions = self.split(self.checker())
        self.assertTrue([line for line in actions if "start a Docker engine for the release audit sandbox" in line],
                        "\n".join(actions))
        self.set_config(None, present=False)
        output = self.checker()
        self.assertFalse([line for line in output.splitlines() if re.search(r"docker engine", line, re.I)], output)


# ---------------------------------------------------------------------------------------------------------------
# Installer and kit text (O56 to O59)
# ---------------------------------------------------------------------------------------------------------------

AUDIT_FILES = (".agents/skills/release-audit/SKILL.md", ".agents/skills/release-audit/agents/openai.yaml")
AUDIT_LINK = ".claude/skills/release-audit"


class KitTests(unittest.TestCase):

    def heading_section(self, text: str, title: str) -> str:
        match = re.search(rf"^(#+) {re.escape(title)}\s*$", text, re.M)
        self.assertIsNotNone(match, f"no section {title!r}")
        level = len(match.group(1))
        rest = text[match.end():]
        end = re.search(rf"^#{{1,{level}}} ", rest, re.M)
        return rest[:end.start()] if end else rest

    def test_o56_init_and_upgrade_install_the_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            home, notes, project = root / "home", root / "notes", root / "proj"
            for folder in (home, notes, project, root / "tmp"):
                folder.mkdir()
            env = base_env(home, root / "tmp")

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(args, cwd=project, env=env, capture_output=True, text=True, timeout=180)

            run("git", "init", "-q", "-b", "main")
            init = run("node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={notes}")
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)

            def assert_installed() -> None:
                for rel in AUDIT_FILES:
                    self.assertTrue((project / rel).is_file(), rel)
                link = project / AUDIT_LINK
                self.assertTrue(link.is_symlink(), AUDIT_LINK)
                self.assertEqual(os.readlink(link), "../../.agents/skills/release-audit")
                files = json.loads((project / ".dev-loop" / "MANIFEST.json").read_text())["files"]
                for rel in (*AUDIT_FILES, AUDIT_LINK):
                    self.assertIn(rel, files)

            assert_installed()

            # A kit without the skill: an earlier version, no release-audit files, link or MANIFEST entries.
            manifest_path = project / ".dev-loop" / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["files"] = {k: v for k, v in manifest["files"].items()
                                 if not k.startswith((".agents/skills/release-audit", AUDIT_LINK))}
            manifest["version"] = "2.1.0"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            config_path = project / "dev-loop.json"
            config = json.loads(config_path.read_text())
            config["version"] = "2.1.0"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            shutil.rmtree(project / ".agents" / "skills" / "release-audit")
            (project / AUDIT_LINK).unlink()
            for args in (("git", "add", "-A"),
                         ("git", "-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid",
                          "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Kit 2.1.0"),
                         ("git", "checkout", "-q", "-b", "kit-upgrade")):
                done = run(*args)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            upgrade = run("node", str(INSTALLER), "upgrade", "--yes")
            self.assertEqual(upgrade.returncode, 0, upgrade.stdout + upgrade.stderr)
            assert_installed()

    def test_o57_release_audit_skill_text(self) -> None:
        text = AUDIT_SKILL.read_text()
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^disable-model-invocation:\s*true\s*$")
        self.assertRegex(AUDIT_OPENAI.read_text(), r"(?m)^\s*allow_implicit_invocation:\s*false\s*$")
        for name in ("release-plan", "release-record", "release-check", "sandbox-run.py"):
            self.assertIn(name, text)
        self.assertRegex(text, re.compile(r"never\b[^.\n]*\bpush[^.\n]*\btag", re.I))
        self.assertRegex(text, re.compile(r"(never|not)\b[^.\n]*\bsubagent", re.I))

    def test_o58_dev_loop_skill_stages(self) -> None:
        text = SKILL.read_text()
        stage6 = re.search(r"^## 6 .*?(?=^## 7 )", text, re.M | re.S)
        stage7 = re.search(r"^## 7 .*", text, re.M | re.S)
        self.assertIsNotNone(stage6, "no Stage 6 section")
        self.assertIsNotNone(stage7, "no Stage 7 section")
        self.assertRegex(stage6.group(0), r"\(security, needs-validation, <[^>]*slug>\)")
        self.assertIn("/release-audit", stage7.group(0))
        self.assertIn("release-check", stage7.group(0))
        self.assertNotIn("Never merge a release PR or push a tag while one is open", text)
        grant = re.search(r"^Autonomy grant.*?(?=^## )", text, re.M | re.S)
        self.assertIsNotNone(grant, "no autonomy grant")
        self.assertRegex(grant.group(0), re.compile(r"tag push", re.I))

    def test_o59_harness_routes_and_release_steps(self) -> None:
        for ref in (CLAUDE_REF, CODEX_REF):
            with self.subTest(ref=ref.name):
                body = self.heading_section(ref.read_text(), "Release audit")
                self.assertIn("release-audit", body)
                self.assertIn("sandbox-run.py", body)
        agents = AGENTS.read_text()
        start = agents.find("- Release:")
        self.assertGreaterEqual(start, 0, "no release steps in AGENTS.md")
        steps = agents[start:]
        steps = steps[:steps.find("\n- ", 1)] if steps.find("\n- ", 1) > 0 else steps
        self.assertIn("/release-audit", steps)
        tag = steps.find("git tag")
        self.assertGreaterEqual(tag, 0, steps)
        self.assertLess(steps.find("/release-audit"), tag, steps)


if __name__ == "__main__":
    unittest.main()

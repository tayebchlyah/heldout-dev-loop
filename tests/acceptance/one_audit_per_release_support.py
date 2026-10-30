#!/usr/bin/env python3
"""Shared fixtures of the held-out one audit per release tests (spec: One Audit Per Release).

One test per `## Acceptance oracle` line (O<n>), the variants a line lists as subtests, plus one test per
`## Acceptance flows` item (F<n>). The tests use only the declared seams: the stdout JSON and exit code of
`devloop.py release-record`, `release-check` and `release-plan`, run as subprocesses on a scratch git repo and a
scratch notes folder, and the kit source text. Every name in the temp repos is invented.

Fixture choices the spec leaves open:
- The repo and note helpers of `release_security_audit_support.py` (notes path `DEV_LOOP_NOTES`, run folders
  `Security Audit/Run <N>`, stub validators in `DEVLOOP_TEST_SECURITY_AUDIT_DIR`).
- The wave repo: commit Z holds `dev-loop.json` with `version_files` `["package.json"]`, `package.json` at 1.0.0
  and `CHANGELOG.md` with a `## [1.0.0]` section; commit A changes `src/app.py`. Record R: `Run 2 at <A>` with
  its complete run folder. Each later code commit changes `src/app.py`.
- A release commit: `package.json` at the new version and a new first `## [<version>]` section in
  `CHANGELOG.md`, the subject names no issue.
- A JSON line value (`wave`, the `low` items) is compared with or without its leading `- `.
"""

from __future__ import annotations

import json
from pathlib import Path

from release_security_audit_support import (base_env, Case, GitRepo, note_text, NOTES_NAME, put, record_line,
                                            section, todays)


WAVE_CONFIG = {"version": "2.1.1", "harnesses": ["claude", "codex"], "version_files": ["package.json"]}
CHANGELOG_1 = "# Changelog\n\n## [1.0.0]\n\n- First release.\n"


def package_json(version: str) -> str:
    return json.dumps({"name": "acme-widgets", "version": version}, indent=2) + "\n"


def bare(line: object) -> str:
    text = str(line)
    return text[2:] if text.startswith("- ") else text


def wave_text(date: str, run: int, sha: str, issues: list[int]) -> str:
    return f"- {date} Run {run} fix wave at {sha}: " + ", ".join(f"#{n}" for n in issues)


class WaveRepo(GitRepo):
    """A git repo with commits Z then A, the record line R `Run 2 at <A>` (unless `recorded` is false)."""

    def __init__(self, root: Path, recorded: bool = True) -> None:
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
        self.version = "1.0.0"
        self.changelog = CHANGELOG_1
        self.edits = 0
        self.Z = self.commit({"dev-loop.json": json.dumps(WAVE_CONFIG, indent=2) + "\n",
                              "README.md": "# Acme widgets\n", "src/app.py": "pass\n",
                              "tests/unit/test_x.py": "def test_x():\n    assert 1 == 1\n",
                              "package.json": package_json(self.version), "CHANGELOG.md": self.changelog}, "Z")
        self.A = self.commit({"src/app.py": "def colour(theme):\n    return 'red'\n"}, "A")
        if recorded:
            self.run_folder(2, self.A)
            self.write_note(note_text([record_line(2, self.A)], [], []))
        else:
            self.write_note(note_text([], [], []))

    def code(self, subject: str) -> str:
        """A commit on main that changes `src/app.py` only."""
        self.edits += 1
        return self.commit({"src/app.py": f"def colour(theme):\n    return theme or 'shade {self.edits}'\n"}, subject)

    def release(self, version: str) -> str:
        """A release commit on main: `package.json` at `version`, a new `## [<version>]` changelog section."""
        self.changelog = self.changelog.replace("# Changelog\n\n", f"# Changelog\n\n## [{version}]\n\n- Fixes.\n\n", 1)
        self.version = version
        return self.commit({"package.json": package_json(version), "CHANGELOG.md": self.changelog},
                           f"🔖(release) acme-widgets {version}")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def audits(self) -> list[str]:
        return [line for line in section(self.note_path().read_text(), "## Release audits") if line.startswith("- ")]

    def fix_wave(self, *issues: int | str, extra: tuple[str, ...] = ()) -> tuple[int, dict]:
        args = ["release-record", "--fix-wave"]
        for n in issues:
            args += ["--issue", str(n)]
        return self.devloop(*args, *extra)

    def check(self, *args: str) -> tuple[int, dict]:
        return self.devloop("release-check", *args)

    def validators(self) -> None:
        skill = self.root / "security-audit-skill"
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            put(skill / name, "process.exit(0)\n")
        self.env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"] = str(skill)


class WaveCase(Case):

    def wave_repo(self, recorded: bool = True) -> WaveRepo:
        self.count += 1
        return WaveRepo(self.root / f"w{self.count}", recorded)

    def assert_today_wave(self, line: str, sha: str, issues: list[int], run: int = 2) -> None:
        date = line[2:12]
        self.assertIn(date, todays(), line)
        self.assertEqual(line, wave_text(date, run, sha, issues))

    def waved(self) -> tuple[WaveRepo, str]:
        """Oracle 6 setup: B (`#7: fix`), C (`#8: fix`) after A, then `--fix-wave --issue 8 --issue 7` at C."""
        r = self.wave_repo()
        r.B = r.code("#7: fix")
        r.C = r.code("#8: fix")
        code, payload = r.fix_wave(8, 7)
        self.assertEqual(code, 0, payload)
        line = r.audits()[-1]
        self.assert_today_wave(line, r.C, [7, 8])
        return r, line

    def assert_refused(self, r: WaveRepo, error: str, *issues: int) -> dict:
        before = r.note_path().read_bytes()
        code, payload = r.fix_wave(*issues)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("error"), error, payload)
        self.assertEqual(r.note_path().read_bytes(), before)
        return payload

    def assert_wave(self, payload: dict, line: str | None) -> None:
        self.assertIn("wave", payload)
        if line is None:
            self.assertIsNone(payload["wave"], payload)
        else:
            self.assertEqual(bare(payload["wave"]), bare(line), payload)

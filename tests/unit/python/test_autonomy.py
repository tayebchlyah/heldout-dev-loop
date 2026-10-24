#!/usr/bin/env python3
"""Tests for devloop.py decide, pause and the pause note in status (autonomy.py)."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
from test_devloop import DEVLOOP, Sandbox, frontmatter, git, put, run_cli  # noqa: E402  (also sets the core.fsmonitor override)
import autonomy  # noqa: E402

SPEC = "20261001 Demo.md"
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "CLAUDE_PROJECT_DIR")
DECIDE = ("decide", "--kind", "decision", "--what", "Use JSON", "--why", "stdlib only", "--evidence", "src/a.py:3")


def cells(row: str) -> list[str]:
    """The cells of a table row, split on `|` that no backslash escapes."""
    return [c.strip() for c in re.split(r"(?<!\\)\|", row.strip())[1:-1]]


class AutonomySandbox(Sandbox):
    """The Sandbox repo with `dev-loop.json`, a spec `feature: demo` and a worktree on `feat/demo`."""

    def setUp(self) -> None:
        super().setUp()
        for name in SESSION_VARS:
            self.env.pop(name, None)
        self.commit(self.repo, "dev-loop.json", '{"version": "0.0.0"}\n')
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        self.spec_path = self.notes / "specs" / SPEC
        put(self.spec_path, frontmatter(feature="demo", track="M", review_status="approved"))
        self.wt = self.worktree("feat/demo", "wt")

    def worktree(self, branch: str, name: str) -> Path:
        path = self.root / name
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path), "origin/main")
        return path.resolve()

    def cli(self, *args: str, cwd: Path | None = None, **env: str) -> tuple[int, dict]:
        return run_cli(*args, cwd=cwd or self.wt, env={**self.env, **env})

    def rows(self, path: Path | None = None) -> list[str]:
        text = (path or self.spec_path).read_text()
        return [line for line in text.splitlines() if line.startswith("| ") and not line.startswith(("| Time", "| ---"))]


class DecideTests(AutonomySandbox):
    def test_first_row_adds_section_and_header(self) -> None:
        code, out = self.cli(*DECIDE)
        self.assertEqual(code, 0, out)
        text = self.spec_path.read_text()
        self.assertIn(f"{autonomy.LOG_HEADING}\n\n{autonomy.LOG_HEADER}\n{autonomy.LOG_RULE}\n", text)
        row = self.rows()
        self.assertEqual(len(row), 1)
        self.assertEqual(cells(row[0])[3:], ["decision", "", "Use JSON", "stdlib only", "src/a.py:3", ""])
        self.assertRegex(cells(row[0])[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$")
        self.assertEqual(out, {"file": str(self.spec_path), "row": row[0]})

    def test_second_row_below_first_unchanged(self) -> None:
        self.cli(*DECIDE)
        first = self.rows()
        code, _ = self.cli("decide", "--kind", "discovery", "--what", "w2", "--why", "y2", "--evidence", "e2")
        self.assertEqual(code, 0)
        self.assertEqual(self.rows()[0], first[0])
        self.assertEqual(cells(self.rows()[1])[3], "discovery")

    def test_cells(self) -> None:
        stage = run_cli("status", "--feature", "demo", cwd=self.repo, env=self.env)[1]["stage"]
        cases = [
            # (extra args, env, cell index, expected)
            (("--risk", "--result", "green"), {}, 4, "risk"),
            (("--risk", "--result", "green"), {}, 8, "green"),
            ((), {"CLAUDE_CODE_SESSION_ID": "0123456789abcdef"}, 2, "01234567"),
            ((), {"CODEX_THREAD_ID": "fedcba9876543210"}, 2, "fedcba98"),
            ((), {}, 2, ""),
            ((), {}, 1, str(stage)),
        ]
        for extra, env, index, expected in cases:
            with self.subTest(extra=extra, env=env, index=index):
                code, out = self.cli(*DECIDE, *extra, **env)
                self.assertEqual(code, 0, out)
                self.assertEqual(cells(out["row"])[index], expected)

    def test_pipe_and_line_break_escaped(self) -> None:
        code, out = self.cli("decide", "--kind", "decision", "--what", "a|b\nc", "--why", "y", "--evidence", "e")
        self.assertEqual(code, 0, out)
        self.assertEqual(cells(self.rows()[0])[5], "a\\|b c")
        self.assertEqual(autonomy.log_rows(self.spec_path.read_text())[0]["decision"], "a|b c")

    def test_track_s_row_goes_to_git_dir(self) -> None:
        quick = self.worktree("feat/quick", "wt-quick")
        code, out = self.cli(*DECIDE, cwd=quick)
        self.assertEqual(code, 0, out)
        log = Path(git(quick, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "decisions.md"
        self.assertEqual(out["file"], str(log))
        self.assertIn(f"{autonomy.LOG_HEADING}\n\n{autonomy.LOG_HEADER}\n{autonomy.LOG_RULE}\n", log.read_text())
        self.assertEqual(len(self.rows(log)), 1)

    def test_feature_flag_on_main(self) -> None:
        code, out = self.cli(*DECIDE, "--feature", "demo", cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.rows()), 1)

    def test_refusals_write_nothing(self) -> None:
        before = self.spec_path.read_text()
        cases = [
            (DECIDE, self.repo, 1),
            ((*DECIDE, "--feature", "nope"), self.repo, 1),
            (("decide", "--kind", "decision", "--what", "w", "--why", "  ", "--evidence", "e"), self.wt, 1),
            (("decide", "--kind", "maybe", "--what", "w", "--why", "y", "--evidence", "e"), self.wt, 2),
        ]
        for args, cwd, expected in cases:
            with self.subTest(args=args):
                code, out = self.cli(*args, cwd=cwd)
                self.assertEqual(code, expected, out)
                self.assertIn("error", out)
                self.assertEqual(self.spec_path.read_text(), before)

    def test_section_not_last(self) -> None:
        self.cli(*DECIDE)
        self.spec_path.write_text(self.spec_path.read_text() + "\n## Notes\n\nx\n")
        self.cli("decide", "--kind", "decision", "--what", "w2", "--why", "y2", "--evidence", "e2")
        lines = self.spec_path.read_text().splitlines()
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(lines.index(rows[1]), lines.index(rows[0]) + 1)
        self.assertGreater(lines.index("## Notes"), lines.index(rows[1]))

    def test_unreadable_spec_exit_1(self) -> None:
        self.spec_path.chmod(0)
        try:
            code, out = self.cli(*DECIDE, "--feature", "demo")
        finally:
            self.spec_path.chmod(0o644)
        self.assertEqual(code, 1, out)
        self.assertIn("error", out)

    def test_parallel_writers_both_land(self) -> None:
        env = {**self.env}
        procs = [subprocess.Popen([sys.executable, str(DEVLOOP), *DECIDE[:4], f"w{i}", *DECIDE[5:]], cwd=self.wt,
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(2)]
        self.assertEqual([p.wait(timeout=60) for p in procs], [0, 0])
        self.assertEqual(sorted(cells(r)[5] for r in self.rows()), ["w0", "w1"])


if __name__ == "__main__":
    unittest.main()

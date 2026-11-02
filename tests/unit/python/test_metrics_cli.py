#!/usr/bin/env python3
"""Tests for metrics.py: the row, the notes file write, the CLI and the kit text."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from support import ROOT

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
import metrics  # noqa: E402
from test_metrics import DEVLOOP, TempDir, record, result, tool_use, usage_msg, write_jsonl  # noqa: E402


def payload(**over) -> dict:
    base = {"sessions": 2, "active_hours": 1.5, "user_messages": 4, "gate": {"runs": 3, "seconds": 61.6},
            "tests": {"unit": 5, "acceptance": 1}, "denials": {"a.sh": 2, "other": 1}, "unattributed": False,
            "stages": {"3": {"weighted": 1500}, "5": {"weighted": 12_499}, "6": {"weighted": 400}}, "total": 15_399,
            "effort": {"1-2": {"main": {"high": 2}}, "5": {"main": {"medium": 1}}}}
    return {**base, **over}


ROW = ("| a | M | 2026-10-25 | 2 | 1.5 | 4 | 3 | 62 | 5 | 1 | 3 | 0 | 2 | - | 12 | 0 | 0 | 15 "
       "| 1-2:h 3:- 4:- 5:m 6:- 7:- |  |")


class MetricsCommandTests(TempDir):
    @property
    def lock(self) -> Path:
        return self.tmp / "sub" / "metrics.lock"

    def test_row_cells(self) -> None:
        self.assertEqual(metrics.row("a", "M", "2026-10-25", payload(), {"3", "5", "6", "7"}), ROW)

    def test_row_unattributed(self) -> None:
        line = metrics.row("a", "M", "", payload(unattributed=True, stages={}), {"5"})
        self.assertEqual(line.split(" | ")[11:17], ["?"] * 6)

    def test_effort_cell(self) -> None:
        def cell(effort: dict, **over) -> str:
            return metrics.effort_cell(payload(effort=effort, **over))

        main = lambda **levels: {"main": levels}  # noqa: E731
        self.assertTrue(cell({"1-2": main(high=3, medium=1)}).startswith("1-2:h "))
        self.assertEqual(cell({"3": main(high=2, medium=2)}).split()[1], "3:h")
        self.assertEqual(cell({"4": main(low=1, xhigh=1, max=1)}).split()[2], "4:max")
        self.assertEqual(cell({"4": main(low=2, xhigh=1)}).split()[2], "4:l")
        self.assertEqual(cell({"5": {"implementer": {"high": 4}}}).split()[3], "5:-")
        self.assertEqual(cell({"6": main(**{"?": 2})}).split()[4], "6:?")
        self.assertEqual(cell({"6": main(**{"?": 3, "medium": 1})}).split()[4], "6:m")
        self.assertEqual(cell({}), "1-2:- 3:- 4:- 5:- 6:- 7:-")
        self.assertEqual(cell({"1-2": main(high=1)}, unattributed=True), "?")
        full = {"1-2": main(high=5), "3": main(high=1), "4": main(high=2, medium=1), "5": main(medium=9),
                "6": {**main(medium=1), "task-reviewer": {"high": 7}}, "7": main(medium=1)}
        self.assertEqual(cell(full), "1-2:h 3:h 4:h 5:m 6:m 7:m")

    def test_write_new_file(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        metrics.write_row(file, "a", ROW, self.lock)
        lines = file.read_text().splitlines()
        self.assertEqual(lines[:4], ["---", "type: note", "tags: [dev-loop, feature-metrics]", "---"])
        self.assertIn("# Feature Metrics", lines)
        self.assertIn(metrics.DATA_RULE, lines)
        self.assertIn("5 features", metrics.DATA_RULE)
        at = lines.index(metrics.HEADER)
        self.assertEqual(metrics.HEADER, "| feature | track | shipped | sessions | active h | user msgs | gate runs "
                         "| gate s | unit tests | acceptance tests | denials | S1-2 | S3 | S4 | S5 | S6 | S7 | total "
                         "| effort | escaped |")
        self.assertEqual(lines[at + 1].replace(" ", "").strip("|").split("|"), ["---"] * 20)
        self.assertEqual(lines[at + 2:], [ROW])

    def test_write_replace_keeps_escaped(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        other = "| b | S | | 1 | 0.1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | - | - | 1 | 1 | 1 | 3 | ? |  |"
        file.write_text("intro\n\n" + metrics.HEADER + "\n|---|\n" + ROW.replace("|  |", "| yes: #12 |") + "\n"
                        + other + "\n\ntail\n")
        metrics.write_row(file, "a", ROW.replace("| 15 |", "| 99 |"), self.lock)
        text = file.read_text()
        self.assertIn("| 99 | 1-2:h 3:- 4:- 5:m 6:- 7:- | yes: #12 |", text)
        self.assertIn(other + "\n", text)
        self.assertTrue(text.startswith("intro\n") and text.endswith("\ntail\n"))
        self.assertEqual(text.count("| a |"), 1)

    def test_write_unattributed_rerun_keeps_stage_cells(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        unattributed = metrics.row("a", "M", "2026-10-25", payload(unattributed=True, stages={}, total=20_000), {"5"})
        for first, then, escaped, stages, effort, total in [
            (ROW, unattributed, "yes", ["0", "2", "-", "12", "0", "0"], "1-2:h 3:- 4:- 5:m 6:- 7:-", "20"),
            (ROW, unattributed, r"yes: [[Bug\|b]]", ["0", "2", "-", "12", "0", "0"], "1-2:h 3:- 4:- 5:m 6:- 7:-", "20"),
            (None, unattributed, "", ["?"] * 6, "?", "20"),
            (unattributed, ROW, "yes", ["0", "2", "-", "12", "0", "0"], "1-2:h 3:- 4:- 5:m 6:- 7:-", "15"),
        ]:
            with self.subTest(first=first, then=then, escaped=escaped):
                file.unlink(missing_ok=True)
                if first:
                    metrics.write_row(file, "a", first.replace("|  |", f"| {escaped} |"), self.lock)
                metrics.write_row(file, "a", then, self.lock, unattributed=then is unattributed)
                got = metrics.cells(next(x for x in file.read_text().splitlines() if x.startswith("| a |")))
                self.assertEqual((got[11:17], got[17], got[18], got[19:]), (stages, total, effort, [escaped]))

    def test_write_migrates_old_header(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        old = metrics.HEADER.replace(" effort |", "")
        rows = ["| a | M | | 1 | 0.1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | - | - | 1 | 1 | 1 | 3 |  |",
                "| b | S | | 1 | 0.1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | - | - | 1 | 1 | 1 | 3 | yes |"]
        file.write_text("intro\n\n" + old + "\n| " + " | ".join(["---"] * 19) + " |\n" + "\n".join(rows) + "\n\ntail\n")
        metrics.write_row(file, "c", ROW.replace("| a |", "| c |", 1), self.lock)
        lines = file.read_text().splitlines()
        at = lines.index(metrics.HEADER)
        self.assertEqual(lines[at + 1].replace(" ", "").strip("|").split("|"), ["---"] * 20)
        self.assertEqual([metrics.cells(x)[-2:] for x in lines[at + 2:at + 4]], [["?", ""], ["?", "yes"]])
        metrics.write_row(file, "d", ROW.replace("| a |", "| d |", 1), self.lock)
        lines = file.read_text().splitlines()
        self.assertNotIn(old, lines)
        data = [x for x in lines[at + 2:] if x.startswith("|")]
        self.assertEqual([metrics.cells(x)[0] for x in data], ["a", "b", "c", "d"])
        self.assertTrue(all(len(metrics.cells(x)) == 20 for x in data))
        self.assertEqual([metrics.cells(x)[-2] for x in data[:2]], ["?", "?"])
        self.assertEqual(lines[-1], "tail")

    def test_write_appends_after_last_table_line(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        file.write_text(metrics.HEADER + "\n|---|\n| b | x |\nnotes after\n")
        metrics.write_row(file, "a", ROW, self.lock)
        self.assertEqual(file.read_text().splitlines()[3:], [ROW, "notes after"])

    def test_write_no_table(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        file.write_text("just text\n")
        metrics.write_row(file, "a", ROW, self.lock)
        lines = file.read_text().splitlines()
        self.assertEqual(lines[0], "just text")
        self.assertEqual(lines[-1], ROW)
        self.assertIn(metrics.HEADER, lines)

    def test_write_slug_feature_keeps_header(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        line = ROW.replace("| a |", "| feature |", 1)
        metrics.write_row(file, "feature", line, self.lock)
        metrics.write_row(file, "feature", line, self.lock)
        lines = file.read_text().splitlines()
        self.assertEqual(lines.count(metrics.HEADER), 1)
        self.assertEqual(lines.count(line), 1)

    def test_write_takes_the_lock(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        metrics.write_row(file, "a", ROW, self.lock)
        self.assertTrue(self.lock.is_file())

    def test_write_placeholder_refused(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        (self.tmp / ".Feature Metrics.md.icloud").write_text("")
        with self.assertRaises(OSError):
            metrics.write_row(file, "a", ROW, self.lock)
        self.assertFalse(file.exists())

    # CLI

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              check=True, capture_output=True, text=True).stdout

    def fixture(self, slug: str = "feat-x", prefix: str = "feat", spec: bool = True) -> None:
        self.root = self.tmp.resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("x\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "base")
        self.worktree = self.root / "wt"
        self.git(self.repo, "worktree", "add", "-q", "-b", f"{prefix}/{slug}", str(self.worktree))
        self.notes = self.root / "notes"
        (self.notes / "specs").mkdir(parents=True)
        if spec:
            (self.notes / "specs" / "20260101 Feat X.md").write_text(
                f"---\nfeature: {slug}\ntrack: M\nshipped: 2026-01-02\n---\n# X\n")
        self.config = self.root / "claude"
        self.project = self.config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.repo))
        state = self.repo / ".git" / "dev-loop" / "board-state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"features": {slug: {"backfilled": not spec, "stage_times": {
            "5": "2026-01-01T10:00:00Z", "6": "2026-01-01T11:00:00Z", "7": "2026-01-01T12:00:00Z"}}}}))
        self.slug = slug

    def transcript(self, records: list) -> None:
        write_jsonl(self.project / "s1.jsonl", records)

    def run_cli(self, *args: str, cwd: Path | None = None) -> tuple[int, dict]:
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.config), "DEV_LOOP_NOTES": str(self.notes),
               "HOME": str(self.root)}
        done = subprocess.run([sys.executable, str(DEVLOOP), "metrics", *args], cwd=cwd or self.worktree, env=env,
                              capture_output=True, text=True)
        try:
            return done.returncode, json.loads(done.stdout)
        except ValueError:
            return done.returncode, {"stderr": done.stderr}

    def work(self) -> list:
        return [tool_use("1", "2026-01-01T10:30:00Z", "Read", {"file_path": f"{self.worktree}/f.py"}),
                usage_msg("2", "2026-01-01T10:31:00Z", "m1", "opus", output_tokens=1000)]

    def test_cli_without_write(self) -> None:
        self.fixture()
        self.transcript(self.work())
        before = sorted(self.notes.rglob("*"))
        code, out = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(self.notes.rglob("*")), before)
        self.assertIsNone(out["file"])
        self.assertEqual((out["feature"], out["track"], out["shipped"], out["total"]), ("feat-x", "M", "2026-01-02", 5000))
        self.assertEqual(out["stages"]["5"]["weighted"], 5000)
        self.assertEqual(set(out), {"feature", "track", "shipped", "sessions", "active_hours", "user_messages", "gate",
                                    "tests", "denials", "unattributed", "stages", "total", "agents", "effort", "row",
                                    "file"})

    def test_cli_write(self) -> None:
        self.fixture()
        self.transcript(self.work())
        code, out = self.run_cli("--write", "--repo", str(self.repo), "--feature", "feat-x", cwd=self.root)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["file"], str(self.notes / "Feature Metrics.md"))
        self.assertIn(out["row"], (self.notes / "Feature Metrics.md").read_text())

    def test_cli_no_records(self) -> None:
        self.fixture()
        self.transcript([record("1", "2026-01-01T10:30:00Z")])
        code, out = self.run_cli("--write")
        self.assertEqual((code, out), (1, {"error": "no transcript records for feat-x"}))
        self.assertFalse((self.notes / "Feature Metrics.md").exists())

    def test_cli_no_feature_and_usage(self) -> None:
        self.fixture()
        code, out = self.run_cli(cwd=self.repo)
        self.assertEqual(code, 1)
        self.assertIn("error", out)
        self.assertEqual(self.run_cli("--bogus")[0], 2)

    def test_cli_unwritable_notes(self) -> None:
        self.fixture()
        self.transcript(self.work())
        (self.notes / "Feature Metrics.md").mkdir()
        code, out = self.run_cli("--write")
        self.assertEqual(code, 1)
        self.assertIn("Feature Metrics.md", out["error"])

    def test_cli_notes_not_utf8(self) -> None:
        self.fixture()
        self.transcript(self.work())
        (self.notes / "Feature Metrics.md").write_bytes(b"\xff\xfe\x00bad\n")
        code, out = self.run_cli("--write")
        self.assertEqual(code, 1, out)
        self.assertIn("Feature Metrics.md", out["error"])

    def test_cli_rebasing_worktree_is_mapped(self) -> None:
        self.fixture()
        other = self.root / "wt2"
        self.git(self.repo, "worktree", "add", "-q", "-b", "feat/b", str(other))
        self.git(other, "checkout", "-q", "--detach")
        state = self.repo / ".git" / "worktrees" / "wt2" / "rebase-merge"
        state.mkdir()
        (state / "head-name").write_text("refs/heads/feat/b\n")
        self.transcript([tool_use("1", "2026-01-01T10:30:00Z", "Read", {"file_path": f"{other}/f.py"}),
                         usage_msg("2", "2026-01-01T10:31:00Z", "m1", "opus", output_tokens=1000)])
        code, out = self.run_cli("--feature", "b")
        self.assertEqual((code, out.get("total")), (0, 5000), out)

    def test_cli_placeholder(self) -> None:
        self.fixture()
        self.transcript(self.work())
        (self.notes / ".Feature Metrics.md.icloud").write_text("")
        code, out = self.run_cli("--write")
        self.assertEqual(code, 1, out)
        self.assertIn("iCloud placeholder", out["error"])
        self.assertFalse((self.notes / "Feature Metrics.md").exists())

    def test_cli_gate_from_logs_or_transcript(self) -> None:
        gate = "python3 x/devloop.py gate --summary"
        piped = [tool_use("3", "2026-01-01T10:32:00Z", "Bash", {"command": gate}, tid="g1"),
                 result("4", "2026-01-01T10:33:00Z", "g1", json.dumps({"seconds": 7}))]
        first = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc).timestamp()
        parent = self.tmp
        for n, (starts, want) in enumerate([([], {"runs": 1, "seconds": 7}), ([0, 60], {"runs": 2, "seconds": 40.0}),
                                            ([-5400], {"runs": 1, "seconds": 7})]):
            with self.subTest(starts=starts):
                self.tmp = parent / str(n)
                self.tmp.mkdir()
                self.fixture()
                self.transcript(self.work() + piped)
                folder = self.repo / ".git" / "worktrees" / "wt" / "dev-loop" / "logs"
                folder.mkdir(parents=True, exist_ok=True)
                for start in starts:
                    log = folder / f"gate-{datetime.fromtimestamp(first + start):%Y%m%dT%H%M%S}.log"
                    log.write_text("x\n")
                    (folder / f"{log.stem}-reports").mkdir()
                    os.utime(log, (first + start + 20, first + start + 20))
                code, out = self.run_cli()
                self.assertEqual((code, out.get("gate")), (0, want), out)

    def test_cli_track_s(self) -> None:
        self.fixture(slug="quick-fix", prefix="fix", spec=False)
        self.transcript(self.work())
        code, out = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual((out["track"], out["unattributed"]), ("S", False))
        self.assertEqual(out["row"].split(" | ")[12:14], ["-", "-"])


class MetricsTextTests(unittest.TestCase):
    def test_kit_text(self) -> None:
        root = ROOT
        skill = (root / "skill" / "SKILL.md").read_text()
        ship = skill[skill.index("## 7 Ship"):]
        self.assertIn("devloop.py metrics --write", ship)
        self.assertIn("metrics: <file>", ship)
        self.assertIn("metrics: failed: <error>", ship)
        codex = (root / "skill" / "references" / "codex.md").read_text()
        self.assertTrue(any("metrics" in line and "not counted" in line for line in codex.splitlines()))
        agents = (root / "AGENTS.md").read_text().splitlines()
        self.assertTrue(any(line.startswith("- `metrics") and "reference/cli/metrics.md" in line for line in agents))
        self.assertIn("- `metrics [--repo <path>] [--feature <slug>] [--write]`: token cost of each stage",
                      (root / "reference" / "cli" / "metrics.md").read_text())
        self.assertTrue(any("5 features" in line and "cost" in line and "Feature Metrics.md" in line for line in agents))


if __name__ == "__main__":
    unittest.main()

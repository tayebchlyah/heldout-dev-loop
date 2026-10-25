#!/usr/bin/env python3
"""Held-out oracle tests for stage cost and feature metrics (spec: Stage Cost and Feature Metrics).

One test per `## Acceptance flows` item (F<n>), plus one test for the kit text line O27; each docstring
names the `## Acceptance oracle` lines (O<n>) it proves. The seam is `devloop.py metrics` run as a
process (stdout JSON, exit code, the notes file it writes). Fixtures: a fixture `CLAUDE_CONFIG_DIR`
with fake transcripts, a temporary git repo with a bare origin and feature worktrees on
`<prefix>/<slug>`, a hand-written `<git-common-dir>/dev-loop/board-state.json`, and `DEV_LOOP_NOTES`
for the notes folder. Every name in the temp repos is invented. HOME is a temporary folder.

Fixture choices the spec leaves open (taken from the documented loop and the transcript facts):
- Main transcripts sit in the project folder of the main worktree (Session B keeps `cwd` = the main
  checkout and `gitBranch` = `main`). Subagent transcripts sit in `<folder>/<session>/subagents/`.
- A transcript `tool_result` text is a plain string `content`; a typed prompt is a string or a list
  with one `text` block.
- Board times are ISO strings in UTC with a `Z`, keyed by the stage number as a string.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import DEVLOOP, REPO_ROOT, Board, git, put, snapshot

OPUS = "claude-opus-4-7"
SONNET = "claude-sonnet-4-6"
DAY = "2026-10-20"
METRICS_FILE = "Feature Metrics.md"
HEADER = ("| feature | track | shipped | sessions | active h | user msgs | gate runs | gate s | unit tests "
          "| acceptance tests | denials | S1-2 | S3 | S4 | S5 | S6 | S7 | total | escaped |")
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_PROJECT_DIR", "CLAUDE_CONFIG_DIR")
GATE = "python3 .agents/skills/dev-loop/scripts/devloop.py gate --summary"
STAGE_CELLS = slice(11, 17)


def at(clock: str) -> str:
    """A transcript timestamp on the fixture day."""
    return f"{DAY}T{clock}.000Z"


def board_time(clock: str) -> str:
    return f"{DAY}T{clock}Z"


def cells(line: str) -> list[str]:
    """The cells of one Markdown table row: split on `|` not escaped by `\\`."""
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def usage(inp: int = 0, w5: int = 0, w1: int = 0, read: int = 0, out: int = 0, legacy: int | None = None) -> dict:
    """A `message.usage`. `legacy`: only `cache_creation_input_tokens`, no `cache_creation` object."""
    found = {"input_tokens": inp, "cache_read_input_tokens": read, "output_tokens": out}
    if legacy is not None:
        found["cache_creation_input_tokens"] = legacy
    else:
        found["cache_creation_input_tokens"] = w5 + w1
        found["cache_creation"] = {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}
    return found


def bash(tool_id: str, command: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}


def read(tool_id: str, path: Path | str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": str(path)}}


def agent(tool_id: str, kind: str, description: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Agent",
            "input": {"subagent_type": kind, "description": description, "prompt": description}}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


class Transcript:
    """One Claude transcript file, built record by record. `records` keeps each record for copies."""

    def __init__(self, path: Path, session: str, cwd: Path, branch: str = "main") -> None:
        self.path, self.session, self.cwd, self.branch = path, session, cwd, branch
        self.lines: list[str] = []
        self.records: list[dict] = []

    def _add(self, record: dict) -> dict:
        record = {"uuid": f"{self.session}-{len(self.records) + 1:03d}", "sessionId": self.session,
                  "cwd": str(self.cwd), "gitBranch": self.branch, **record}
        self.records.append(record)
        self.lines.append(json.dumps(record))
        return record

    def user(self, clock: str, content: object, **extra: object) -> dict:
        return self._add({"type": "user", "timestamp": at(clock), "message": {"role": "user", "content": content},
                          **extra})

    def result(self, clock: str, tool_id: str, body: str, is_error: bool = False) -> dict:
        block = {"type": "tool_result", "tool_use_id": tool_id, "content": body, "is_error": is_error}
        return self.user(clock, [block])

    def assistant(self, clock: str, msg_id: str, blocks: list[dict], use: dict, model: str = OPUS) -> dict:
        return self._add({"type": "assistant", "timestamp": at(clock),
                          "message": {"id": msg_id, "type": "message", "role": "assistant", "model": model,
                                      "content": blocks, "usage": use}})

    def copy(self, record: dict) -> None:
        """A record a resumed session copied: same `uuid`, same content."""
        self.lines.append(json.dumps(record))

    def garbage(self, line: str) -> None:
        self.lines.append(line)

    def save(self) -> None:
        put(self.path, "".join(f"{line}\n" for line in self.lines))


class MetricsCase(Board):
    """The board sandbox (a main checkout `acme-widgets`, a bare origin, a notes folder, a sandboxed HOME)
    plus a fixture `CLAUDE_CONFIG_DIR`."""

    def setUp(self) -> None:
        super().setUp()
        for key in SESSION_VARS:
            self.env.pop(key, None)
        self.claude = self.root / "claude-config"
        (self.claude / "projects").mkdir(parents=True)
        self.env.update(CLAUDE_CONFIG_DIR=str(self.claude), PYTHONDONTWRITEBYTECODE="1")
        self.metrics_file = self.notes / METRICS_FILE

    # fixtures

    def folder(self, worktree: Path) -> Path:
        """The Claude project folder of a worktree: each character not `A-Za-z0-9` becomes `-`."""
        return self.claude / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(worktree))

    def transcript(self, session: str, worktree: Path | None = None) -> Transcript:
        top = worktree or self.repo
        return Transcript(self.folder(top) / f"{session}.jsonl", session, cwd=self.repo)

    def subagent(self, session: str, agent_id: str, tool_use_id: str, kind: str, description: str,
                 worktree: Path | None = None) -> Transcript:
        base = self.folder(worktree or self.repo) / session / "subagents"
        put(base / f"agent-{agent_id}.meta.json",
            json.dumps({"agentType": kind, "description": description, "toolUseId": tool_use_id}) + "\n")
        return Transcript(base / f"agent-{agent_id}.jsonl", session, cwd=self.repo)

    def board_state(self, features: dict) -> None:
        put(self.repo / ".git" / "dev-loop" / "board-state.json", json.dumps({"features": features}) + "\n")

    @staticmethod
    def times(**clocks: str) -> dict:
        return {key.lstrip("s"): board_time(clock) for key, clock in clocks.items()}

    def claim(self, slug: str) -> str:
        return f"python3 .agents/skills/dev-loop/scripts/devloop.py board --claim {slug}"

    # runs

    def metrics(self, *args: str, cwd: Path) -> tuple[int, dict | None, str]:
        result = subprocess.run([sys.executable, str(DEVLOOP), "metrics", *args], cwd=cwd, env=self.env,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        return result.returncode, payload, result.stdout[-400:] + result.stderr[-400:]

    def metrics_ok(self, *args: str, cwd: Path) -> dict:
        code, payload, tail = self.metrics(*args, cwd=cwd)
        self.assertIsInstance(payload, dict, f"metrics {' '.join(args)}: stdout is not one JSON object: {tail}")
        self.assertEqual(code, 0, payload)
        return payload

    def table_rows(self) -> list[str]:
        """The data rows of the notes file table: lines starting with `|` after the header and separator."""
        lines = self.metrics_file.read_text().splitlines()
        self.assertIn(HEADER, [line.strip() for line in lines])
        start = [line.strip() for line in lines].index(HEADER)
        rows = []
        for line in lines[start + 1:]:
            if not line.lstrip().startswith("|"):
                break
            if re.fullmatch(r"\|[\s:|-]+\|", line.strip()):
                continue
            rows.append(line)
        return rows


class TrackMFeatureTests(MetricsCase):
    def test_f1_track_m_feature_claimed_built_and_written(self) -> None:
        """F1, O1 to O11, O14 to O20, O22, O26, O28: Session A claims `widget-export`, Session B builds it with
        subagents across Stages 3 to 7 and then moves to `label-printer`; `metrics` without `--write` writes
        nothing; `metrics --write` writes the row with stage cells."""
        slug, other = "widget-export", "label-printer"
        put(self.repo / "tests" / "unit" / "test_old.py", "def test_old():\n    assert True\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "old test")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        self.spec(slug, "Widget Export", track="M", shipped="2026-10-25")
        wt = self.worktree(slug)
        wt_other = self.worktree(other)

        # O18: 3 `def test_` added and 1 removed under tests/unit/, 2 `test(` added under tests/acceptance/.
        put(wt / "tests" / "unit" / "test_old.py", "# moved to test_export.py\n")
        put(wt / "tests" / "unit" / "test_export.py",
            "def test_header():\n    pass\n\n\ndef test_rows():\n    pass\n\n\ndef test_quotes():\n    pass\n")
        put(wt / "tests" / "acceptance" / "export.test.mjs",
            "import test from 'node:test'\n\ntest('exports', () => {})\ntest('quotes', () => {})\n")
        put(wt / "src" / "export.py", "def export():\n    return ''\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "export")

        self.board_state({slug: {"backfilled": False, "stage_times": self.times(
            s1="08:00:00", s2="08:30:00", s3="10:00:00", s4="11:00:00", s5="12:00:00", s6="14:00:00",
            s7="16:00:00")}})

        # Session A: everything before the `3` time, so stage `1-2`.
        a = self.transcript("sess-a")
        a.user("09:00:00", "Frame the widget export feature")              # O2: before the first mention
        a.assistant("09:01:00", "msg_a2", [bash("toolu_claim", self.claim(slug))],
                    usage(inp=1000, w5=1000, w1=1000, read=1000, out=1000))  # O1, O9: 9350
        a.result("09:01:30", "toolu_claim", '{"notes": []}')               # O16: tool_result, not a prompt
        a.garbage("{not json")                                              # O26
        a.user("09:02:00", "Caveat: local command output follows", isMeta=True)  # O16: isMeta
        a.user("09:02:30", "<command-name>/model</command-name>")          # O16: starts with `<`
        a.assistant("09:03:00", "msg_a7", [text("Spec drafted.")], usage(legacy=1000))  # O10: 1250
        a.save()

        # Session B: cwd the main checkout, branch main (O5).
        b = self.transcript("sess-b")
        b.user("10:05:00", [text("Run Session B")])                         # O2, O16: list prompt counts
        b2 = b.assistant("10:06:00", "msg_b2", [read("toolu_r1", wt / "src" / "export.py")], usage(inp=3000))
        b.assistant("11:30:00", "msg_b4", [text("Plan written.")], usage(inp=4000))
        b.assistant("12:10:00", "msg_b5", [agent("toolu_impl", "implementer", "Task 1: export")], usage(out=100))
        impl = self.subagent("sess-b", "impl1", "toolu_impl", "implementer", "Task 1: export")
        impl.user("12:10:10", "Implement Task 1")                          # a subagent prompt is no user message
        impl.assistant("12:10:30", "msg_s1", [text("Writing.")], usage(inp=4000), model=SONNET)
        impl.assistant("12:20:30", "msg_s2", [text("Done.")], usage(out=200), model=SONNET)
        impl.save()
        b.result("12:21:00", "toolu_impl", "Task 1 done")
        b.assistant("12:22:00", "msg_b6", [bash("toolu_g1", GATE)], usage(inp=100))
        b.result("12:23:00", "toolu_g1", json.dumps({"exit": 0, "seconds": 10, "failures": []}))  # O17
        b.assistant("14:00:00", "msg_b7", [bash("toolu_g2", GATE)], usage(inp=6000))             # O11: at `6`
        b.result("14:01:00", "toolu_g2", json.dumps({"exit": 0, "seconds": 20, "failures": []}))  # O17
        b.assistant("14:02:00", "msg_b8", [bash("toolu_g3", GATE)], usage(inp=50))
        b.result("14:03:00", "toolu_g3", "Traceback (most recent call last): gate crashed")       # O17: no JSON
        b.assistant("14:04:00", "msg_b9", [bash("toolu_d1", "cat .env")], usage(inp=50))
        b.result("14:04:10", "toolu_d1", "PreToolUse:Bash hook error: [~/.claude/hooks/deny-path.sh]: denied",
                 is_error=True)                                                                    # O19
        b.assistant("14:05:00", "msg_b10", [bash("toolu_d2", "cat .env.example")], usage(inp=50))
        b.result("14:05:10", "toolu_d2", "PreToolUse:Bash hook error: [~/.claude/hooks/deny-path.sh]: denied",
                 is_error=False)                                                                   # O19: not an error
        b.assistant("14:10:00", "msg_b11", [agent("toolu_rev", "code-reviewer", "Review the branch")],
                    usage(inp=50))
        rev = self.subagent("sess-b", "rev1", "toolu_rev", "code-reviewer", "Review the branch")
        rev.user("14:10:30", "Review the branch diff")
        rev.assistant("14:15:30", "msg_r1", [text("Two findings.")], usage(inp=20000))
        rev.save()
        b15 = b.assistant("16:10:00", "msg_b15", [text("Merging.")], usage(inp=7000))             # O8
        b15b = b.assistant("16:10:01", "msg_b15", [bash("toolu_m", "gh pr merge 7 --rebase")], usage(inp=7000))
        b16 = b.user("16:20:00", "Ship it")
        # O3: after a mention of label-printer, the next records count for it.
        b.assistant("16:21:00", "msg_b17", [read("toolu_r2", wt_other / "README.md")], usage(inp=5000))
        b.user("16:22:00", "Now the label printer")
        b.assistant("16:23:00", "msg_b19", [agent("toolu_y", "implementer", "Task 1: labels")], usage(inp=5000))
        lab = self.subagent("sess-b", "lab1", "toolu_y", "implementer", "Task 1: labels")
        lab.assistant("16:23:30", "msg_y1", [text("Labels.")], usage(inp=8000))                    # O7
        lab.save()
        orphan = self.subagent("sess-b", "gone1", "toolu_nowhere", "explorer", "Find the CSV code")
        orphan.assistant("12:30:00", "msg_o1", [text("Found.")], usage(inp=30000))                 # O7
        orphan.save()
        b.save()

        # O28: a resumed session copies earlier records (same uuid), the mention among them.
        resumed = self.transcript("sess-b-resumed")
        for record in (b2, b15, b15b, b16):
            resumed.copy(record)
        resumed.save()

        # O4: a session that names no feature.
        stray = self.transcript("sess-stray")
        stray.user("13:00:00", "What does git rebase do?")
        stray.assistant("13:01:00", "msg_q1", [text("It replays commits.")], usage(inp=9999))
        stray.save()

        before = snapshot(self.notes)
        dry = self.metrics_ok(cwd=wt)
        with self.subTest(oracle=22):
            self.assertIsNone(dry["file"], dry)
            self.assertEqual(snapshot(self.notes), before)

        out = self.metrics_ok("--write", cwd=wt)
        with self.subTest(oracle="1, 5, 28"):
            self.assertEqual((out["feature"], out["track"], out["shipped"]), (slug, "M", "2026-10-25"), out)
            self.assertEqual(out["sessions"], 2, out)
        with self.subTest(oracle="2, 16, 28"):
            self.assertEqual(out["user_messages"], 3, out)
        with self.subTest(oracle="15"):
            self.assertEqual(out["active_hours"], 0.4, out)   # 180 + 60 + 180 + 930 + 1 = 1351 s
        with self.subTest(oracle="17"):
            self.assertEqual(out["gate"], {"runs": 2, "seconds": 30}, out)
        with self.subTest(oracle="18"):
            self.assertEqual(out["tests"], {"unit": 2, "acceptance": 2}, out)
        with self.subTest(oracle="19"):
            self.assertEqual(out["denials"], {"deny-path.sh": 1}, out)
        with self.subTest(oracle="3, 4, 7, 8, 9, 10, 11"):
            self.assertFalse(out["unattributed"], out)
            weighted = {key: value["weighted"] for key, value in out["stages"].items()}
            self.assertEqual(weighted, {"1-2": 10600, "3": 3000, "4": 4000, "5": 5600, "6": 26200, "7": 7000}, out)
            self.assertEqual(out["total"], 56400, out)
        with self.subTest(oracle="14"):
            zero = {"input": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0}
            self.assertEqual(out["stages"]["1-2"]["models"],
                             {OPUS: {"input": 1000, "cache_write_5m": 2000, "cache_write_1h": 1000,
                                     "cache_read": 1000, "output": 1000}}, out)
            self.assertEqual(out["stages"]["5"]["models"],
                             {OPUS: {**zero, "input": 100, "output": 100},
                              SONNET: {**zero, "input": 4000, "output": 200}}, out)
        with self.subTest(oracle="6, 7"):
            self.assertEqual(out["agents"], [
                {"type": "code-reviewer", "description": "Review the branch", "weighted": 20000, "seconds": 300},
                {"type": "implementer", "description": "Task 1: export", "weighted": 5000, "seconds": 620},
            ], out)
        with self.subTest(oracle="3, 6"):
            label = self.metrics_ok("--feature", other, cwd=wt)
            self.assertEqual(label["total"], 18000, label)
            self.assertEqual([item["description"] for item in label["agents"]], ["Task 1: labels"], label)

        expected = [slug, "M", "2026-10-25", "2", "0.4", "3", "2", "30", "2", "2", "1",
                    "11", "3", "4", "6", "26", "7", "56", ""]
        with self.subTest(oracle=20):
            self.assertEqual(Path(out["file"]).resolve(), self.metrics_file.resolve(), out)
            self.assertEqual(cells(out["row"]), expected, out["row"])
            content = self.metrics_file.read_text()
            self.assertTrue(content.startswith("---\n"), content)
            front = content.split("---", 2)[1]
            self.assertRegex(front, r"(?m)^type: note\s*$")
            self.assertRegex(front, r"(?m)^tags: \[dev-loop, feature-metrics\]\s*$")
            self.assertRegex(content, r"(?m)^# Feature Metrics\s*$")
            head = content.split(HEADER, 1)[0]
            self.assertTrue(any("5 features" in line for line in head.splitlines()), content)
            rows = self.table_rows()
            self.assertEqual([cells(row) for row in rows], [expected], content)


class EscapedRerunTests(MetricsCase):
    def test_f2_rerun_keeps_the_escaped_cell(self) -> None:
        """F2, O21: after a hand edit of `escaped` and another row, a second `--write` replaces the row of the
        feature, keeps its `escaped` value, and leaves the other row unchanged."""
        slug = "bulk-import"
        self.spec(slug, "Bulk Import", track="M")
        wt = self.worktree(slug)
        self.board_state({slug: {"backfilled": False, "stage_times": self.times(
            s3="10:00:00", s4="11:00:00", s5="12:00:00", s6="14:00:00", s7="16:00:00")}})
        first = self.transcript("sess-1")
        first.assistant("09:00:00", "msg_1", [bash("toolu_c", self.claim(slug))], usage(inp=2000))
        first.assistant("10:30:00", "msg_2", [read("toolu_r", wt / "README.md")], usage(inp=3000))
        first.save()
        self.metrics_ok("--write", cwd=wt)

        lines = self.metrics_file.read_text().splitlines()
        index = next(n for n, line in enumerate(lines) if line.strip().startswith(f"| {slug} |"))
        own = cells(lines[index])
        self.assertEqual(own[-1], "", lines[index])
        edited = "| " + " | ".join(own[:-1] + ["1: duplicate rows on retry"]) + " |"
        other_row = "| older-feature | S |  | 1 | 0.5 | 2 | 1 | 12 | 1 | 0 | 0 | - | - | - | 4 | 2 | 1 | 7 |  |"
        lines[index:index + 1] = [other_row, edited]
        self.metrics_file.write_text("\n".join(lines) + "\n")
        before = self.metrics_file.read_text().splitlines()

        second = self.transcript("sess-2")
        second.assistant("12:30:00", "msg_3", [read("toolu_r2", wt / "src" / "load.py")], usage(inp=5000))
        second.save()
        out = self.metrics_ok("--write", cwd=wt)
        self.assertEqual(out["sessions"], 2, out)

        after = self.metrics_file.read_text().splitlines()
        self.assertEqual(len(after), len(before), "\n".join(after))
        row_at = index + 1
        self.assertEqual(after[:row_at], before[:row_at])
        self.assertEqual(after[row_at + 1:], before[row_at + 1:])
        new = cells(after[row_at])
        self.assertEqual(new[:-1], cells(out["row"])[:-1], after[row_at])
        self.assertEqual(new[0], slug)
        self.assertEqual(new[3], "2", after[row_at])
        self.assertEqual(new[-2], "10", after[row_at])
        self.assertEqual(new[-1], "1: duplicate rows on retry", after[row_at])


class BackfilledFeatureTests(MetricsCase):
    def test_f3_backfilled_feature_has_question_marks_and_a_total(self) -> None:
        """F3, O12: a feature with a spec that the board saw first at Stage 5 (`backfilled` true):
        `unattributed` true, `stages` `{}`, `total` holds all tokens, each stage cell of the row is `?`."""
        slug = "audit-log"
        self.spec(slug, "Audit Log", track="M")
        wt = self.worktree(slug)
        self.board_state({slug: {"backfilled": True, "stage_times": self.times(
            s5="12:00:00", s6="14:00:00", s7="16:00:00")}})
        session = self.transcript("sess-1")
        session.assistant("12:10:00", "msg_1", [read("toolu_r", wt / "src" / "audit.py")], usage(inp=2000))
        session.assistant("14:10:00", "msg_2", [text("Reviewed.")], usage(inp=3000))
        session.save()

        out = self.metrics_ok(cwd=wt)
        self.assertTrue(out["unattributed"], out)
        self.assertEqual(out["stages"], {}, out)
        self.assertEqual(out["total"], 5000, out)
        row = cells(out["row"])
        self.assertEqual(row[STAGE_CELLS], ["?"] * 6, out["row"])
        self.assertEqual(row[17], "5", out["row"])


class TrackSFeatureTests(MetricsCase):
    def test_f4_track_s_feature_has_dashes_for_s3_and_s4(self) -> None:
        """F4, O13, O23: a Track S feature (no spec, `fix/` branch) with `backfilled` true and `stage_times`
        `5`, `6`, `7`: `track` `S`, `unattributed` false, S3 and S4 `-`, S5 to S7 numbers."""
        slug = "fix-typo"
        wt = self.worktree(slug, prefix="fix")
        self.board_state({slug: {"backfilled": True, "stage_times": self.times(
            s5="12:00:00", s6="14:00:00", s7="16:00:00")}})
        session = self.transcript("sess-1")
        session.assistant("12:05:00", "msg_1", [bash("toolu_b", f"git -C {wt} status --short")], usage(inp=5000))
        session.assistant("14:05:00", "msg_2", [text("Reviewed.")], usage(inp=6000))
        session.assistant("16:05:00", "msg_3", [text("Merged.")], usage(inp=7000))
        session.save()

        out = self.metrics_ok(cwd=wt)
        self.assertEqual((out["feature"], out["track"], out["shipped"]), (slug, "S", ""), out)
        self.assertFalse(out["unattributed"], out)
        self.assertEqual({key: value["weighted"] for key, value in out["stages"].items()},
                         {"5": 5000, "6": 6000, "7": 7000}, out)
        row = cells(out["row"])
        self.assertEqual(row[12:17], ["-", "-", "5", "6", "7"], out["row"])
        self.assertEqual(row[17], "18", out["row"])


class ErrorTests(MetricsCase):
    def test_f5_errors_no_records_no_feature_usage(self) -> None:
        """F5, O24, O25: no transcript record for the feature: exit 1, `error` `no transcript records for
        <slug>`, nothing written; a branch without `/` and no `--feature`: exit 1 with `error`; an unknown
        option: exit 2."""
        slug = "empty-feature"
        self.spec(slug, "Empty Feature", track="M")
        wt = self.worktree(slug)
        self.board_state({slug: {"backfilled": False, "stage_times": self.times(s3="10:00:00")}})
        stray = self.transcript("sess-stray")
        stray.user("10:00:00", "Explain the build")
        stray.assistant("10:01:00", "msg_1", [text("It runs make.")], usage(inp=1000))
        stray.save()
        before = snapshot(self.notes)

        with self.subTest(oracle=24):
            code, payload, tail = self.metrics("--write", cwd=wt)
            self.assertEqual(code, 1, tail)
            self.assertIsInstance(payload, dict, tail)
            self.assertEqual(payload.get("error"), f"no transcript records for {slug}", payload)
            self.assertEqual(snapshot(self.notes), before)
        with self.subTest(oracle=25, case="no feature"):
            code, payload, tail = self.metrics(cwd=self.repo)
            self.assertEqual(code, 1, tail)
            self.assertIsInstance(payload, dict, tail)
            self.assertTrue(payload.get("error"), payload)
        with self.subTest(oracle=25, case="usage"):
            code, _, tail = self.metrics("--bogus", cwd=wt)
            self.assertEqual(code, 2, tail)


class KitTextTests(unittest.TestCase):
    def test_o27_kit_text(self) -> None:
        """O27: `skill/SKILL.md` Stage 7 names `devloop.py metrics --write` and the report line `metrics:`;
        `skill/references/codex.md` names the gap; AGENTS.md holds the `metrics` CLI entry and the data rule."""
        skill = (REPO_ROOT / "skill" / "SKILL.md").read_text()
        self.assertIn("devloop.py metrics --write", skill)
        self.assertIn("metrics:", skill)
        codex = (REPO_ROOT / "skill" / "references" / "codex.md").read_text()
        self.assertTrue(any("metrics" in line.lower() and re.search(r"(?i)not counted|skip", line)
                            for line in codex.splitlines()), "codex.md names no metrics gap")
        agents = (REPO_ROOT / "AGENTS.md").read_text()
        self.assertRegex(agents, r"(?m)^- `metrics[ `]")
        self.assertTrue(any("5 features" in line and "cost" in line for line in agents.splitlines()),
                        "AGENTS.md holds no cost data rule")


if __name__ == "__main__":
    unittest.main()

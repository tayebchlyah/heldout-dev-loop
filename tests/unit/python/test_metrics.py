#!/usr/bin/env python3
"""Tests for metrics.py: transcript read, feature attribution, token weights, stages, numbers and the row."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
import metrics  # noqa: E402

DEVLOOP = HERE.parents[2] / "skill" / "scripts" / "devloop.py"


def record(uid: str, ts: str | None, kind: str = "assistant", **message) -> dict:
    rec: dict = {"uuid": uid, "type": kind, "message": message}
    if ts is not None:
        rec["timestamp"] = ts
    return rec


def tool_use(uid: str, ts: str, name: str, inp: dict, tid: str = "", **extra) -> dict:
    return record(uid, ts, content=[{"type": "tool_use", "id": tid or f"tu-{uid}", "name": name, "input": inp}], **extra)


def write_jsonl(path: Path, records: list, raw: list[str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(raw or []) + "".join(json.dumps(r) + "\n" for r in records))
    return path


class TempDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class MetricsAttributionTests(TempDir):
    WT_A = Path("/w/feat-a")
    WT_B = Path("/w/feat-b")

    def features(self, transcripts: list) -> list[str]:
        return [c.feature for c in metrics.attribute(transcripts, {"a": self.WT_A, "b": self.WT_B})]

    def main(self, records: list) -> metrics.Transcript:
        return metrics.Transcript(path=self.tmp / "x.jsonl", records=records, meta=None)

    def test_weight_full_message(self) -> None:
        raw = metrics.raw_usage({"usage": {
            "input_tokens": 1000, "cache_creation_input_tokens": 2000,
            "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 1000},
            "cache_read_input_tokens": 1000, "output_tokens": 1000}})
        self.assertEqual(metrics.weigh(raw), 9350)

    def test_weight_no_cache_creation_object(self) -> None:
        raw = metrics.raw_usage({"usage": {"cache_creation_input_tokens": 1000}})
        self.assertEqual(raw["cache_write_5m"], 1000)
        self.assertEqual(metrics.weigh(raw), 1250)

    def test_weight_bad_numbers_count_zero(self) -> None:
        raw = metrics.raw_usage({"usage": {"input_tokens": None, "output_tokens": "x", "cache_read_input_tokens": True}})
        self.assertEqual(metrics.weigh(raw), 0)
        self.assertEqual(metrics.weigh(metrics.raw_usage({"usage": "x"})), 0)

    def test_project_folder_name(self) -> None:
        (self.tmp / "-a-b-c-d-e").mkdir()
        found = metrics.project_folders(self.tmp, [Path("/a/b.c/d_e"), Path("/missing")])
        self.assertEqual(found, [self.tmp / "-a-b-c-d-e"])

    def test_load_skips_bad_lines(self) -> None:
        write_jsonl(self.tmp / "s.jsonl", [record("1", "2026-01-01T00:00:00Z")], raw=["not json\n", "[1]\n", "3\n"])
        loaded = metrics.load([self.tmp])
        self.assertEqual([len(t.records) for t in loaded], [1])

    def test_load_orders_by_first_timestamp(self) -> None:
        write_jsonl(self.tmp / "a.jsonl", [record("1", "2026-01-02T00:00:00Z")])
        write_jsonl(self.tmp / "b.jsonl", [record("2", "2026-01-01T00:00:00Z")])
        write_jsonl(self.tmp / "c.jsonl", [record("3", None)])
        meta = self.tmp / "s" / "subagents" / "agent-x.meta.json"
        write_jsonl(self.tmp / "s" / "subagents" / "agent-x.jsonl", [record("4", "2026-01-01T12:00:00Z")])
        meta.write_text(json.dumps({"agentType": "implementer", "toolUseId": "t"}))
        loaded = metrics.load([self.tmp])
        self.assertEqual([t.path.name for t in loaded], ["b.jsonl", "agent-x.jsonl", "a.jsonl", "c.jsonl"])
        self.assertEqual(loaded[1].meta["agentType"], "implementer")
        self.assertIsNone(loaded[0].meta)

    def test_mention_by_path_terminators(self) -> None:
        for text, hit in (("/w/feat-a/x", True), ("/w/feat-a", True), ("cd /w/feat-a && ls", True),
                          ("/w/feat-ab/x", False)):
            with self.subTest(text=text):
                got = self.features([self.main([tool_use("1", "2026-01-01T00:00:00Z", "Bash", {"command": text})])])
                self.assertEqual(got, ["a"] if hit else [])

    def test_mention_by_claim(self) -> None:
        for name, inp, hit in (("Bash", {"command": "python3 x/devloop.py board --claim my-feat"}, True),
                               ("Bash", {"command": "python3 x/devloop.py board --claim=my-feat"}, True),
                               ("Agent", {"prompt": "run devloop.py board --claim my-feat"}, False)):
            with self.subTest(inp=inp):
                got = [c.feature for c in metrics.attribute(
                    [self.main([tool_use("1", "2026-01-01T00:00:00Z", name, inp)])], {})]
                self.assertEqual(got, ["my-feat"] if hit else [])

    def test_records_before_first_mention_and_switch(self) -> None:
        recs = [record("1", "2026-01-01T00:00:01Z"),
                tool_use("2", "2026-01-01T00:00:02Z", "Read", {"file_path": "/w/feat-a/f.py"}),
                record("3", "2026-01-01T00:00:03Z"),
                tool_use("4", "2026-01-01T00:00:04Z", "Read", {"file_path": "/w/feat-b/f.py"}),
                record("5", "2026-01-01T00:00:05Z", kind="user", content="hi")]
        self.assertEqual(self.features([self.main(recs)]), ["a", "a", "a", "b", "b"])

    def test_transcript_with_no_mention_gives_nothing(self) -> None:
        self.assertEqual(self.features([self.main([record("1", "2026-01-01T00:00:00Z")])]), [])

    def test_subagent_follows_tool_use(self) -> None:
        main = self.main([tool_use("1", "2026-01-01T00:00:00Z", "Agent", {"prompt": "work in /w/feat-a"}, tid="T1")])
        sub = metrics.Transcript(path=self.tmp / "agent-1.jsonl", meta={"toolUseId": "T1"},
                                 records=[tool_use("2", "2026-01-01T00:00:01Z", "Agent", {"prompt": "x"}, tid="T2")])
        nested = metrics.Transcript(path=self.tmp / "agent-2.jsonl", meta={"toolUseId": "T2"},
                                    records=[record("3", "2026-01-01T00:00:02Z")])
        lost = metrics.Transcript(path=self.tmp / "agent-3.jsonl", meta={"toolUseId": "nope"},
                                  records=[record("4", "2026-01-01T00:00:03Z")])
        items = metrics.attribute([main, nested, sub, lost], {"a": self.WT_A})
        self.assertEqual(sorted((c.record["uuid"], c.feature) for c in items), [("1", "a"), ("2", "a"), ("3", "a")])

    def test_copy_flag(self) -> None:
        first = self.main([tool_use("1", "2026-01-01T00:00:00Z", "Read", {"file_path": "/w/feat-a/f"})])
        second = metrics.Transcript(path=self.tmp / "y.jsonl", meta=None,
                                    records=[first.records[0], record("2", "2026-01-01T00:01:00Z")])
        items = metrics.attribute([first, second], {"a": self.WT_A})
        self.assertEqual([(c.record["uuid"], c.feature, c.copy) for c in items],
                         [("1", "a", False), ("1", "a", True), ("2", "a", False)])

    def test_bad_shapes_no_crash(self) -> None:
        recs = [{"uuid": "1", "type": "assistant", "message": "x"},
                {"uuid": "2", "type": "assistant", "message": {"content": 3}},
                {"uuid": "3", "type": "assistant", "message": {"content": [3, {"type": "tool_use", "input": None}]}},
                tool_use("4", "2026-01-01T00:00:00Z", "Read", {"file_path": "/w/feat-a/f"})]
        self.assertEqual(self.features([self.main(recs)]), ["a"] * 4)


def usage_msg(uid: str, ts: str | None, mid: str, model: str = "m1", **usage) -> dict:
    return record(uid, ts, id=mid, model=model, usage=usage)


def result(uid: str, ts: str, tid: str, text, is_error: bool = False) -> dict:
    return record(uid, ts, kind="user", content=[{"type": "tool_result", "tool_use_id": tid, "content": text,
                                                   "is_error": is_error}])


def items(transcripts: list) -> list:
    return metrics.attribute(transcripts, {"a": Path("/w/feat-a")})


MENTION = {"file_path": "/w/feat-a/f"}


class MetricsNumbersTests(TempDir):
    def main(self, records: list, name: str = "x") -> metrics.Transcript:
        return metrics.Transcript(path=self.tmp / f"{name}.jsonl", meta=None,
                                  records=[tool_use("m-" + name, "2026-01-01T00:00:00Z", "Read", MENTION), *records])

    def state(self, entry: dict | None, raw: str | None = None) -> Path:
        path = self.tmp / "board-state.json"
        path.write_text(raw if raw is not None else json.dumps({"features": {"a": entry} if entry else {}}))
        return path

    def test_stage_boundaries(self) -> None:
        bounds, present = metrics.boundaries(self.state({"backfilled": False, "stage_times": {
            "2": "2026-01-01T09:00:00Z", "3": "2026-01-01T10:00:00Z", "5": "2026-01-01T12:00:00Z",
            "6": "2026-01-01T13:00:00Z", "7": "2026-01-01T14:00:00Z"}}), "a", True)
        self.assertEqual(present, {"2", "3", "5", "6", "7"})
        at = metrics.parse_time
        self.assertEqual(metrics.stage_of(at("2026-01-01T09:30:00Z"), bounds), "1-2")
        self.assertEqual(metrics.stage_of(at("2026-01-01T12:30:00Z"), bounds), "5")
        self.assertEqual(metrics.stage_of(at("2026-01-01T13:00:00Z"), bounds), "6")
        self.assertEqual(metrics.stage_of(None, bounds), "1-2")

    def test_unattributed_cases(self) -> None:
        times = {"5": "2026-01-01T12:00:00Z", "6": "2026-01-01T13:00:00Z", "7": "2026-01-01T14:00:00Z"}
        self.assertIsNone(metrics.boundaries(self.tmp / "none.json", "a", True)[0])
        self.assertIsNone(metrics.boundaries(self.state(None, raw="{bad"), "a", True)[0])
        self.assertIsNone(metrics.boundaries(self.state(None), "a", True)[0])
        self.assertIsNone(metrics.boundaries(self.state({"backfilled": True, "stage_times": times}), "a", True)[0])
        self.assertIsNone(metrics.boundaries(self.state({"stage_times": {"2": "2026-01-01T12:00:00Z"}}), "a", True)[0])
        bounds, _ = metrics.boundaries(self.state({"backfilled": True, "stage_times": times}), "a", False)
        self.assertEqual([k for _, k in bounds], ["5", "6", "7"])

    def test_message_counted_once(self) -> None:
        recs = [usage_msg("1", "2026-01-01T00:00:01Z", "msg", input_tokens=100),
                usage_msg("2", "2026-01-01T00:00:02Z", "msg", input_tokens=100)]
        self.assertEqual(metrics.numbers(items([self.main(recs)]), "a", [])["total"], 100)

    def test_models_split(self) -> None:
        bounds = [(metrics.parse_time("2026-01-01T00:00:00Z"), "5")]
        recs = [usage_msg("1", "2026-01-01T00:00:01Z", "m1", "opus", input_tokens=10, output_tokens=2),
                usage_msg("2", "2026-01-01T00:00:02Z", "m2", "haiku", cache_read_input_tokens=1000)]
        got = metrics.numbers(items([self.main(recs)]), "a", bounds)["stages"]
        self.assertEqual(got["5"]["weighted"], 120)
        self.assertEqual(got["5"]["models"]["opus"], {"input": 10, "cache_write_5m": 0, "cache_write_1h": 0,
                                                       "cache_read": 0, "output": 2})
        self.assertEqual(got["5"]["models"]["haiku"]["cache_read"], 1000)

    def test_active_hours(self) -> None:
        recs = [record("1", "2026-01-01T00:01:00Z"), record("2", "2026-01-01T00:06:01Z"),
                record("3", "2026-01-01T00:08:01Z")]
        got = metrics.numbers(items([self.main(recs)]), "a", [])
        self.assertEqual(got["active_hours"], round(180 / 3600, 1))
        long = [record(str(i), f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00Z") for i in range(1, 121)]
        self.assertEqual(metrics.numbers(items([self.main(long)]), "a", [])["active_hours"], 2.0)

    def test_user_messages(self) -> None:
        recs = [record("1", "2026-01-01T00:00:01Z", kind="user", content="fix it"),
                record("2", "2026-01-01T00:00:02Z", kind="user", content=[{"type": "text", "text": "also this"}]),
                result("3", "2026-01-01T00:00:03Z", "t", "ok"),
                {**record("4", "2026-01-01T00:00:04Z", kind="user", content="meta"), "isMeta": True},
                record("5", "2026-01-01T00:00:05Z", kind="user", content="<command-name>/x</command-name>"),
                record("6", "2026-01-01T00:00:06Z", kind="user", content=[{"type": "text", "text": "<system>"}])]
        sub = metrics.Transcript(self.tmp / "agent-1.jsonl", meta={"toolUseId": "tu-m-x"},
                                 records=[record("7", "2026-01-01T00:00:07Z", kind="user", content="brief")])
        self.assertEqual(metrics.numbers(items([self.main(recs), sub]), "a", [])["user_messages"], 2)

    def test_gate_runs(self) -> None:
        gate = "python3 x/devloop.py gate --summary"
        recs = [tool_use("1", "2026-01-01T00:00:01Z", "Bash", {"command": gate}, tid="g1"),
                result("2", "2026-01-01T00:00:02Z", "g1", json.dumps({"seconds": 10, "exit": 0})),
                tool_use("3", "2026-01-01T00:00:03Z", "Bash", {"command": gate}, tid="g2"),
                result("4", "2026-01-01T00:00:04Z", "g2", [{"type": "text", "text": json.dumps({"seconds": 20.5})}]),
                tool_use("5", "2026-01-01T00:00:05Z", "Bash", {"command": gate}, tid="g3"),
                result("6", "2026-01-01T00:00:06Z", "g3", "not json"),
                tool_use("7", "2026-01-01T00:00:07Z", "Bash", {"command": "ls"}, tid="g4"),
                result("8", "2026-01-01T00:00:08Z", "g4", json.dumps({"seconds": 5}))]
        self.assertEqual(metrics.numbers(items([self.main(recs)]), "a", [])["gate"], {"runs": 2, "seconds": 30.5})

    def test_denials(self) -> None:
        text = "PreToolUse:Bash hook error: [~/.claude/hooks/deny-path.sh]: denied"
        recs = [result("1", "2026-01-01T00:00:01Z", "t1", text, is_error=True),
                result("2", "2026-01-01T00:00:02Z", "t2", text),
                result("3", "2026-01-01T00:00:03Z", "t3", [{"type": "text", "text": text}], is_error=True),
                result("4", "2026-01-01T00:00:04Z", "t4", "PreToolUse:Edit hook error: [python3 x.py]: no",
                       is_error=True),
                result("5", "2026-01-01T00:00:05Z", "t5", 'PreToolUse:Read hook error: ["a.sh" b/c.sh x]: no',
                       is_error=True)]
        self.assertEqual(metrics.numbers(items([self.main(recs)]), "a", [])["denials"],
                         {"deny-path.sh": 2, "other": 1, "c.sh": 1})

    def test_sessions_and_copies(self) -> None:
        first = self.main([usage_msg("1", "2026-01-01T00:00:01Z", "m1", input_tokens=10),
                           record("2", "2026-01-01T00:00:02Z", kind="user", content="go")])
        second = metrics.Transcript(self.tmp / "y.jsonl", meta=None, records=list(first.records))
        got = metrics.numbers(items([first, second]), "a", [])
        self.assertEqual((got["sessions"], got["total"], got["user_messages"]), (1, 10, 1))

    def test_agents(self) -> None:
        main = self.main([tool_use("1", "2026-01-01T00:00:01Z", "Agent", {"prompt": "x"}, tid="T1"),
                          tool_use("2", "2026-01-01T00:00:02Z", "Agent", {"prompt": "y"}, tid="T2")])
        small = metrics.Transcript(self.tmp / "agent-1.jsonl", meta={"toolUseId": "T1", "agentType": "implementer",
                                                                "description": "Task 1"},
                                   records=[usage_msg("3", "2026-01-01T00:00:10Z", "s1", input_tokens=10),
                                    record("4", "2026-01-01T00:00:40Z")])
        big = metrics.Transcript(self.tmp / "agent-2.jsonl", meta={"toolUseId": "T2", "agentType": "spec-tester"},
                                 records=[usage_msg("5", "2026-01-01T00:00:10Z", "s2", output_tokens=10)])
        got = metrics.numbers(items([main, small, big]), "a", [])["agents"]
        self.assertEqual(got, [{"type": "spec-tester", "description": "", "weighted": 50, "seconds": 0.0},
                               {"type": "implementer", "description": "Task 1", "weighted": 10, "seconds": 30.0}])

    def test_unattributed_numbers(self) -> None:
        got = metrics.numbers(items([self.main([usage_msg("1", "2026-01-01T00:00:01Z", "m", output_tokens=1)])]),
                              "a", None)
        self.assertEqual((got["unattributed"], got["stages"], got["total"]), (True, {}, 5))

    def test_bad_timestamp(self) -> None:
        recs = [usage_msg("1", "yesterday", "m", input_tokens=7), record("2", None)]
        got = metrics.numbers(items([self.main(recs)]), "a", [(0.0, "3")])
        self.assertEqual((got["total"], list(got["stages"]), got["active_hours"]), (7, ["1-2", "3"], 0.0))
        self.assertEqual(got["stages"]["1-2"]["weighted"], 7)

    def test_test_counts(self) -> None:
        repo = self.tmp / "repo"
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
        repo.mkdir()
        run("init", "-q", "-b", "main")
        unit = repo / "tests" / "unit" / "test_x.py"
        unit.parent.mkdir(parents=True)
        unit.write_text("def test_old():\n    pass\n")
        run("add", ".")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        run("switch", "-qc", "feat/a")
        unit.write_text("".join(f"def test_{i}():\n    pass\n" for i in range(3)))
        acc = repo / "tests" / "acceptance" / "x.test.mjs"
        acc.parent.mkdir(parents=True)
        acc.write_text("test('a', () => {});\n  it('b', () => {});\n")
        (repo / "x_test.go").write_text("func TestX(t *testing.T) {}\n")
        run("add", ".")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "feat")
        self.assertEqual(metrics.test_counts(repo, "refs/heads/main"), {"unit": 3, "acceptance": 2})


def payload(**over) -> dict:
    base = {"sessions": 2, "active_hours": 1.5, "user_messages": 4, "gate": {"runs": 3, "seconds": 61.6},
            "tests": {"unit": 5, "acceptance": 1}, "denials": {"a.sh": 2, "other": 1}, "unattributed": False,
            "stages": {"3": {"weighted": 1500}, "5": {"weighted": 12_499}, "6": {"weighted": 400}}, "total": 15_399}
    return {**base, **over}


ROW = "| a | M | 2026-10-25 | 2 | 1.5 | 4 | 3 | 62 | 5 | 1 | 3 | 0 | 2 | - | 12 | 0 | 0 | 15 |  |"


class MetricsCommandTests(TempDir):
    def test_row_cells(self) -> None:
        self.assertEqual(metrics.row("a", "M", "2026-10-25", payload(), {"3", "5", "6", "7"}), ROW)

    def test_row_unattributed(self) -> None:
        line = metrics.row("a", "M", "", payload(unattributed=True, stages={}), {"5"})
        self.assertEqual(line.split(" | ")[11:17], ["?"] * 6)

    def test_write_new_file(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        metrics.write_row(file, "a", ROW)
        lines = file.read_text().splitlines()
        self.assertEqual(lines[:4], ["---", "type: note", "tags: [dev-loop, feature-metrics]", "---"])
        self.assertIn("# Feature Metrics", lines)
        self.assertIn(metrics.DATA_RULE, lines)
        self.assertIn("5 features", metrics.DATA_RULE)
        at = lines.index(metrics.HEADER)
        self.assertEqual(lines[at + 1].replace(" ", "").strip("|").split("|"), ["---"] * 19)
        self.assertEqual(lines[at + 2:], [ROW])

    def test_write_replace_keeps_escaped(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        other = "| b | S | | 1 | 0.1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | - | - | 1 | 1 | 1 | 3 |  |"
        file.write_text("intro\n\n" + metrics.HEADER + "\n|---|\n" + ROW.replace("|  |", "| yes: #12 |") + "\n"
                        + other + "\n\ntail\n")
        metrics.write_row(file, "a", ROW.replace("| 15 |", "| 99 |"))
        text = file.read_text()
        self.assertIn("| 99 | yes: #12 |", text)
        self.assertIn(other + "\n", text)
        self.assertTrue(text.startswith("intro\n") and text.endswith("\ntail\n"))
        self.assertEqual(text.count("| a |"), 1)

    def test_write_appends_after_last_table_line(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        file.write_text(metrics.HEADER + "\n|---|\n| b | x |\nnotes after\n")
        metrics.write_row(file, "a", ROW)
        self.assertEqual(file.read_text().splitlines()[3:], [ROW, "notes after"])

    def test_write_no_table(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        file.write_text("just text\n")
        metrics.write_row(file, "a", ROW)
        lines = file.read_text().splitlines()
        self.assertEqual(lines[0], "just text")
        self.assertEqual(lines[-1], ROW)
        self.assertIn(metrics.HEADER, lines)

    def test_write_slug_feature_keeps_header(self) -> None:
        file = self.tmp / "Feature Metrics.md"
        line = ROW.replace("| a |", "| feature |", 1)
        metrics.write_row(file, "feature", line)
        metrics.write_row(file, "feature", line)
        lines = file.read_text().splitlines()
        self.assertEqual(lines.count(metrics.HEADER), 1)
        self.assertEqual(lines.count(line), 1)

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
                                    "tests", "denials", "unattributed", "stages", "total", "agents", "row", "file"})

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

    def test_cli_track_s(self) -> None:
        self.fixture(slug="quick-fix", prefix="fix", spec=False)
        self.transcript(self.work())
        code, out = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual((out["track"], out["unattributed"]), ("S", False))
        self.assertEqual(out["row"].split(" | ")[12:14], ["-", "-"])


class MetricsTextTests(unittest.TestCase):
    def test_kit_text(self) -> None:
        root = HERE.parents[2]
        skill = (root / "skill" / "SKILL.md").read_text()
        ship = skill[skill.index("## 7 Ship"):]
        self.assertIn("devloop.py metrics --write", ship)
        self.assertIn("metrics: <file>", ship)
        self.assertIn("metrics: failed: <error>", ship)
        codex = (root / "skill" / "references" / "codex.md").read_text()
        self.assertTrue(any("metrics" in line and "not counted" in line for line in codex.splitlines()))
        agents = (root / "AGENTS.md").read_text().splitlines()
        self.assertTrue(any(line.startswith("- `metrics") for line in agents))
        self.assertTrue(any("5 features" in line and "cost" in line and "Feature Metrics.md" in line for line in agents))


if __name__ == "__main__":
    unittest.main()

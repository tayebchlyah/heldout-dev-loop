#!/usr/bin/env python3
"""Tests for metrics.py: transcript read, feature attribution, token weights, stages, numbers, gate logs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from support import ROOT

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import metrics  # noqa: E402

DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"


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

    def test_subagent_reusing_main_uuids_is_not_a_copy(self) -> None:
        main = self.main([tool_use("u1", "2026-01-01T00:00:00Z", "Read", {"file_path": "/w/feat-a/f"}),
                          tool_use("u2", "2026-01-01T00:00:01Z", "Agent", {"prompt": "x"}, tid="T1")])
        sub = metrics.Transcript(path=self.tmp / "agent-1.jsonl", meta={"toolUseId": "T1"},
                                 records=[record("u1", "2026-01-01T00:00:02Z"), record("u2", "2026-01-01T00:00:03Z")])
        items = metrics.attribute([main, sub], {"a": self.WT_A})
        self.assertEqual([(c.transcript is sub, c.copy) for c in items],
                         [(False, False), (False, False), (True, False), (True, False)])

    def test_subagents_reusing_each_others_uuids_are_not_copies(self) -> None:
        main = self.main([tool_use("m1", "2026-01-01T00:00:00Z", "Agent", {"prompt": "work in /w/feat-a"}, tid="T1"),
                          tool_use("m2", "2026-01-01T00:00:01Z", "Agent", {"prompt": "x"}, tid="T2")])
        subs = [metrics.Transcript(path=self.tmp / f"agent-{n}.jsonl", meta={"toolUseId": f"T{n}"},
                                   records=[record("s1", "2026-01-01T00:00:02Z"), record("s2", "2026-01-01T00:00:03Z")])
                for n in (1, 2)]
        items = metrics.attribute([main, *subs], {"a": self.WT_A})
        self.assertEqual([c.copy for c in items if c.transcript is not main], [False] * 4)

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


def api(uid: str, ts: str, params: object) -> dict:
    return {"uuid": uid, "type": "api-request", "timestamp": ts, "params": params}


def effort(uid: str, ts: str, level: object) -> dict:
    return api(uid, ts, {"output_config": {"effort": level}})


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

    def test_message_counted_once_over_features(self) -> None:
        recs = [tool_use("1", "2026-01-01T00:00:00Z", "Read", MENTION),
                usage_msg("2", "2026-01-01T00:00:01Z", "msg", input_tokens=100),
                tool_use("3", "2026-01-01T00:00:02Z", "Read", {"file_path": "/w/feat-b/f"}),
                usage_msg("4", "2026-01-01T00:00:03Z", "msg", input_tokens=100)]
        both = metrics.attribute([metrics.Transcript(self.tmp / "x.jsonl", recs, None)],
                                 {"a": Path("/w/feat-a"), "b": Path("/w/feat-b")})
        self.assertEqual(metrics.numbers(both, "a", [])["total"], 100)
        self.assertEqual(metrics.numbers(both, "b", [])["total"], 0)

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

    def test_user_messages_skip_summary_and_interrupt(self) -> None:
        recs = [{**record("1", "2026-01-01T00:00:01Z", kind="user", content="summary"), "isCompactSummary": True},
                record("2", "2026-01-01T00:00:02Z", kind="user", content="[Request interrupted by user]"),
                record("3", "2026-01-01T00:00:03Z", kind="user",
                       content=[{"type": "text", "text": "[Request interrupted by user for tool use]"}]),
                record("4", "2026-01-01T00:00:04Z", kind="user", content="real")]
        self.assertEqual(metrics.numbers(items([self.main(recs)]), "a", [])["user_messages"], 1)

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

    def test_gate_red_result_counted(self) -> None:
        gate = "python3 x/devloop.py gate --summary"
        recs = [tool_use("1", "2026-01-01T00:00:01Z", "Bash", {"command": gate}, tid="g1"),
                result("2", "2026-01-01T00:00:02Z", "g1", 'Exit code 1\n{"exit": 1, "seconds": 7}')]
        self.assertEqual(metrics.numbers(items([self.main(recs)]), "a", [])["gate"], {"runs": 1, "seconds": 7})

    def test_gate_logs(self) -> None:
        logs = self.tmp / "logs"
        logs.mkdir()
        start = datetime(2026, 1, 1, 10).timestamp()
        for name, reports, seconds in [("gate-20260101T100000", True, 30.04), ("gate-20260101T100000-2", True, 12.5),
                                       ("gate-20260101T100000-3", False, 9), ("ci-wait-20260101T100000", True, 4),
                                       ("gate-2026", True, 3), ("gate-20260101T095959", True, 5),
                                       ("gate-20260101T103001", True, 5), ("gate-20261399T100000", True, 5),
                                       ("gate-20260101T100500", True, 250)]:
            log = logs / f"{name}.log"
            log.write_text("x\n")
            if reports:
                (logs / f"{name}-reports").mkdir()
            os.utime(log, (start + seconds, start + seconds))
        (logs / "gate-20260101T100100.log").symlink_to(logs / "missing")
        (logs / "gate-20260101T100100-reports").mkdir()
        window = (start, start + 1800)
        self.assertEqual(metrics.gate_logs(logs, *window), {"runs": 2, "seconds": 42.5})
        self.assertEqual(metrics.gate_logs(self.tmp / "none", *window), {"runs": 0, "seconds": 0.0})

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

    def test_denials_bracket_in_command_and_no_close(self) -> None:
        recs = [result("1", "2026-01-01T00:00:01Z", "t1", "PreToolUse:Bash hook error: [[ -x a ] && ~/h/deny-path.sh]: no",
                       is_error=True),
                result("2", "2026-01-01T00:00:02Z", "t2", "PreToolUse:Bash hook error: [x.sh\nmore y.sh", is_error=True)]
        self.assertEqual(metrics.numbers(items([self.main(recs)]), "a", [])["denials"], {"deny-path.sh": 1, "x.sh": 1})

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

    def test_effort_counts(self) -> None:
        bounds = [(metrics.parse_time("2026-01-01T05:00:00Z"), "5")]
        main = self.main([tool_use("1", "2026-01-01T00:00:01Z", "Agent", {"prompt": "x"}, tid="T1"),
                          tool_use("2", "2026-01-01T00:00:02Z", "Agent", {"prompt": "y"}, tid="T2"),
                          *[effort(f"h{i}", "2026-01-01T01:00:00Z", "high") for i in range(3)],
                          effort("m1", "2026-01-01T01:00:00Z", "medium"),
                          record("u1", "2026-01-01T01:00:00Z", kind="user", content="go"),
                          api("n1", "2026-01-01T06:00:00Z", {})])
        copy = metrics.Transcript(self.tmp / "y.jsonl", meta=None, records=list(main.records))
        typed_agent = metrics.Transcript(self.tmp / "agent-1.jsonl", meta={"toolUseId": "T1", "agentType": "implementer"},
                                         records=[effort(f"s{i}", "2026-01-01T06:00:00Z", "medium") for i in range(2)])
        bare = metrics.Transcript(self.tmp / "agent-2.jsonl", meta={"toolUseId": "T2"},
                                  records=[effort("b1", "2026-01-01T06:00:00Z", "low")])
        got = metrics.efforts(items([main, copy, typed_agent, bare]), "a", bounds)
        self.assertEqual(got, {"1-2": {"main": {"high": 3, "medium": 1}},
                               "5": {"main": {"?": 1}, "implementer": {"medium": 2}, "?": {"low": 1}}})
        self.assertEqual(metrics.efforts(items([main]), "a", None), {})

    def test_effort_bad_shapes(self) -> None:
        recs = [api("1", "2026-01-01T01:00:00Z", ["x"]), api("2", "2026-01-01T01:00:00Z", {"output_config": "high"}),
                effort("3", "2026-01-01T01:00:00Z", 3), effort("4", "2026-01-01T01:00:00Z", "turbo"),
                effort("5", "2026-01-01T01:00:00Z", "HIGH"), {"uuid": "6", "type": "api-request"}]
        got = metrics.efforts(items([self.main(recs)]), "a", [])
        self.assertEqual(got, {"1-2": {"main": {"?": 6}}})

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
        run("config", "diff.noprefix", "true")
        self.assertEqual(metrics.test_counts(repo, "refs/heads/main"), {"unit": 3, "acceptance": 2})


if __name__ == "__main__":
    unittest.main()

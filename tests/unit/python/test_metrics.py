#!/usr/bin/env python3
"""Tests for metrics.py: transcript read, feature attribution, token weights, stages, numbers and the row."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
import metrics  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for devloop.py seal, seal --rebase-continue and audit (the seal package)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from support import ROOT

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from test_devloop import DEVLOOP, git, put, run_cli  # noqa: E402  (also sets the core.fsmonitor override)
from seal import ACCEPTANCE_DIR  # noqa: E402
from seal import audit as audit_module  # noqa: E402
from seal.audit import taint_state  # noqa: E402
from seal.value import commit_entries, commit_patch_value, seal_value  # noqa: E402

SUITE = f"{ACCEPTANCE_DIR}/x-y/test_flow.py"


def lines_value(lines: list[str]) -> str:
    return hashlib.sha256("".join(f"{line}\n" for line in sorted(lines)).encode()).hexdigest()


class SealCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name).resolve() / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "commit.gpgsign", "false")
        put(self.repo / "README.md", "readme\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def stage(self, path: str, text: str) -> None:
        put(self.repo / path, text)
        git(self.repo, "add", "--", path)

    def seal(self, *args: str) -> tuple[int, dict]:
        return run_cli("seal", *args, cwd=self.repo)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()

    def body(self) -> str:
        return git(self.repo, "log", "-1", "--format=%B")

    def test_value_is_sha256_of_sorted_path_blob_lines(self) -> None:
        self.assertEqual(seal_value([("b", "2"), ("a", "1")]), hashlib.sha256(b"a 1\nb 2\n").hexdigest())

    def test_seal_commits_with_trailers(self) -> None:
        self.stage(SUITE, "def test_flow():\n    pass\n")
        blob = git(self.repo, "rev-parse", f":{SUITE}").strip()
        code, out = self.seal("--feature", "x-y", "--message", "Acceptance tests for x-y")
        self.assertEqual(code, 0, out)
        value = lines_value([f"{SUITE} {blob}"])
        self.assertEqual(out["commit"], self.head())
        self.assertEqual(out["seal"], value)
        self.assertEqual(out["files"], [SUITE])
        self.assertTrue(self.body().startswith("Acceptance tests for x-y\n\n"))
        self.assertIn("Acceptance: x-y\n", self.body())
        self.assertIn(f"Dev-Loop-Seal: {value}\n", self.body())

    def test_seal_refuses(self) -> None:
        cases = [
            ("a path outside", [(SUITE, "a\n"), ("README.md", "changed\n")], "x-y"),
            ("nothing staged", [], "x-y"),
            ("only a path outside", [("README.md", "changed\n")], "x-y"),
            ("a bad slug", [(SUITE, "a\n")], "Bad_Slug"),
        ]
        for name, staged, slug in cases:
            with self.subTest(name):
                before = self.head()
                for path, text in staged:
                    self.stage(path, text)
                code, out = self.seal("--feature", slug, "--message", "m")
                self.assertEqual(code, 1, out)
                self.assertTrue(out.get("error"), out)
                self.assertEqual(self.head(), before)
                git(self.repo, "reset", "-q", "--hard")
                git(self.repo, "clean", "-qfd")

    def test_reverts_writes_one_line_per_sha(self) -> None:
        first = self.head()
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "second")
        second = self.head()
        self.stage(SUITE, "a\n")
        code, out = self.seal("--feature", "x-y", "--message", "m", "--reverts", first, "--reverts", second)
        self.assertEqual(code, 0, out)
        self.assertIn(f"\n\nThis reverts commit {first}.\nThis reverts commit {second}.\nAcceptance: x-y\n", self.body())

    def test_deleted_file_entry_is_deleted(self) -> None:
        self.stage(SUITE, "a\n")
        self.assertEqual(self.seal("--feature", "x-y", "--message", "add")[0], 0)
        git(self.repo, "rm", "-q", "--", SUITE)
        code, out = self.seal("--feature", "x-y", "--message", "remove")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["seal"], lines_value([f"{SUITE} deleted"]))

    def test_path_with_space_and_unicode(self) -> None:
        path = f"{ACCEPTANCE_DIR}/x-y/café flow.py"
        self.stage(path, "a\n")
        blob = git(self.repo, "rev-parse", f":{path}").strip()
        code, out = self.seal("--feature", "x-y", "--message", "m")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["files"], [path])
        self.assertEqual(out["seal"], lines_value([f"{path} {blob}"]))

    def conflicting_rebase(self, message: str = "seal", sealed: bool = True) -> None:
        """A feature commit of SUITE, `main` changes SUITE too: `git rebase main` stops on it."""
        self.stage(SUITE, "base\n")
        git(self.repo, "commit", "-q", "-m", "suite")
        git(self.repo, "checkout", "-q", "-b", "feat/x-y")
        self.stage(SUITE, "feature\n")
        if sealed:
            self.assertEqual(self.seal("--feature", "x-y", "--message", message)[0], 0)
        else:
            git(self.repo, "commit", "-q", "-m", message)
        git(self.repo, "checkout", "-q", "main")
        self.stage(SUITE, "upstream\n")
        git(self.repo, "commit", "-q", "-m", "upstream")
        git(self.repo, "checkout", "-q", "feat/x-y")
        with self.assertRaises(Exception):
            git(self.repo, "rebase", "main")

    def test_rebase_continue_rewrites_the_trailer(self) -> None:
        """D7: only the trailers of the last paragraph change; D1: the patch trailer is written too."""
        quoted = "Dev-Loop-Seal: quoted in the body"
        self.conflicting_rebase(f"seal\n\n{quoted}")
        self.stage(SUITE, "resolved\n")
        code, out = self.seal("--rebase-continue")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rebase"], "continued")
        entries, outside = commit_entries(self.repo, self.head())
        self.assertEqual(outside, [])
        self.assertEqual(out["seal"], seal_value(entries))
        self.assertTrue(self.body().rstrip("\n").endswith(
            f"\n\nAcceptance: x-y\nDev-Loop-Seal: {seal_value(entries)}\n"
            f"Dev-Loop-Seal-Patch: {commit_patch_value(self.repo, self.head())}"), self.body())
        self.assertIn(f"\n\n{quoted}\n\n", self.body())
        self.assertEqual(self.body().count("Dev-Loop-Seal:"), 2)
        self.assertEqual(run_cli("seal-check", cwd=self.repo)[0], 0)

    def test_rebase_continue_refuses(self) -> None:
        """D7: a stopped commit that is no seal commit, or nothing staged under the folder."""
        cases = [
            ("not a seal commit", False, "plain\n\nDev-Loop-Seal: quoted in the body\n\nmore", "resolved\n",
             "the stopped commit is not a seal commit"),
            ("nothing staged", True, "seal", "upstream\n", None),
        ]
        for name, sealed, message, resolved, error in cases:
            with self.subTest(name):
                self.conflicting_rebase(message, sealed)
                self.stage(SUITE, resolved)
                code, out = self.seal("--rebase-continue")
                self.assertEqual(code, 1, out)
                self.assertTrue(out.get("error"), out)
                if error:
                    self.assertEqual(out["error"], error)
                self.assertTrue((Path(git(self.repo, "rev-parse", "--absolute-git-dir").strip()) / "rebase-merge").is_dir())
                git(self.repo, "rebase", "--abort")
                git(self.repo, "checkout", "-q", "main")
                git(self.repo, "reset", "-q", "--hard", "HEAD~2")
                git(self.repo, "branch", "-q", "-D", "feat/x-y")

    def test_rebase_continue_without_rebase(self) -> None:
        code, out = self.seal("--rebase-continue")
        self.assertEqual(code, 1, out)
        self.assertTrue(out.get("error"), out)


# Three distinctive lines of a protected file (30 characters or more each).
L1 = "assert compute_total([3, 4, 5]) == 12  # held-out case one"
L2 = "assert compute_total([10, 20]) == 30  # held-out case two"
L3 = "assert compute_total([]) == 0  # held-out case three, empty"
PROTECTED = f"{ACCEPTANCE_DIR}/x/test_total.py"


def claude_line(text: str, call_id: str = "t") -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": [{"type": "text", "text": text}]}]}}


def claude_call(name: str, tool_input: dict, call_id: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]}}


def monitor_note(call_id: str, *events: str) -> dict:
    body = "".join(f"<event>{event}</event>\n" for event in events)
    return {"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "task-notification",
            "prompt": f"<task-notification>\n<task-id>T</task-id>\n<tool-use-id>{call_id}</tool-use-id>\n{body}</task-notification>"}}


def codex_mcp(text: str) -> dict:
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "McpToolCall", "result": {"content": [{"type": "text", "text": text}]}}}}


def codex_custom(output: object) -> dict:
    return {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "x", "output": output}}


def codex_line(text: str, command: str = "cat") -> dict:
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "CommandExecution", "command": command, "aggregated_output": text}}}


def bash_result(command: str, text: str, call_id: str = "c1") -> list[dict]:
    return [claude_call("Bash", {"command": command}, call_id), claude_line(text, call_id)]


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        put(self.repo / "README.md", "readme\n")
        put(self.repo / PROTECTED, f"def test_total():\n    {L1}\n    {L2}\n    {L3}\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "feat/x")
        self.env = {k: v for k, v in os.environ.items() if k != "DEV_LOOP_NOTES"}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()

    def commit(self, path: str, text: str, message: str) -> str:
        put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return self.head()

    def test_patch_ids_run_no_textconv_program(self) -> None:
        marker = self.tmp / "ran"
        conv = self.tmp / "conv.sh"
        put(conv, f"#!/bin/sh\necho x >> {marker}\ncat \"$1\"\n")
        conv.chmod(0o755)
        git(self.repo, "config", "diff.probe.textconv", str(conv))
        put(self.repo / ".gitattributes", "* diff=probe\n")
        sha = self.commit("README.md", "changed\n", "change")
        self.assertTrue(audit_module.patch_ids(self.repo, "diff", f"{sha}^", sha))
        self.assertTrue(audit_module.patch_ids(self.repo, "log", "-p", "--format=commit %H", "HEAD"))
        self.assertFalse(marker.exists())

    def mark_start(self, agent_id: str) -> None:
        put(Path(git(self.repo, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop-subagents" / f"{agent_id}.head",
            self.head() + "\n")

    def transcript(self, lines: list, name: str = "t.jsonl") -> Path:
        path = self.tmp / name
        path.write_text("".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines))
        return path

    def audit(self, transcript: Path, agent_type: str = "implementer", agent_id: str = "a1") -> tuple[int, dict]:
        hook = {"hook_event_name": "SubagentStop", "session_id": "s", "cwd": str(self.repo), "agent_id": agent_id,
                "agent_type": agent_type, "agent_transcript_path": str(transcript)}
        result = subprocess.run([sys.executable, str(DEVLOOP), "audit"], cwd=self.repo, env=self.env,
                                input=json.dumps(hook), capture_output=True, text=True, timeout=60)
        return result.returncode, json.loads(result.stdout)

    def taints(self, rows: list[tuple[list, tuple[list, list] | None]]) -> None:
        for n, (lines, expected) in enumerate(rows):
            with self.subTest(n=n):
                self.mark_start(f"r{n}")
                self.commit(f"src/r{n}.py", f"x = {n}\n", f"r{n}")
                code, out = self.audit(self.transcript(lines, f"r{n}.jsonl"), agent_id=f"r{n}")
                self.assertEqual(code, 0, out)
                if expected is None:
                    self.assertEqual(out, {"taint": None, "skipped": []})
                else:
                    self.assertEqual((out["taint"]["paths"], out["taint"]["lines"]), expected)
        if any(expected is not None for _, expected in rows):
            self.assertFalse(run_cli("seal-check", cwd=self.repo)[1]["ok"])

    def test_lsp_and_mcp_results_taint(self) -> None:
        refs = f"Found 2 references across 2 files:\n\n{PROTECTED}:\n  Line 3:5\nsrc/app.py:\n  Line 1:5"
        self.taints([
            ([claude_call("LSP", {"operation": "findReferences", "filePath": "src/app.py", "line": 1, "character": 5}, "l1"),
              claude_line(refs, "l1")], ([PROTECTED], [])),
            ([claude_call("mcp__fs__search", {"pattern": "make"}, "m1"), claude_line(PROTECTED, "m1")], ([PROTECTED], [])),
            ([claude_call("ReadMcpResourceTool", {"server": "fs", "uri": "file:///x"}, "r1"), claude_line(f"{L1}\n{L2}\n", "r1")],
             ([], [L1, L2]))])

    def test_monitor_events_taint_by_their_call(self) -> None:
        hit = f"./{PROTECTED}:3:r = make(1)"
        self.taints([
            ([claude_call("Monitor", {"command": "grep -rn make .", "description": "d"}, "U"), claude_line("Monitor started", "U"),
              monitor_note("U", hit)], ([PROTECTED], [])),
            ([claude_call("Monitor", {"command": "grep -rn make ."}, "U2"), monitor_note("U2", "build ok", f"{PROTECTED}:3:x")],
             ([PROTECTED], [])),
            ([claude_call("Monitor", {"command": f"cat {PROTECTED}"}, "V"), monitor_note("V", f"{L1}\n{L2}")], ([], [L1, L2])),
            ([claude_call("Bash", {"command": "grep -rn make .", "run_in_background": True}, "W"), claude_line("started", "W"),
              monitor_note("W", hit, f"{L1}\n{L2}")], None),
            ([claude_line("x", "Z"), monitor_note("Z", f"{L1}\n{L2}")], None)])

    def test_monitor_events_are_unescaped_before_matching(self) -> None:
        shell = "cd build && make all && make install  # held-out shell line"
        arrow = "assert compute(a < b) -> b > a  # held-out arrow line"
        self.commit(f"{ACCEPTANCE_DIR}/x/test_ops.py", f"def test_ops():\n    {shell}\n    {arrow}\n", "ops")
        escaped = "\n".join(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for line in (shell, arrow))
        self.taints([([claude_call("Monitor", {"command": "tail -f build.log"}, "E"), monitor_note("E", escaped)], ([], [shell, arrow]))])

    def test_codex_mcp_and_code_mode_results(self) -> None:
        self.taints([
            ([codex_mcp(f"{PROTECTED}:3:x")], ([PROTECTED], [])),
            ([codex_custom([{"type": "input_text", "text": f"{L1}\n{L2}"}])], ([], [L1, L2])),
            ([codex_custom(f"{L1}\n{L2}")], ([], [L1, L2])),
            ([codex_custom([{"type": "input_text", "text": f"{PROTECTED}:3:x"}])], None)])

    def test_tool_use_alone_does_not_taint(self) -> None:
        self.taints([([
            claude_call("LSP", {"operation": "findReferences", "filePath": "src/app.py", "line": 1, "character": 5}, "a"),
            claude_line("No references found", "a"),
            claude_call("Monitor", {"command": "tail -f build.log"}, "b"), monitor_note("b", "build ok"),
            claude_call("mcp__fs__read_file", {"path": "README.md"}, "c"), claude_line("readme", "c")], None)])
        self.assertTrue(run_cli("seal-check", cwd=self.repo)[1]["ok"])

    def test_claude_two_lines_with_commit_taints(self) -> None:
        self.mark_start("a1")
        sha = self.commit("src/total.py", "def compute_total(xs):\n    return sum(xs)\n", "add total")
        transcript = self.transcript([
            claude_line(f"[feat/x {sha[:7]}] add total\n 1 file changed"),
            claude_line(f"     1\tdef test_total():\n     2\t    {L1}\n     3\t    {L2}\n"),
        ])
        code, out = self.audit(transcript)
        self.assertEqual(code, 0, out)
        record = out["taint"]
        self.assertEqual(record["agent_id"], "a1")
        self.assertEqual(record["agent_type"], "implementer")
        self.assertEqual(record["set"], "acceptance")
        self.assertEqual(record["lines"], [L1, L2])
        self.assertEqual(record["paths"], [])
        self.assertEqual(record["end"], sha)
        self.assertEqual(record["start"], git(self.repo, "rev-parse", "HEAD~1").strip())
        self.assertEqual(record["attribution"], "transcript")
        self.assertEqual([c["sha"] for c in record["commits"]], [sha])
        self.assertTrue(record["commits"][0]["patch_id"])
        self.assertNotEqual(record["commits"][0]["patch_id"], record["commits"][0]["inverse_patch_id"])
        self.assertRegex(record["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(taint_state(self.repo)["open"], [record])

    def test_one_line_or_shared_line_does_not_taint(self) -> None:
        cases = [
            ("one line", [claude_line(f"{L1}\n")], None),
            ("two lines also in README.md", [claude_line(f"{L1}\n{L2}\n")], f"{L1}\n{L2}\n"),
            ("two lines in the prompt only", [{"type": "user", "message": {"role": "user", "content": f"{L1}\n{L2}"}},
                                              claude_line("ok")], None),
        ]
        for name, lines, readme in cases:
            with self.subTest(name):
                put(self.repo / "README.md", readme or "readme\n")
                code, out = self.audit(self.transcript(lines))
                self.assertEqual(code, 0, out)
                self.assertIsNone(out["taint"], out)
        state = taint_state(self.repo)
        self.assertEqual(state["open"] + state["closed"], [])

    def test_spec_plan_glossary_lines_excluded(self) -> None:
        notes = self.tmp / "notes"
        self.env["DEV_LOOP_NOTES"] = str(notes)
        shared = f"Quoted: {L1}\nand {L2}\n"
        cases = [
            ("spec", notes / "specs" / "20261024 X.md", "---\nfeature: x\n---\n\n# X\n" + shared),
            ("plan", notes / "plans" / "20261024 X.md", "---\nspec: 20261024 X\n---\n\n# Plan\n" + shared),
            ("glossary", self.repo / "GLOSSARY.md", "# Glossary\n" + shared),
        ]
        put(notes / "specs" / "20261024 X.md", "---\nfeature: x\n---\n\n# X\n")
        for name, path, text in cases:
            with self.subTest(name):
                before = path.read_text() if path.exists() else None
                put(path, text)
                code, out = self.audit(self.transcript([claude_line(f"{L1}\n{L2}\n")]))
                self.assertEqual(code, 0, out)
                self.assertIsNone(out["taint"], out)
                if before is None:
                    path.unlink()
                else:
                    put(path, before)

    def test_revert_closes_and_rebase_keeps_open(self) -> None:
        self.mark_start("a1")
        sha = self.commit("src/total.py", "def compute_total(xs):\n    return sum(xs)\n", "add total")
        self.audit(self.transcript([claude_line(f"[feat/x {sha[:7]}] add total\n{L1}\n{L2}\n")]))
        self.assertEqual(len(taint_state(self.repo)["open"]), 1)
        git(self.repo, "reset", "-q", "--soft", "HEAD~1")
        git(self.repo, "commit", "-q", "-m", "add total again")
        self.assertNotEqual(self.head(), sha)
        self.assertEqual(len(taint_state(self.repo)["open"]), 1, "re-commit after reset --soft")
        git(self.repo, "checkout", "-q", "main")
        self.commit("other.txt", "upstream\n", "upstream")
        git(self.repo, "checkout", "-q", "feat/x")
        before = self.head()
        git(self.repo, "rebase", "-q", "main")
        self.assertNotEqual(self.head(), before)
        self.assertEqual(len(taint_state(self.repo)["open"]), 1, "rebase")
        git(self.repo, "revert", "--no-edit", "HEAD")
        state = taint_state(self.repo)
        self.assertEqual(state["open"], [])
        self.assertEqual(len(state["closed"]), 1)

    def test_codex_spec_tester_source_taint(self) -> None:
        put(self.repo / "src/y.py", "def compute_total(xs):\n    return sum(x for x in xs if x is not None)\n"
            "    # running total of every basket line item seen\n")
        code, out = self.audit(self.transcript([
            {"type": "session_meta", "payload": {"id": "r"}},
            codex_line("    return sum(x for x in xs if x is not None)\n    # running total of every basket line item seen\n"),
        ]), agent_type="spec_tester")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["taint"]["set"], "source")
        self.assertEqual(out["taint"]["agent_type"], "spec_tester")

    def test_rollout_without_items_is_skipped(self) -> None:
        code, out = self.audit(self.transcript([
            {"type": "session_meta", "payload": {"id": "r"}},
            {"type": "response_item", "payload": {"type": "message", "content": [{"type": "output_text", "text": L1 + L2}]}},
        ]), agent_type="implementer")
        self.assertEqual(code, 0, out)
        self.assertIsNone(out["taint"])
        self.assertEqual(len(out["skipped"]), 1)
        self.assertTrue(out["skipped"][0]["reason"])
        self.assertEqual(taint_state(self.repo)["skipped"], out["skipped"])

    def test_no_commit_record_is_closed(self) -> None:
        self.mark_start("a1")
        code, out = self.audit(self.transcript([claude_line(f"{L1}\n{L2}\n{L3}\n")]))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["taint"]["commits"], [])
        self.assertEqual(len(out["taint"]["lines"]), 3)
        state = taint_state(self.repo)
        self.assertEqual((state["open"], state["closed"]), ([], [out["taint"]]))

    def test_spec_tester_revert_then_seal_closes(self) -> None:
        put(self.repo / "src/y.py", f"# {L1}\n# {L2}\n")
        self.commit("src/y.py", f"# {L1}\n# {L2}\n", "source")
        self.mark_start("s1")
        put(self.repo / f"{ACCEPTANCE_DIR}/x/test_new.py", "def test_new():\n    assert True\n")
        git(self.repo, "add", "-A")
        code, sealed = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)
        self.assertEqual(code, 0, sealed)
        transcript = self.transcript([claude_line(f"# {L1}\n# {L2}\n"), claude_line(json.dumps(sealed, indent=2))])
        code, out = self.audit(transcript, agent_type="spec-tester", agent_id="s1")
        self.assertEqual([c["sha"] for c in out["taint"]["commits"]], [sealed["commit"]])
        self.assertEqual(len(taint_state(self.repo)["open"]), 1)
        git(self.repo, "revert", "--no-commit", sealed["commit"])
        code, out = run_cli("seal", "--feature", "x", "--message", "revert", "--reverts", sealed["commit"], cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(taint_state(self.repo)["open"], [])

    def test_no_head_file_starts_at_base(self) -> None:
        """D2: no `<agent_id>.head`: start is `<base>`, the transcript's commit is listed, the record is open."""
        sha = self.commit("src/total.py", "def compute_total(xs):\n    return sum(xs)\n", "add total")
        code, out = self.audit(self.transcript([claude_line(f"[feat/x {sha[:7]}] add total\n{L1}\n{L2}\n")]))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["taint"]["start"], git(self.repo, "merge-base", "HEAD", "main").strip())
        self.assertEqual(([c["sha"] for c in out["taint"]["commits"]], out["taint"]["attribution"]), ([sha], "transcript"))
        self.assertEqual(len(taint_state(self.repo)["open"]), 1)

    def test_re_stop_replaces_the_agent_entry(self) -> None:
        """D8: a second audit of the same agent replaces its record, and its skip, instead of appending."""
        self.mark_start("a1")
        tainted = self.transcript([claude_line(f"{L1}\n{L2}\n")])
        missing = self.tmp / "missing.jsonl"
        for transcript in (tainted, tainted, missing, missing):
            self.assertEqual(self.audit(transcript)[0], 0)
        self.audit(tainted, agent_id="a2")
        data = json.loads(audit_module.taint_file(self.repo).read_text())
        self.assertEqual([r["agent_id"] for r in data["records"]], ["a1", "a2"])
        self.assertEqual([s["agent_id"] for s in data["skipped"]], ["a1"])

    def test_hook_input_not_json_writes_the_skip(self) -> None:
        """A skip for input that is no JSON object is written too, to the repo of the process cwd."""
        result = subprocess.run([sys.executable, str(DEVLOOP), "audit"], cwd=self.repo, env=self.env,
                                input="{not json", capture_output=True, text=True, timeout=60)
        out = json.loads(result.stdout)
        self.assertEqual((result.returncode, out["taint"], out["skipped"][0]["reason"]), (0, None, "error"))
        self.assertEqual(taint_state(self.repo)["skipped"], out["skipped"])

    def test_other_agents_commit_not_listed(self) -> None:
        self.mark_start("a1")
        self.mark_start("a2")
        a = self.commit("src/a.py", "a = 1\n", "A")
        b = self.commit("src/b.py", "b = 2\n", "B")
        code, out = self.audit(self.transcript([claude_line(f"[feat/x {a[:7]}] A\n{L1}\n{L2}\n")]), agent_id="a1")
        self.assertEqual(([c["sha"] for c in out["taint"]["commits"]], out["taint"]["attribution"]), ([a], "transcript"))
        code, out = self.audit(self.transcript([claude_line(f"{L1}\n{L2}\n")]), agent_id="a2")
        self.assertEqual(([c["sha"] for c in out["taint"]["commits"]], out["taint"]["attribution"]), ([a, b], "range"))

    def test_broken_transcript_line_is_skipped(self) -> None:
        cases = [
            ("broken line between two good ones", [claude_line(f"{L1}\n"), "{not json", claude_line(f"{L2}\n")], True),
            ("empty file", [], False),
            ("missing file", None, False),
        ]
        for name, lines, tainted in cases:
            with self.subTest(name):
                transcript = self.tmp / "missing.jsonl" if lines is None else self.transcript(lines)
                code, out = self.audit(transcript)
                self.assertEqual(code, 0, out)
                self.assertEqual(out["taint"] is not None, tainted, out)
                self.assertEqual(len(out["skipped"]), 0 if tainted else 1, out)

    def test_binary_and_large_files_ignored(self) -> None:
        put(self.repo / PROTECTED, "def test_total():\n    pass\n")
        (self.repo / f"{ACCEPTANCE_DIR}/x/data.bin").write_bytes(b"\0\n" + f"{L1}\n{L2}\n".encode())
        put(self.repo / f"{ACCEPTANCE_DIR}/x/big.txt", f"{L1}\n{L2}\n" + "#" * (1024 * 1024))
        code, out = self.audit(self.transcript([claude_line(f"{L1}\n{L2}\n")]))
        self.assertEqual(code, 0, out)
        self.assertIsNone(out["taint"], out)

    def test_no_base_counts_full_history(self) -> None:
        git(self.repo, "branch", "-m", "main", "master")  # no main, no origin/main: no base
        self.mark_start("a1")
        sha = self.commit("src/total.py", "def compute_total(xs):\n    return sum(xs)\n", "add total")
        code, out = self.audit(self.transcript([claude_line(f"[feat/x {sha[:7]}] add total\n{L1}\n{L2}\n")]))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(taint_state(self.repo)["open"]), 1)

    def test_other_files_read_only_with_two_output_lines(self) -> None:
        put(self.repo / "notes.txt", "unrelated\n")
        for output, reads_other in ((f"{L1}\n", False), (f"{L1}\n{L2}\n", True)):
            with self.subTest(reads_other=reads_other):
                hook = {"cwd": str(self.repo), "agent_id": "a1", "agent_type": "implementer",
                        "agent_transcript_path": str(self.transcript([claude_line(output)]))}
                with mock.patch.object(audit_module, "read_text", wraps=audit_module.read_text) as read:
                    audit_module.audit(hook)
                read_paths = {Path(call.args[0]).name for call in read.call_args_list}
                self.assertEqual("notes.txt" in read_paths, reads_other, read_paths)

    def test_search_output_path_taints(self) -> None:
        grep, short = "grep -rn make .", PROTECTED[len("tests/"):]
        rows = [bash_result(grep, f"./{PROTECTED}:3:r = make(1)"), bash_result("rg -n make", f"{PROTECTED}:3:r = make(1)"),
                bash_result("find . -name '*.py'", f"./{PROTECTED}"),
                [claude_call("Glob", {"pattern": "**/*.py"}, "g1"), claude_line(f"{self.repo}/{PROTECTED}", "g1")],
                bash_result(grep, f"{short}:3:x"), bash_result(grep, f"./{short}"), bash_result(grep, f"{PROTECTED}-4-  y"),
                [codex_line(f"{PROTECTED}:3:r = make(1)", "rg -n make")]]
        shas = []
        for n, lines in enumerate(rows):
            with self.subTest(lines=lines):
                self.mark_start(f"p{n}")
                shas.append(self.commit(f"src/p{n}.py", f"p = {n}\n", f"p{n}"))
                code, out = self.audit(self.transcript(lines), agent_id=f"p{n}")
                record = out["taint"] or {}
                self.assertEqual((record.get("set"), record.get("paths"), record.get("lines")),
                                 ("acceptance", [PROTECTED], []), out)
                self.assertEqual([c["sha"] for c in record["commits"]], [shas[-1]])
        code, out = run_cli("seal-check", cwd=self.repo)
        self.assertEqual((out["ok"], [r["agent_id"] for r in out["taint"]]), (False, [f"p{n}" for n in range(len(rows))]))
        for commit in reversed(shas):
            git(self.repo, "revert", "--no-edit", commit)
        code, out = run_cli("seal-check", cwd=self.repo)
        self.assertEqual((out["ok"], out["taint"]), (True, []), out)

    def test_lines_that_do_not_start_with_a_suite_path_do_not_taint(self) -> None:
        held = PROTECTED
        self.mark_start("a1")
        self.commit("src/a.py", "a = 1\n", "A")
        texts = [f" {held} | 3 +++", f"diff --git a/{held} b/{held}", f"- unsealed abc1234 {held}", f'  "path": "{held}"',
                 f"see {held}", f"{ACCEPTANCE_DIR}/gone.py:1:x", "src/acceptance/x/test_total.py:1:x", f"{held}c:1:x"]
        rows = [bash_result("grep -rn make .", text) for text in texts] + [
            bash_result("git diff --name-only main...HEAD", held), bash_result("scripts/gate.sh", f"{held}:3:1: E501 line too long"),
            [codex_line(held, "git diff --name-only main...HEAD")]]
        for lines in rows:
            with self.subTest(lines=lines):
                code, out = self.audit(self.transcript(lines))
                self.assertIsNone(out["taint"], out)
        code, out = run_cli("seal-check", cwd=self.repo)
        self.assertEqual((out["ok"], out["taint"]), (True, []), out)

    def test_filter_output_does_not_taint(self) -> None:
        held = PROTECTED
        self.mark_start("a1")
        self.commit("src/a.py", "a = 1\n", "A")
        rows = [bash_result("git diff --name-only main...HEAD | grep -v md", held),
                bash_result("git ls-files | grep tests", held),
                bash_result("git ls-files | rg -v md | egrep tests", held),
                [codex_line(held, "git diff --name-only main...HEAD | grep -v md")],
                [codex_line(held, "bash -lc 'git ls-files | grep tests'")]]
        for lines in rows:
            with self.subTest(lines=lines):
                code, out = self.audit(self.transcript(lines))
                self.assertIsNone(out["taint"], out)

    def test_search_that_reads_files_taints(self) -> None:
        rows = ["grep -rn make .", "rg -l make", "git grep -n make", "find . -name '*.py'", "git ls-files | xargs grep make",
                "git ls-files | grep -r make", "git ls-files | grep make .", "git ls-files | grep make 'unclosed",
                "bash -c 'grep -rn make .'", "grep -rn make . | grep -v md"]
        for n, command in enumerate(rows):
            with self.subTest(command=command):
                self.mark_start(f"f{n}")
                self.commit(f"src/f{n}.py", f"f = {n}\n", f"f{n}")
                code, out = self.audit(self.transcript(bash_result(command, f"{PROTECTED}:3:x")), agent_id=f"f{n}")
                self.assertEqual((out["taint"] or {}).get("paths"), [PROTECTED], out)

    def test_path_rule_scope(self) -> None:
        self.mark_start("a1")
        code, out = self.audit(self.transcript(bash_result("grep -rn make .", f"{PROTECTED}:3:x")))
        self.assertEqual((out["taint"]["paths"], out["taint"]["commits"]), ([PROTECTED], []))
        code, check = run_cli("seal-check", cwd=self.repo)
        self.assertEqual((check["ok"], check["taint"]), (True, []))
        self.assertIn(out["taint"], check["warnings"])
        self.commit("src/app.py", "x = 1\n", "app")
        code, out = self.audit(self.transcript(bash_result("grep -rn make .", "src/app.py:3:x")), agent_type="spec-tester",
                               agent_id="s1")
        self.assertIsNone(out["taint"], out)
        self.mark_start("a2")
        self.commit("src/b.py", "b = 1\n", "B")
        code, out = self.audit(self.transcript([claude_line(f"{L1}\n{L2}\n")]), agent_id="a2")
        path = audit_module.taint_file(self.repo)
        data = json.loads(path.read_text())
        for record in data["records"]:
            record.pop("paths", None)
        path.write_text(json.dumps(data))
        code, check = run_cli("seal-check", cwd=self.repo)
        self.assertEqual([r["agent_id"] for r in check["taint"]], ["a2"], check)


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


if __name__ == "__main__":
    unittest.main()

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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
from test_devloop import DEVLOOP, git, put, run_cli  # noqa: E402  (also sets the core.fsmonitor override)
from seal import ACCEPTANCE_DIR  # noqa: E402
from seal import audit as audit_module  # noqa: E402
from seal.audit import taint_state  # noqa: E402
from seal.value import commit_entries, seal_value  # noqa: E402

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

    def test_rebase_continue_rewrites_the_trailer(self) -> None:
        self.stage(SUITE, "base\n")
        git(self.repo, "commit", "-q", "-m", "suite")
        git(self.repo, "checkout", "-q", "-b", "feat/x-y")
        self.stage(SUITE, "feature\n")
        self.assertEqual(self.seal("--feature", "x-y", "--message", "seal")[0], 0)
        git(self.repo, "checkout", "-q", "main")
        self.stage(SUITE, "upstream\n")
        git(self.repo, "commit", "-q", "-m", "upstream")
        git(self.repo, "checkout", "-q", "feat/x-y")
        with self.assertRaises(Exception):
            git(self.repo, "rebase", "main")
        self.stage(SUITE, "resolved\n")
        code, out = self.seal("--rebase-continue")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rebase"], "continued")
        entries, outside = commit_entries(self.repo, self.head())
        self.assertEqual(outside, [])
        self.assertEqual(out["seal"], seal_value(entries))
        self.assertIn(f"Dev-Loop-Seal: {seal_value(entries)}\n", self.body())
        self.assertEqual(self.body().count("Dev-Loop-Seal:"), 1)
        self.assertIn("Acceptance: x-y\n", self.body())

    def test_rebase_continue_without_rebase(self) -> None:
        code, out = self.seal("--rebase-continue")
        self.assertEqual(code, 1, out)
        self.assertTrue(out.get("error"), out)


# Three distinctive lines of a protected file (30 characters or more each).
L1 = "assert compute_total([3, 4, 5]) == 12  # held-out case one"
L2 = "assert compute_total([10, 20]) == 30  # held-out case two"
L3 = "assert compute_total([]) == 0  # held-out case three, empty"
PROTECTED = f"{ACCEPTANCE_DIR}/x/test_total.py"


def claude_line(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": [{"type": "text", "text": text}]}]}}


def codex_line(text: str) -> dict:
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "CommandExecution", "command": "cat", "aggregated_output": text}}}


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



class SealRepo(unittest.TestCase):
    """A repo whose `main` holds a README, checked out on `feat/x`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name).resolve() / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        put(self.repo / "README.md", "readme\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "feat/x")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def commit(self, path: str, text: str, message: str = "change") -> str:
        put(self.repo / path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD").strip()

    def seal(self, path: str, text: str) -> str:
        put(self.repo / path, text)
        git(self.repo, "add", "--", path)
        code, out = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)
        self.assertEqual(code, 0, out)
        return out["commit"]

    def check(self, *args: str) -> tuple[int, dict]:
        return run_cli("seal-check", *args, cwd=self.repo)

    def violations(self, *args: str) -> list[tuple]:
        return [(v["reason"], v["commit"], v["path"]) for v in self.check(*args)[1]["violations"]]


class SealCheckTests(SealRepo):
    def test_seal_commit_is_ok(self) -> None:
        self.seal(SUITE, "def test_flow():\n    assert True\n")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))

    def test_unsealed_then_revert_is_ok(self) -> None:
        sha = self.commit(SUITE, "def test_flow():\n    pass\n")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertFalse(out["ok"])
        self.assertEqual(self.violations(), [("unsealed", None, SUITE)])
        git(self.repo, "revert", "--no-edit", sha)
        self.assertEqual(self.check()[0], 0)

    def test_amended_seal_is_bad_and_later_change_is_after_seal(self) -> None:
        self.seal(SUITE, "a\n")
        put(self.repo / SUITE, "b\n")
        git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
        amended = git(self.repo, "rev-parse", "HEAD").strip()
        self.assertIn(("bad-seal", amended, None), self.violations())
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        self.seal(SUITE, "a\n")
        self.commit(SUITE, "b\n")
        self.assertEqual(self.violations(), [("changed-after-seal", None, SUITE)])

    def test_seal_with_a_path_outside_is_bad(self) -> None:
        self.seal(SUITE, "a\n")
        put(self.repo / "README.md", "changed\n")
        git(self.repo, "commit", "-q", "-a", "--amend", "--no-edit")
        self.assertIn(("bad-seal", git(self.repo, "rev-parse", "HEAD").strip(), None), self.violations())

    def test_rebase_onto_main_with_another_sealed_file_is_ok(self) -> None:
        self.seal(SUITE, "a\n")
        git(self.repo, "checkout", "-q", "main")
        self.seal(f"{ACCEPTANCE_DIR}/other/test_other.py", "o\n")
        git(self.repo, "checkout", "-q", "feat/x")
        git(self.repo, "rebase", "-q", "main")
        self.assertEqual(self.check()[0], 0)

    def test_clean_rebase_that_merges_upstream_into_the_sealed_file_is_ok(self) -> None:
        """D1: the replayed seal commit gets a new blob, its patch value still matches."""
        lines = [f"line {n}\n" for n in range(20)]
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, "".join(lines))
        git(self.repo, "checkout", "-q", "-B", "feat/x", "main")
        self.seal(SUITE, "".join(["changed by the feature\n"] + lines[1:]))
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, "".join(lines[:-1] + ["changed upstream\n"]))
        git(self.repo, "checkout", "-q", "feat/x")
        git(self.repo, "rebase", "-q", "main")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))

    def test_uncommitted_only_with_the_flag(self) -> None:
        self.seal(SUITE, "a\n")
        put(self.repo / f"{ACCEPTANCE_DIR}/x/test_new.py", "n\n")
        put(self.repo / SUITE, "changed\n")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))
        self.assertEqual(sorted(self.violations("--uncommitted")), [
            ("uncommitted", None, SUITE), ("uncommitted", None, f"{ACCEPTANCE_DIR}/x/test_new.py")])

    def test_no_base_is_ok(self) -> None:
        git(self.repo, "branch", "-m", "main", "trunk")  # no main, no origin/main: no base
        self.commit(SUITE, "unsealed\n")
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": []}))

    def test_open_taint_makes_ok_false(self) -> None:
        start = git(self.repo, "rev-parse", "HEAD").strip()
        old = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "outside the branch history").strip()
        sha = self.commit("src/total.py", "def total(xs):\n    return sum(xs)\n")
        record = {"agent_id": "a1", "agent_type": "implementer", "at": "t", "set": "acceptance", "lines": [],
                  "start": start, "end": sha, "attribution": "transcript"}
        taint = {"records": [{**record, "commits": [audit_module.commit_record(self.repo, sha)]},
                             {**record, "agent_id": "closed", "commits": []},
                             {**record, "agent_id": "old", "end": old, "commits": []}],
                 "skipped": [{"reason": "no-transcript", "end": sha}, {"reason": "error", "end": old}]}
        path = audit_module.taint_file(self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(taint))
        code, out = self.check()
        self.assertEqual((code, out["ok"], out["violations"]), (1, False, []))
        self.assertEqual([r["agent_id"] for r in out["taint"]], ["a1"])
        self.assertEqual(out["warnings"], [taint["records"][1], taint["skipped"][0]])
        for text in ("{not json", "[]"):
            with self.subTest(taint=text):
                path.write_text(text)
                code, out = self.check()
                self.assertEqual((code, out["ok"]), (1, False), out)
                self.assertEqual(self.violations(), [("taint-unreadable", None, str(path))])

    def test_closed_record_at_base_is_a_warning(self) -> None:
        head = git(self.repo, "rev-parse", "HEAD").strip()
        record = {"agent_id": "a1", "agent_type": "implementer", "at": "t", "set": "acceptance", "lines": [],
                  "start": head, "end": head, "commits": [], "attribution": "transcript"}
        path = audit_module.taint_file(self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"records": [record], "skipped": []}))
        self.assertEqual(self.check(), (0, {"ok": True, "violations": [], "taint": [], "warnings": [record]}))

    def test_quoted_trailer_in_an_earlier_paragraph_is_no_seal(self) -> None:
        put(self.repo / "docs.md", "docs\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "docs\n\nDev-Loop-Seal: abc\n\nCo-Authored-By: t <t@example.invalid>")
        self.assertEqual(self.check()[0], 0)

    def test_unicode_path(self) -> None:
        path = f"{ACCEPTANCE_DIR}/x/café flow.py"
        self.commit(path, "a\n")
        self.assertEqual(self.violations(), [("unsealed", None, path)])
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        self.seal(path, "a\n")
        self.assertEqual(self.check()[0], 0)
        put(self.repo / path, "b\n")
        self.assertEqual(self.violations("--uncommitted"), [("uncommitted", None, path)])


PASSING_GATE = "#!/bin/sh\necho all good\n"


class GateSealRepo(SealRepo):
    def gate(self) -> tuple[int, dict]:
        put(self.repo / "scripts" / "gate.sh", PASSING_GATE)
        (self.repo / "scripts" / "gate.sh").chmod(0o755)
        return run_cli("gate", "--summary", cwd=self.repo)


class GateSealTests(GateSealRepo):
    def test_unsealed_commit_fails_a_passing_gate(self) -> None:
        self.commit(SUITE, "unsealed\n")
        code, out = self.gate()
        self.assertEqual((code, out["exit"], out["seal"]["ok"]), (1, 0, False), out)
        self.assertEqual([v["reason"] for v in out["seal"]["violations"]], ["unsealed"])

    def test_sealed_passing_gate_is_ok(self) -> None:
        self.seal(SUITE, "a\n")
        code, out = self.gate()
        self.assertEqual((code, out["seal"], out["weakened"]),
                         (0, {"ok": True, "violations": [], "taint": [], "warnings": []}, []), out)


class WeakenedTests(GateSealRepo):
    def write(self, path: str, text: str | bytes) -> None:
        if isinstance(text, bytes):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_bytes(text)
        else:
            put(self.repo / path, text)

    def test_weakened_reasons(self) -> None:
        unit = "tests/unit/test_a.py"
        cases = [
            ("skip added", unit, "@pytest.mark.skip\ndef test_a():\n    assert f() == 1\n    assert g() == 2\n",
             [{"path": unit, "reason": "skip-added"}]),
            ("assert removed", unit, "def test_a():\n    assert f() == 1\n", [{"path": unit, "reason": "assertions-removed"}]),
            ("js only and expect removed", "web/a.test.js", "it.only('a', () => {\n})\n",
             [{"path": "web/a.test.js", "reason": "skip-added"}, {"path": "web/a.test.js", "reason": "assertions-removed"}]),
            ("unicode path", "tests/unit/test_café.py", "def test_a():\n    pass\n",
             [{"path": "tests/unit/test_café.py", "reason": "assertions-removed"}]),
            ("not a test file", "src/a.py", "pass\n", []),
            ("assertion added", unit, "def test_a():\n    assert f() == 1\n    assert g() == 2\n    assert h()\n", []),
            ("latin-1 file", "tests/unit/test_lat.py", b"# caf\xe9\nassert x\n@pytest.mark.skip\n",
             [{"path": "tests/unit/test_lat.py", "reason": "skip-added"}]),
            ("binary file", "web/logo.test.png", b"\x89PNG\x00\xfe\xff", []),
        ]
        originals = {unit: "def test_a():\n    assert f() == 1\n    assert g() == 2\n",
                     "web/a.test.js": "it('a', () => {\n  expect(f()).toBe(1)\n})\n",
                     "tests/unit/test_café.py": "def test_a():\n    assert f()\n", "src/a.py": "assert x\n",
                     "tests/unit/test_lat.py": b"# caf\xe9\nassert x\n", "web/logo.test.png": b"\x89PNG\x00\xff"}
        git(self.repo, "checkout", "-q", "main")
        for path, text in originals.items():
            self.write(path, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "tests")
        for name, path, text, expected in cases:
            with self.subTest(name):
                git(self.repo, "checkout", "-q", "-B", "feat/x", "main")
                self.write(path, text)
                git(self.repo, "add", "-A")
                git(self.repo, "commit", "-q", "-m", name)
                code, out = self.gate()
                self.assertEqual((code, out["weakened"]), (0, expected), out)


if __name__ == "__main__":
    unittest.main()

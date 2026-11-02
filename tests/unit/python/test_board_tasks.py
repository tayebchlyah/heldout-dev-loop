"""Board task state, subagents, hook filtering, version keys and the snapshot hook."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from board import state as board_state  # noqa: E402
from board_fixtures import BoardHookFixture, RobustBoard, RobustnessHookFixture, epoch, put, set_time, slow  # noqa: E402


class BoardTaskStateTests(RobustBoard):
    def test_briefs_written_up_front_give_one_doing_task(self) -> None:
        """SDD preflight writes every brief at once: the native list names the doing task; with no native list, the
        first task with a brief and no `complete` line."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=3)
        self.ledger(wt, plan)
        for n in (1, 2, 3):
            put(self.sdd(wt, plan) / f"task-{n}-brief.md", f"# Task {n}\n")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["doing", "todo", "todo"])
        self.native("sess-north", ("Task 1: Step 1", "completed"), ("Task 2: Step 2", "in_progress"),
                    ("Task 3: Step 3", "pending"))
        self.ledger(wt, plan, "Task 1: complete")
        self.hook_event("prompt", wt, "sess-north")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["done", "doing", "todo"])

    def test_brief_born_before_the_previous_task_done_is_not_the_start(self) -> None:
        """A brief written up front is older than the previous task's done time: the task starts at that done time."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=2)
        folder = self.sdd(wt, plan)
        now = int(time.time())
        self.ledger(wt, plan)
        for name, at in {"task-1-brief.md": now - 3 * 3600, "task-2-brief.md": now - 3 * 3600,
                         "task-1-tests.log": now - 3600}.items():
            put(folder / name, f"{name}\n")
            set_time(folder / name, at)
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        tasks = self.own_note("gadget")["tasks"]
        self.assert_time(tasks[1].get("started"), (folder / "task-1-tests.log").stat().st_mtime, "Task 2 started")

    def test_native_doing_task_starts_at_the_run_not_at_an_old_brief(self) -> None:
        """The native list runs ahead of the ledger, the up-front brief of Task 2 is old: Task 2 starts when the
        board first sees it doing, not at the brief time."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=2)
        folder = self.sdd(wt, plan)
        self.ledger(wt, plan)
        put(folder / "task-2-brief.md", "task-2-brief.md\n")
        set_time(folder / "task-2-brief.md", time.time() - 3 * 3600)
        self.native("sess-north", ("Task 1: Step 1", "completed"), ("Task 2: Step 2", "in_progress"))
        before = int(time.time())  # Note times have whole seconds.
        self.hook_event("prompt", wt, "sess-north")
        self.ok(cwd=wt)
        after = time.time()
        self.assertEqual(self.states("gadget"), ["todo", "doing"])
        started = epoch(self.own_note("gadget")["tasks"][1]["started"])
        self.assertTrue(before <= started <= after, f"Task 2 started {started} not in the runs [{before}, {after}]")

    def test_saved_start_stays_when_the_previous_task_finishes_later(self) -> None:
        """Task 2 started (saved) before Task 1 is done: Task 1 done later does not move Task 2's start."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=2)
        folder = self.sdd(wt, plan)
        self.ledger(wt, plan)
        self.native("sess-north", ("Task 1: Step 1", "completed"), ("Task 2: Step 2", "in_progress"))
        self.hook_event("prompt", wt, "sess-north")
        self.ok(cwd=wt)
        first = self.own_note("gadget")["tasks"][1]["started"]
        put(folder / "task-1-tests.log", "tests\n")
        set_time(folder / "task-1-tests.log", time.time() + 600)
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        self.assertEqual(self.own_note("gadget")["tasks"][1]["started"], first)

    def test_task_todo_for_one_run_keeps_its_saved_times(self) -> None:
        """A task that is todo in one run (native list ahead or behind the ledger) keeps its saved start."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=2)
        self.ledger(wt, plan)
        self.native("sess-north", ("Task 1: Step 1", "pending"), ("Task 2: Step 2", "in_progress"))
        self.hook_event("prompt", wt, "sess-north")
        self.ok(cwd=wt)
        first = self.own_note("gadget")["tasks"][1]["started"]
        self.native("sess-north", ("Task 1: Step 1", "in_progress"), ("Task 2: Step 2", "pending"))
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["doing", "todo"])
        self.assertEqual(self.state()["features"]["gadget"]["tasks"]["2"]["started"], first)

    def test_briefed_task_before_a_doing_task_stays_doing(self) -> None:
        """Task 1 has a brief and no `complete` line (in review) while the native list marks Task 2 in_progress."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=3)
        self.ledger(wt, plan)
        for n in (1, 2, 3):
            put(self.sdd(wt, plan) / f"task-{n}-brief.md", f"# Task {n}\n")
        self.native("sess-north", ("Task 1: Step 1", "completed"), ("Task 2: Step 2", "in_progress"),
                    ("Task 3: Step 3", "pending"))
        self.hook_event("prompt", wt, "sess-north")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["doing", "doing", "todo"])


    def test_stage_5_time_waits_for_the_ledger_of_a_planned_feature(self) -> None:
        """D6: a feature with a plan and no ledger yet has no Stage 5 time; a Track S feature (no plan) has its first
        run time."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=2)
        self.ok(cwd=wt)
        self.assertEqual(self.own_note("gadget")["stage"], 5)
        self.assertNotIn("5", self.own_note("gadget")["stage_times"])
        self.ledger(wt, plan)
        self.ok(cwd=wt)
        self.assertIn("5", self.own_note("gadget")["stage_times"])
        quick = self.worktree("quick", prefix="fix")
        self.ok(cwd=quick)
        self.assertEqual(self.own_note("quick")["stage"], 5)
        self.assertIn("5", self.own_note("quick")["stage_times"])


class HookFilterTests(RobustnessHookFixture, unittest.TestCase):
    """D11: `board.sh post-tool` filter, in the oracle hook sandbox (a fake devloop.py logs each call)."""

    @slow
    def test_git_dash_c_commit_and_worktree_add_start_board(self) -> None:
        """`git -C <path> commit` and `git -C <path> worktree add` start one `board --event post-tool` run."""
        for harness in ("claude", "codex"):
            for command in ("git -C ../w commit -m 'Task 2'", "git -C /tmp/r worktree add ../r-x -b feat/x"):
                with self.subTest(harness=harness, command=command):
                    self.log.unlink(missing_ok=True)
                    result = self.board_sh(harness, "post-tool", self.repo, command=command)
                    self.assertEqual((result.returncode, result.stdout), (0, ""))
                    self.assertEqual(len([c for c in self.calls(wait=6) if "board" in c["argv"]]), 1)


class VersionKeyTests(unittest.TestCase):
    def test_order(self) -> None:
        """D2: X, Y, Z, then a release candidate before its release, then N as a number; no leading X.Y.Z is ()."""
        from board import store

        chain = ["2.1.1", "3.0.0-rc.1", "3.0.0-rc.2", "3.0.0-rc.10", "3.0.0", "3.0.1"]
        keys = [store.version_key(v) for v in chain]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), len(chain))
        for same in ["3.0.0-rc.0", "3.0.0-beta.1", "3.0.0-rc.1x", "3.0.0-rc.01"]:
            with self.subTest(same=same):
                self.assertEqual(store.version_key(same), store.version_key("3.0.0"))
        for none in [None, 3, "", "2.1", "abc"]:
            with self.subTest(none=none):
                self.assertEqual(store.version_key(none), ())


class BoardSubagentTests(RobustBoard):
    """A Stop while a subagent of the session runs (SubagentStart seen, no SubagentStop yet) waits for nobody."""

    def agent(self, name: str, cwd: Path, agent_id: str | None, session: str = "session-1", **extra: str) -> dict:
        hook = {"subagent-start": "SubagentStart", "subagent-stop": "SubagentStop"}[name]
        stdin = {"session_id": session, "cwd": str(cwd), "hook_event_name": hook, **extra}
        if agent_id:
            stdin["agent_id"] = agent_id
        return self.ok("--event", name, cwd=cwd, stdin=stdin)

    def test_stop_with_a_running_subagent_is_not_waiting(self) -> None:
        """Start a1, stop: no waiting. Stop a1, stop: reply in the session. A resumed a1 (start again) counts."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.agent("subagent-start", self.repo, "a1")
        self.event("stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")
        self.agent("subagent-stop", self.repo, "a1")
        self.event("stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "reply in the session")
        self.event("prompt", wt)
        self.agent("subagent-start", self.repo, "a1")
        self.event("stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")

    def test_another_session_subagent_does_not_count(self) -> None:
        """A subagent of another session leaves this session's Stop waiting."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.agent("subagent-start", wt, "b1", session="session-2")
        self.event("stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "reply in the session")

    def test_session_start_keeps_running_subagents(self) -> None:
        """session-start also fires on compact and resume with the same session id while background subagents run."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.agent("subagent-start", wt, "a1")
        self.event("session-start", wt)
        self.event("stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")

    def test_subagent_stop_without_an_id_changes_nothing(self) -> None:
        """A SubagentStop with no agent_id (Codex, an old queued line) drops no running subagent."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.agent("subagent-start", wt, "a1")
        self.agent("subagent-stop", wt, None)
        self.event("stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")

    def test_running_subagent_follows_its_transcript(self) -> None:
        """A subagent runs while its start or its transcript (<session dir>/subagents/agent-<id>.jsonl, beside the
        session transcript) moved in the last AGENT_IDLE_MINUTES; a killed agent goes quiet and expires."""
        idle = board_state.AGENT_IDLE_MINUTES * 60
        with tempfile.TemporaryDirectory() as tmp:
            session_log = Path(tmp) / "s1.jsonl"
            log = Path(tmp) / "s1" / "subagents" / "agent-a1.jsonl"
            put(log, "{}\n")
            set_time(log, 5000)
            data: dict = {"sessions": {}}
            board_state.agent_started(data, "s1", "a1", "2026-01-01T00:00:00Z", 1000.0, str(session_log))
            board_state.agent_started(data, "s1", "a2", "2026-01-01T00:00:00Z", 1000.0, None)
            self.assertEqual(board_state.running_agents(data, "s1", 1000 + idle - 1), ["a1", "a2"])
            self.assertEqual(board_state.running_agents(data, "s1", 5000 + idle - 1), ["a1"])
            self.assertEqual(board_state.running_agents(data, "s1", 5000 + idle + 1), [])

    def test_subagent_start_with_a_transcript_path_counts_as_running(self) -> None:
        """The hook JSON transcript_path goes through the board run: a fresh subagent keeps the Stop from waiting."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.agent("subagent-start", wt, "a1", transcript_path=str(self.repo / "s.jsonl"))
        self.event("stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")


class SnapshotBoardHookTests(BoardHookFixture, unittest.TestCase):
    """gate-snapshot.sh (Claude SubagentStart) runs `board --event subagent-start`. Fixture of the board hook tests."""

    @slow
    def test_subagent_start_runs_board(self) -> None:
        result = self.hook("claude", "gate-snapshot.sh", "SubagentStart")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assert_board_event(self.calls(wait=6), "subagent-start")


class CodexGateBoardHookTests(BoardHookFixture, unittest.TestCase):
    """The Codex gate.sh hook runs a passing fixture gate in its sandbox, then logs the board stop event."""

    @slow
    def test_codex_gate_runs_then_logs_board_event(self) -> None:
        for script, event in (("Stop", "stop"), ("SubagentStop", "subagent-stop")):
            with self.subTest(event=script):
                self.log.unlink(missing_ok=True)
                self.gate(False)
                blocked = self.hook("codex", "gate.sh", script)
                self.assertEqual(json.loads(blocked.stdout)["decision"], "block")
                self.assertEqual([c for c in self.calls(wait=1) if "board" in c["argv"]], [])
                self.gate(True)
                result = self.hook("codex", "gate.sh", script)
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                self.assert_board_event(self.calls(wait=6), event)

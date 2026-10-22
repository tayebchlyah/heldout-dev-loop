#!/usr/bin/env python3
"""Held-out oracle tests for the board robustness change (spec: Board Robustness).

Each test names the `## Acceptance oracle` number (O<n>) or flow (F<n>) of that spec. The tests use only
its seams: `devloop.py board` and `devloop.py status` as processes on a temporary repo and notes folder,
`board.sh` as a process with hook input on stdin, the HTTP server, the state file
`<git-common-dir>/dev-loop/board-state.json`, the note paths and the hook fragments. A fake Claude
native task list lives in a temporary CLAUDE_CONFIG_DIR. Every name in here is invented.

Fixture choices the spec leaves open:
- `task-N-brief.md` and `task-N-tests.log` sit next to the ledger, in `.superpowers/sdd/<plan>/`.
- File times are set with os.utime. On macOS, a utime earlier than the birth time moves the birth
  time back too, so a file gets a controlled birth time; the expected value is always read back with
  os.stat (st_birthtime, else st_mtime, as the spec allows on a system without birth times).
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_board import (CLAUDE_HOOKS, CODEX_HOOKS, DEVLOOP, GIT, ISO_Z, MACHINE, OTHER, PROJECT, REPO_ROOT, SCRIPTS,
                        VERSION, Board, git, health, note_name, note_text, put, read_note, request, slow, snapshot,
                        status_note)

HOOK_NAMES = {"session-start": "SessionStart", "prompt": "UserPromptSubmit", "stop": "Stop",
              "subagent-stop": "SubagentStop", "post-tool": "PostToolUse"}


def epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def birth(path: Path) -> float:
    st = path.stat()
    return getattr(st, "st_birthtime", st.st_mtime)


def set_time(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def yaml_block(fields: dict) -> str:
    """The Obsidian properties form (Fact 5): plain scalars, nested block maps and block lists."""

    def scalar(value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if value == "":
            return '""'
        return str(value)

    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, dict) and value:
            lines.append(f"{key}:")
            lines += [f'  "{k}": {scalar(v)}' for k, v in value.items()]
        elif isinstance(value, list) and value:
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    for i, (k, v) in enumerate(item.items()):
                        lines.append(f"  {'- ' if i == 0 else '  '}{k}: {scalar(v)}")
                else:
                    lines.append(f"  - {scalar(item)}")
        elif isinstance(value, (dict, list)):
            lines.append(f"{key}: {'{}' if isinstance(value, dict) else '[]'}")
        else:
            lines.append(f"{key}: {scalar(value)}")
    return "\n".join([*lines, "---"]) + "\n"


class RobustBoard(Board):
    """The test_board sandbox, plus a temporary CLAUDE_CONFIG_DIR for fake native task lists."""

    def setUp(self) -> None:
        super().setUp()
        self.claude_dir = self.root / "claude-config"
        self.claude_dir.mkdir()
        self.env["CLAUDE_CONFIG_DIR"] = str(self.claude_dir)

    @property
    def features_dir(self) -> Path:
        return self.board_dir / "features"

    @property
    def state_file(self) -> Path:
        return self.repo / ".git" / "dev-loop" / "board-state.json"

    def own_name(self, slug: str) -> str:
        return note_name(MACHINE["id"], PROJECT, slug)

    def own_path(self, slug: str) -> Path:
        return self.features_dir / self.own_name(slug)

    def own_note(self, slug: str) -> dict:
        path = self.own_path(slug)
        listing = sorted(str(p.relative_to(self.board_dir)) for p in self.board_dir.rglob("*")) if self.board_dir.exists() else []
        self.assertTrue(path.is_file(), f"no note at features/{path.name}: {listing}")
        return read_note(path)

    def state(self) -> dict:
        self.assertTrue(self.state_file.is_file(), f"no {self.state_file.name}")
        return json.loads(self.state_file.read_text())

    def old_note(self, slug: str, **over) -> dict:
        """An own note in the 2.1 form: own name, own machine_id, in `dev-loop-board/`."""
        fields = status_note(feature=slug, machine_id=MACHINE["id"], owner=MACHINE["owner"], machine=MACHINE["machine"],
                             **over)
        put(self.board_dir / self.own_name(slug), note_text(fields))
        return fields

    def sdd(self, wt: Path, plan: Path) -> Path:
        return wt / ".superpowers" / "sdd" / plan.stem

    def native(self, session: str, *items: tuple[str, str]) -> None:
        folder = self.claude_dir / "tasks" / session
        for n, (subject, status) in enumerate(items, 1):
            put(folder / f"{n}.json", json.dumps({"id": str(n), "subject": subject, "status": status}) + "\n")

    def hook_event(self, name: str, cwd: Path, session: str, command: str | None = None) -> dict:
        stdin: dict = {"session_id": session, "cwd": str(cwd), "hook_event_name": HOOK_NAMES[name]}
        if name == "post-tool":
            stdin.update(tool_name="Bash", tool_input={"command": command or "task-done 1"})
        return self.ok("--event", name, cwd=cwd, stdin=stdin)

    def status(self, cwd: Path | None = None, env: dict | None = None) -> dict:
        result = subprocess.run([sys.executable, str(DEVLOOP), "status"], cwd=cwd or self.repo, env=env or self.env,
                                capture_output=True, text=True, timeout=60)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"status stdout is not one JSON object: {result.stdout!r} {result.stderr!r}")
        self.assertEqual(result.returncode, 0, payload)
        return payload

    def assert_time(self, value: object, expected: float, what: str) -> None:
        self.assertIsInstance(value, str, what)
        assert isinstance(value, str)
        self.assertRegex(value, ISO_Z, what)
        self.assertLess(abs(epoch(value) - expected), 1.0, f"{what}: {value} is not {datetime.fromtimestamp(expected, timezone.utc)}")

    def states(self, slug: str) -> list[str]:
        return [t["state"] for t in self.own_note(slug)["tasks"]]

    def hold_lock(self):
        lock = self.repo / ".git" / "dev-loop" / "board.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock, "a+")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle


class RobustnessStateTests(RobustBoard):
    """State and notes: oracles 1 to 9 and 32 to 34."""

    def test_o1_note_goes_to_the_features_subfolder(self) -> None:
        """O1, D4: `board` writes the note to <notes>/dev-loop-board/features/<name>.md."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        out = self.ok(cwd=wt)
        path = self.own_path("gadget")
        note = self.own_note("gadget")
        self.assertEqual((note["type"], note["schema"], note["machine_id"]), ("dev-loop-status", 1, MACHINE["id"]))
        self.assertFalse((self.board_dir / path.name).exists())
        self.assertIn(str(path), [str(Path(p).resolve()) for p in out["notes"]])

    def test_o2_own_note_moves_from_the_board_folder_to_features(self) -> None:
        """O2, D4: an own note in dev-loop-board/ is moved to features/ with the same name; the old file is gone."""
        self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.old_note("gadget", title="Gadget Export")
        self.ok(cwd=wt)
        self.assertTrue(self.own_path("gadget").is_file())
        self.assertFalse((self.board_dir / self.own_name("gadget")).exists())

    def test_o3_yaml_block_note_is_written_again_as_json_lines(self) -> None:
        """O3, D1, D3: an own note in YAML block style is written again, one JSON value per line, times kept."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_path("gadget")
        before = self.own_note("gadget")
        path.write_text(yaml_block(before))
        with self.assertRaises(Exception):
            read_note(path)  # the fixture really is not one JSON value per line
        self.ok(cwd=wt)
        after = read_note(path)
        self.assertEqual(after["stage_times"], before["stage_times"])
        self.assertEqual([(t["n"], t.get("started"), t.get("done")) for t in after["tasks"]],
                         [(t["n"], t.get("started"), t.get("done")) for t in before["tasks"]])

    def test_o4_state_file_holds_the_note_times(self) -> None:
        """O4, D1: after a run, board-state.json holds stage_times and task times equal to the note's."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        note = self.own_note("gadget")
        entry = self.state()["features"]["gadget"]
        self.assertEqual(entry["stage_times"], note["stage_times"])
        noted = {str(t["n"]): {k: t[k] for k in ("started", "done") if k in t} for t in note["tasks"]}
        stored = {n: {k: v for k, v in times.items() if k in ("started", "done") and v} for n, times in entry["tasks"].items()}
        self.assertEqual({n: v for n, v in stored.items() if v}, {n: v for n, v in noted.items() if v})
        self.assertTrue(noted["1"].get("done"))

    def test_o5_hand_edited_time_comes_back_from_the_state(self) -> None:
        """O5, D1: a note edited by hand (a time changed) gets the time from the state again at the next run."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_path("gadget")
        before = self.own_note("gadget")
        self.assertIn("5", before["stage_times"])
        edited = {**before, "stage_times": {**before["stage_times"], "5": "2020-01-01T00:00:00Z"}}
        path.write_text(note_text(edited))
        self.ok(cwd=wt)
        self.assertEqual(read_note(path)["stage_times"], before["stage_times"])

    def test_o6_own_name_with_another_machine_id_is_foreign(self) -> None:
        """O6, D3: a file with an own name and another machine_id stays unchanged; skipped has `foreign`."""
        self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        path = self.own_path("gadget")
        put(path, note_text(status_note(feature="gadget", title="Gadget Export")))
        before = snapshot(self.board_dir)
        out = self.ok(cwd=wt)
        self.assertEqual(snapshot(self.board_dir).get(str(path.relative_to(self.board_dir))),
                         before[str(path.relative_to(self.board_dir))])
        self.assertTrue(self.skipped(out, "foreign", "gadget"), out)

    def test_o7_files_with_other_names_stay_unchanged(self) -> None:
        """O7, D3: a file with another name in dev-loop-board/ or features/ stays unchanged."""
        self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        own = self.own_note("gadget")
        stem = self.own_name("gadget")[:-3]
        others = {
            self.features_dir / f"{stem} 2.md": note_text(own),
            self.board_dir / f"{stem} 3.md": note_text(own),
            self.board_dir / note_name(OTHER["id"], PROJECT, "gadget"): note_text(status_note(feature="gadget")),
            self.features_dir / note_name(OTHER["id"], PROJECT, "widget"): note_text(status_note(feature="widget")),
        }
        for path, text in others.items():
            put(path, text)
        before = snapshot(self.board_dir)
        self.ok(cwd=wt)
        self.event("stop", wt)
        after = snapshot(self.board_dir)
        for path in others:
            rel = str(path.relative_to(self.board_dir))
            self.assertEqual(after.get(rel), before[rel], rel)

    def test_o8_first_run_keeps_the_old_note_values(self) -> None:
        """O8, D2: the first run of the new kit on a parseable old note keeps its stage_times, task times and
        assumptions_baseline."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        old = self.old_note(
            "gadget", title="Gadget Export", stage=5,
            stage_times={"2": "2026-10-01T08:00:00Z", "3": "2026-10-01T09:00:00Z", "4": "2026-10-01T10:00:00Z",
                         "5": "2026-10-01T11:00:00Z"},
            tasks=[{"n": 1, "title": "Step 1", "state": "done", "started": "2026-10-01T11:00:00Z",
                    "done": "2026-10-01T12:00:00Z"},
                   {"n": 2, "title": "Step 2", "state": "doing", "started": "2026-10-01T12:00:00Z"},
                   {"n": 3, "title": "Step 3", "state": "todo"}],
            assumptions_baseline=["Widgets are blue.", "Crates hold ten widgets."])
        self.ok(cwd=wt)
        note = self.own_note("gadget")
        for stage, at in old["stage_times"].items():
            self.assertEqual(note["stage_times"].get(stage), at, f"stage {stage}")
        tasks = {t["n"]: t for t in note["tasks"]}
        self.assertEqual((tasks[1].get("started"), tasks[1].get("done")), ("2026-10-01T11:00:00Z", "2026-10-01T12:00:00Z"))
        self.assertEqual(tasks[2].get("started"), "2026-10-01T12:00:00Z")
        self.assertEqual(note["assumptions_baseline"], ["Widgets are blue.", "Crates hold ten widgets."])

    def test_o9_status_reports_a_foreign_skip_and_nothing_after_a_clean_run(self) -> None:
        """O9, D5: `status` has board.skipped with `foreign` after a run that skipped a foreign note, and no
        `board` key after a run with no skip."""
        self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        self.assertNotIn("board", self.status())
        self.own_note("gadget")
        path = self.own_path("gadget")
        path.write_text(note_text(status_note(feature="gadget", title="Gadget Export")))
        out = self.ok(cwd=wt)
        self.assertTrue(self.skipped(out, "foreign", "gadget"), out)
        board = self.status().get("board")
        self.assertIsInstance(board, dict, "no board key after a foreign skip")
        self.assertRegex(board["at"], ISO_Z)
        self.assertIn({"feature": "gadget", "reason": "foreign"},
                      [{"feature": s.get("feature"), "reason": s.get("reason")} for s in board["skipped"]])
        path.unlink()
        out = self.ok(cwd=wt)
        self.assertEqual(out["skipped"], [])
        self.assertNotIn("board", self.status())

    def test_o32_note_in_both_folders_keeps_only_features(self) -> None:
        """O32, D3: an own note in both dev-loop-board/ and features/: after a run, only the features/ one exists."""
        self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        self.own_note("gadget")
        old = self.board_dir / self.own_name("gadget")
        shutil.copy2(self.own_path("gadget"), old)
        self.ok(cwd=wt)
        self.assertFalse(old.exists())
        self.assertTrue(self.own_path("gadget").is_file())

    @slow
    def test_o33_format_repair_keeps_updated(self) -> None:
        """O33, D1: the rewrite of oracle 3 keeps `updated` when no other field changes."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_path("gadget")
        before = self.own_note("gadget")
        path.write_text(yaml_block(before))
        time.sleep(1.1)
        self.ok(cwd=wt)
        after = read_note(path)
        self.assertEqual(after["updated"], before["updated"])

    def test_o34_notes_in_repo_skip_is_saved_and_reported(self) -> None:
        """O34, D5: a run with the notes folder in the repo saves `notes-in-repo` in the state file, and `status`
        reports it in `board`."""
        self.worktree("tiny", prefix="fix")
        inside = self.repo / "docs" / "notes"
        (inside / "specs").mkdir(parents=True)
        env = {**self.env, "DEV_LOOP_NOTES": str(inside)}
        out = self.ok(env=env)
        self.assertTrue(self.skipped(out, "notes-in-repo", None), out)
        saved = self.state()["last_run"]
        self.assertRegex(saved["at"], ISO_Z)
        self.assertIn("notes-in-repo", [s.get("reason") for s in saved["skipped"]])
        board = self.status(env=env).get("board")
        self.assertIsInstance(board, dict, "no board key after a notes-in-repo skip")
        self.assertIn("notes-in-repo", [s.get("reason") for s in board["skipped"]])


class RobustnessTimeTests(RobustBoard):
    """Times and task state: oracles 10 to 18 and 35 to 37."""

    def test_o10_stage_5_time_is_the_ledger_birth_time(self) -> None:
        """O10, D6: the Stage 5 time is the birth time of progress.md, also when the first run comes later."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan)
        ledger = self.sdd(wt, plan) / "progress.md"
        set_time(ledger, int(time.time()) - 3 * 3600)
        self.ledger(wt, plan, "Task 1: complete")
        expected = birth(ledger)
        self.ok(cwd=wt)
        self.assert_time(self.own_note("gadget")["stage_times"].get("5"), expected, "Stage 5")

    def test_o11_task_done_is_the_tests_log_mtime(self) -> None:
        """O11, D7: task `done` is the modification time of task-N-tests.log, also when the first run comes later."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        log = self.sdd(wt, plan) / "task-1-tests.log"
        put(log, "4 passed\n")
        set_time(log, int(time.time()) - 2 * 3600)
        self.ok(cwd=wt)
        task = self.own_note("gadget")["tasks"][0]
        self.assertEqual(task["state"], "done")
        self.assert_time(task.get("done"), log.stat().st_mtime, "Task 1 done")

    def test_o12_task_done_without_a_log_is_the_commit_time(self) -> None:
        """O12, D7: task `done` with no tests log is the commit time of <b> in its `complete` line (committer
        date, in UTC)."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        first = git(wt, "rev-parse", "--short", "HEAD").strip()
        put(wt / "gadget.txt", "export\n")
        git(wt, "add", "gadget.txt")
        dates = {"GIT_AUTHOR_DATE": "2026-10-02T09:00:00+00:00", "GIT_COMMITTER_DATE": "2026-10-03T10:20:30+02:00"}
        subprocess.run([*GIT, "commit", "-q", "-m", "gadget"], cwd=wt, env={**os.environ, **dates}, check=True,
                       capture_output=True)
        last = git(wt, "rev-parse", "--short", "HEAD").strip()
        self.ledger(wt, plan, f"Task 1: complete (commits {first}..{last}, tests green)")
        self.ok(cwd=wt)
        task = self.own_note("gadget")["tasks"][0]
        self.assertEqual(task["state"], "done")
        self.assert_time(task.get("done"), epoch("2026-10-03T08:20:30Z"), "Task 1 done")

    def test_o13_task_started_is_the_brief_birth_time(self) -> None:
        """O13, D7: task `started` is the birth time of task-N-brief.md."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=2)
        self.ledger(wt, plan)
        brief = self.sdd(wt, plan) / "task-1-brief.md"
        put(brief, "# Task 1\n")
        set_time(brief, int(time.time()) - 90 * 60)
        brief.write_text("# Task 1\n\nBrief.\n")
        expected = birth(brief)
        self.ok(cwd=wt)
        task = self.own_note("gadget")["tasks"][0]
        self.assertEqual(task["state"], "doing")
        self.assert_time(task.get("started"), expected, "Task 1 started")

    def test_o14_backfilled_from_the_first_stage_seen(self) -> None:
        """O14, D8: a first state entry at Stage 5 sets backfilled: true; an entry made by --claim at Stage 2 has
        backfilled: false."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan)
        self.ok(cwd=wt)
        self.assertIs(self.own_note("gadget").get("backfilled"), True)
        self.assertIs(self.state()["features"]["gadget"]["backfilled"], True)
        self.spec("widget", "Widget Import", review="draft")
        self.ok("--claim", "widget")
        self.assertIs(self.own_note("widget").get("backfilled", False), False)
        self.assertIs(self.state()["features"]["widget"]["backfilled"], False)

    def test_o35_copied_note_with_a_stage_2_time_is_not_backfilled(self) -> None:
        """O35, D8: a note copied by D2 with a Stage 2 time gets backfilled: false."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan)
        self.old_note("gadget", title="Gadget Export", stage=5,
                      stage_times={"2": "2026-10-01T08:00:00Z", "3": "2026-10-01T09:00:00Z", "5": "2026-10-01T11:00:00Z"})
        self.ok(cwd=wt)
        self.assertIs(self.own_note("gadget").get("backfilled", False), False)
        self.assertIs(self.state()["features"]["gadget"]["backfilled"], False)

    def test_o36_no_stage_5_time_before_the_ledger(self) -> None:
        """O36, D6: at Stage 5 with no progress.md, no Stage 5 time; when progress.md appears, the next run sets
        its birth time."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ok(cwd=wt)
        note = self.own_note("gadget")
        self.assertEqual(note["stage"], 5)
        self.assertNotIn("5", note["stage_times"])
        self.ledger(wt, plan)
        ledger = self.sdd(wt, plan) / "progress.md"
        set_time(ledger, int(time.time()) - 3600)
        self.ledger(wt, plan, "Started.")
        expected = birth(ledger)
        self.ok(cwd=wt)
        self.assert_time(self.own_note("gadget")["stage_times"].get("5"), expected, "Stage 5")

    def test_o15_two_in_progress_native_items_are_both_doing(self) -> None:
        """O15, D9 (a): two tasks whose native items are in_progress are both doing."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=4)
        self.ledger(wt, plan, "Task 1: complete")
        self.native("sess-north", ("Stage 5: Build", "in_progress"), ("Task 1: Step 1", "completed"),
                    ("Task 2: Step 2", "in_progress"), ("Task 3: Step 3", "in_progress"), ("Task 4: Step 4", "pending"))
        self.hook_event("prompt", wt, "sess-north")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["done", "doing", "doing", "todo"])

    @slow
    def test_o37_only_the_latest_session_list_gives_doing(self) -> None:
        """O37, D9, D12: two sessions mapped to one feature: only the native list of the session with the highest
        last_seen gives doing."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=3)
        self.ledger(wt, plan, "Task 1: complete")
        self.native("sess-early", ("Task 2: Step 2", "in_progress"), ("Task 3: Step 3", "pending"))
        self.native("sess-late", ("Task 2: Step 2", "pending"), ("Task 3: Step 3", "in_progress"))
        self.hook_event("prompt", wt, "sess-early")
        time.sleep(1.1)
        self.hook_event("prompt", wt, "sess-late")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["done", "todo", "doing"])
        time.sleep(1.1)
        self.hook_event("prompt", wt, "sess-early")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["done", "doing", "todo"])

    def test_o16_brief_gives_doing_without_a_native_list(self) -> None:
        """O16, D9 (b): a task with a brief and no `complete` line is doing with no native task list."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=3)
        self.ledger(wt, plan, "Task 1: complete")
        put(self.sdd(wt, plan) / "task-3-brief.md", "# Task 3\n")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["done", "todo", "doing"])

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

    def test_task_back_to_todo_forgets_its_start(self) -> None:
        """A task no longer doing and not done drops its saved start: its next start is a new time."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=2)
        self.ledger(wt, plan)
        self.native("sess-north", ("Task 1: Step 1", "pending"), ("Task 2: Step 2", "in_progress"))
        self.hook_event("prompt", wt, "sess-north")
        self.ok(cwd=wt)
        first = self.own_note("gadget")["tasks"][1]["started"]
        time.sleep(1.1)
        self.native("sess-north", ("Task 1: Step 1", "pending"), ("Task 2: Step 2", "pending"))
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["doing", "todo"])
        time.sleep(1.1)
        self.native("sess-north", ("Task 1: Step 1", "pending"), ("Task 2: Step 2", "in_progress"))
        self.ok(cwd=wt)
        self.assertGreater(self.own_note("gadget")["tasks"][1]["started"], first)

    def test_o17_first_task_not_done_is_doing_by_default(self) -> None:
        """O17, D9 (c): no native item and no brief, at Stage 5: only the first task not done is doing."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=3)
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["done", "doing", "todo"])

    def test_o18_complete_line_wins_over_a_native_in_progress(self) -> None:
        """O18, D9: a task with a `complete` line is done when its native item is still in_progress."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=2)
        self.ledger(wt, plan, "Task 1: complete")
        self.native("sess-north", ("Task 1: Step 1", "in_progress"), ("Task 2: Step 2", "pending"))
        self.hook_event("prompt", wt, "sess-north")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget")[0], "done")


class RobustnessEventTests(RobustBoard):
    """Events: oracles 21 to 24."""

    def test_o21_post_tool_clears_waiting(self) -> None:
        """O21, D11: --event post-tool clears waiting."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.hook_event("stop", wt, "sess-north")
        self.assertEqual(self.own_note("gadget")["waiting"], "reply in the session")
        self.hook_event("post-tool", wt, "sess-north", "task-done 1")
        self.assertEqual(self.own_note("gadget")["waiting"], "")

    def test_o22_mapped_session_stop_from_main_sets_waiting(self) -> None:
        """O22, D12: after an event in the worktree with session S, --event stop with S and cwd in the main
        checkout sets waiting on that feature."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.hook_event("prompt", wt, "sess-north")
        self.hook_event("stop", self.repo, "sess-north")
        self.assertEqual(self.own_note("gadget")["waiting"], "reply in the session")

    def test_o23_busy_run_without_event_is_queued(self) -> None:
        """O23, D13: a run without --event that finds the lock busy appends one line to board-events.jsonl."""
        self.worktree("tiny", prefix="fix")
        events = self.repo / ".git" / "dev-loop" / "board-events.jsonl"
        with self.hold_lock():
            before = events.read_text().splitlines() if events.exists() else []
            out = self.ok()
            after = events.read_text().splitlines() if events.exists() else []
        self.assertTrue(self.skipped(out, "busy", None), out)
        self.assertEqual(len(after), len(before) + 1, after)
        line = json.loads(after[-1])
        self.assertIn("event", line)
        self.assertIsNone(line["event"])

    @slow
    def test_o24_holder_pass_sees_a_plan_made_while_it_writes(self) -> None:
        """O24, D13: a plan file created while the lock holder writes: the holder's next pass shows Stage 5.

        The holder is one `board` run whose git calls each take 0.3 s more (a `git` wrapper first on its PATH).
        Once it holds the lock, a busy `--event subagent-stop` run queues an event, so the holder makes a
        second pass. Its first pass writes the Stage 4 note; the plan appears at once. A pass that clears its
        git cache spends 0.3 s in its first git call, so its stage comes after the plan exists."""
        spec = self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.acceptance(wt, "gadget")
        slow_bin = self.root / "slow-bin"
        put(slow_bin / "git", f'#!/bin/sh\nsleep 0.3\nexec "{shutil.which("git")}" "$@"\n')
        (slow_bin / "git").chmod(0o755)
        holder = subprocess.Popen([sys.executable, str(DEVLOOP), "board", "--no-serve"], cwd=wt,
                                  env={**self.env, "PATH": f"{slow_bin}{os.pathsep}{self.env['PATH']}"},
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: holder.poll() is None and holder.kill())
        lock = self.repo / ".git" / "dev-loop" / "board.lock"
        stdin = {"session_id": "sess-stream", "cwd": str(self.repo), "hook_event_name": "SubagentStop"}

        def busy_run() -> None:
            out = self.ok("--event", "subagent-stop", stdin=stdin)
            self.assertTrue(self.skipped(out, "busy", None), f"the holder had ended: {out}")

        def wait_for(check, what: str) -> None:
            deadline = time.monotonic() + 30
            while not check():
                self.assertIsNone(holder.poll(), f"the holder ended before {what}")
                self.assertLess(time.monotonic(), deadline, f"no {what} in 30 s")
                time.sleep(0.02)

        def held() -> bool:
            if not lock.exists():
                return False
            with open(lock, "a+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return True
                fcntl.flock(handle, fcntl.LOCK_UN)
                return False

        name = self.own_name("gadget")
        wait_for(held, "lock held")
        busy_run()
        wait_for(lambda: (self.features_dir / name).exists() or (self.board_dir / name).exists(), "Stage 4 note")
        self.plan(spec, "Gadget Export", 2)
        out, err = holder.communicate(timeout=90)
        self.assertEqual(holder.returncode, 0, out + err)
        path = self.features_dir / name if (self.features_dir / name).exists() else self.board_dir / name
        self.assertEqual(read_note(path)["stage"], 5)


class RobustnessKitTests(RobustBoard):
    """Kit: oracles 26, 27 and 38."""

    def drift(self, version: str) -> dict:
        self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        put(wt / ".dev-loop" / "MANIFEST.json", json.dumps({"version": version, "files": {}}) + "\n")
        return self.status(cwd=wt)

    def test_o26_newer_worktree_kit_reports_kit_drift(self) -> None:
        """O26, D16: `status` in a worktree whose kit version differs from the main worktree's has kit_drift."""
        self.assertEqual(self.drift("2.2.0").get("kit_drift"), {"worktree": "2.2.0", "main": VERSION})

    def test_o27_same_kit_reports_no_kit_drift(self) -> None:
        """O27, D16: with the same version, `status` has no kit_drift."""
        self.assertNotIn("kit_drift", self.drift(VERSION))

    def test_o38_older_worktree_kit_reports_no_kit_drift(self) -> None:
        """O38, D16: a worktree with an older kit than the main worktree: `status` has no kit_drift."""
        self.assertNotIn("kit_drift", self.drift("2.0.0"))


FAKE_CLI = (
    "#!/usr/bin/env python3\nimport json, os, sys\ndata = sys.stdin.read()\n"
    "with open(os.environ['BOARD_TEST_LOG'], 'a') as log:\n"
    "    log.write(json.dumps({'label': %r, 'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin': data}) + '\\n')\n"
)


class RobustnessHookTests(unittest.TestCase):
    """Hooks: oracles 19, 20, 25 and 39. `board.sh` runs from a kit layout copy in a temp repo; a fake
    devloop.py at `.agents/skills/dev-loop/scripts/devloop.py` logs each call with its label."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / PROJECT
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        put(self.repo / "dev-loop.json", json.dumps({"version": VERSION, "harnesses": ["claude", "codex"]}) + "\n")
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            target = self.repo / ".dev-loop" / "hooks" / harness
            target.mkdir(parents=True)
            for script in source.iterdir():
                shutil.copy2(script, target / script.name)
        self.fake(self.repo, "MAIN")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "kit")
        self.log = self.root / "board-calls.jsonl"
        self.home = self.root / "home"
        self.home.mkdir()

    def fake(self, checkout: Path, label: str) -> None:
        path = checkout / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"
        put(path, FAKE_CLI % label)
        path.chmod(0o755)

    def board_sh(self, harness: str, arg: str, cwd: Path, command: str | None = None,
                 checkout: Path | None = None) -> subprocess.CompletedProcess[str]:
        payload: dict = {"session_id": "sess-hook", "cwd": str(cwd), "hook_event_name": "PostToolUse"}
        if command is not None:
            payload.update(tool_name="Bash", tool_input={"command": command})
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        env.update(BOARD_TEST_LOG=str(self.log), HOME=str(self.home))
        if harness == "claude":
            env["CLAUDE_PROJECT_DIR"] = str(self.repo)
        script = (checkout or self.repo) / ".dev-loop" / "hooks" / harness / "board.sh"
        return subprocess.run(["bash" if harness == "claude" else "sh", str(script), arg], cwd=cwd,
                              input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=60)

    def calls(self, wait: float) -> list[dict]:
        deadline = time.monotonic() + wait
        while not self.log.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(0.3)
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    @slow
    def test_o19_post_tool_with_an_unrelated_command_starts_no_board(self) -> None:
        """O19, D11: `board.sh post-tool` with tool_input.command `ls` starts no `board` run."""
        for harness in ("claude", "codex"):
            with self.subTest(harness):
                self.log.unlink(missing_ok=True)
                result = self.board_sh(harness, "post-tool", self.repo, command="ls")
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                self.assertEqual([c for c in self.calls(wait=1.5) if "board" in c["argv"]], [])

    @slow
    def test_o20_post_tool_after_task_done_starts_board(self) -> None:
        """O20, D11: `board.sh post-tool` with a command that holds `task-done` starts `board --event post-tool`."""
        for harness in ("claude", "codex"):
            with self.subTest(harness):
                self.log.unlink(missing_ok=True)
                result = self.board_sh(harness, "post-tool", self.repo, command="task-done 2")
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                board = [c for c in self.calls(wait=6) if "board" in c["argv"]]
                self.assertEqual(len(board), 1, board)
                argv = board[0]["argv"]
                self.assertIn("--event", argv)
                self.assertEqual(argv[argv.index("--event") + 1], "post-tool")
                self.assertEqual(json.loads(board[0]["stdin"]).get("cwd"), str(self.repo))

    def test_o25_fragments_have_the_post_tool_entries(self) -> None:
        """O25, D11: the Claude fragment has PostToolUse entries with matchers `TaskCreate|TaskUpdate` and `Bash`;
        the Codex fragment has one with matcher `Bash`. Each runs `board.sh post-tool`."""
        for path, matchers in ((REPO_ROOT / "claude" / "settings.fragment.json", {"Bash", "TaskCreate|TaskUpdate"}),
                               (REPO_ROOT / "codex" / "hooks.fragment.json", {"Bash"})):
            with self.subTest(path.parent.name):
                entries = json.loads(path.read_text())["hooks"].get("PostToolUse", [])
                found = {e.get("matcher") for e in entries
                         if any(str(h.get("command", "")).endswith("board.sh post-tool") for h in e.get("hooks", []))}
                self.assertLessEqual(matchers, found, entries)

    @slow
    def test_o39_codex_board_sh_in_a_worktree_runs_the_main_cli(self) -> None:
        """O39, D4: Codex `board.sh` with cwd in a worktree runs the CLI of the main worktree."""
        wt = self.root / "worktrees" / f"{PROJECT}-gadget"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/gadget", str(wt))
        wt = wt.resolve()
        self.fake(wt, "WORKTREE")
        for name, checkout in (("worktree copy", wt), ("main copy", self.repo)):
            with self.subTest(name):
                self.log.unlink(missing_ok=True)
                self.board_sh("codex", "prompt", wt, checkout=checkout)
                calls = self.calls(wait=6)
                self.assertEqual([c["label"] for c in calls], ["MAIN"], calls)


class RobustnessServerBase(RobustBoard):
    """The server that `board` starts, on the test port. Every test stops it again."""

    def setUp(self) -> None:
        super().setUp()
        (self.vault / ".obsidian").mkdir()
        self.board_dir.mkdir(parents=True)
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        try:
            self.board("--stop-server", timeout=30)
        except (AssertionError, subprocess.TimeoutExpired):
            pass
        self.kill_stray_server()

    def serve(self, cwd: Path | None = None) -> dict:
        out = self.ok(cwd=cwd, serve=True, timeout=90)
        deadline = time.monotonic() + 5
        while health(self.port) is None and time.monotonic() < deadline:
            time.sleep(0.1)
        return out

    def api_features(self) -> set[str]:
        status, _, body = request(self.port, "/api/status")
        self.assertEqual(status, 200)
        return {f.get("feature") for f in json.loads(body)["features"]}


class RobustnessServerTests(RobustnessServerBase):
    """Server: oracle 28 and flow 6."""

    def test_o28_api_lists_both_folders(self) -> None:
        """O28, D4: GET /api/status lists notes from dev-loop-board/ and from dev-loop-board/features/."""
        wt = self.worktree("tiny", prefix="fix")
        put(self.board_dir / note_name(OTHER["id"], PROJECT, "zephyr-old"), note_text(status_note(feature="zephyr-old")))
        other = self.vault / "Projects" / "Other Thing" / "dev-loop-board"
        put(other / "features" / "yonder.md", note_text(status_note(project="other-thing", feature="yonder")))
        put(other / "elder.md", note_text(status_note(project="other-thing", feature="elder")))
        self.serve(cwd=wt)
        self.assertTrue(self.own_path("tiny").is_file())
        self.assertLessEqual({"tiny", "zephyr-old", "yonder", "elder"}, self.api_features())

    def test_f6_upgrade_moves_the_own_note_and_keeps_the_foreign_one(self) -> None:
        """F6: an old own note and a note of another machine in dev-loop-board/. After one run: the own note is in
        features/ with its times; the other machine's note is unchanged and still served."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        times = {"2": "2026-10-01T08:00:00Z", "3": "2026-10-01T09:00:00Z", "4": "2026-10-01T10:00:00Z",
                 "5": "2026-10-01T11:00:00Z"}
        self.old_note("gadget", title="Gadget Export", stage=5, stage_times=times,
                      tasks=[{"n": 1, "title": "Step 1", "state": "done", "started": "2026-10-01T11:00:00Z",
                              "done": "2026-10-01T12:00:00Z"},
                             {"n": 2, "title": "Step 2", "state": "doing", "started": "2026-10-01T12:00:00Z"},
                             {"n": 3, "title": "Step 3", "state": "todo"}])
        foreign = self.board_dir / note_name(OTHER["id"], PROJECT, "widget")
        put(foreign, note_text(status_note(feature="widget", title="Widget Import", stage=5)))
        before = (foreign.read_bytes(), foreign.stat().st_mtime_ns)
        self.serve(cwd=wt)
        self.assertFalse((self.board_dir / self.own_name("gadget")).exists())
        note = self.own_note("gadget")
        for stage, at in times.items():
            self.assertEqual(note["stage_times"].get(stage), at, f"stage {stage}")
        self.assertEqual((note["tasks"][0].get("started"), note["tasks"][0].get("done")),
                         ("2026-10-01T11:00:00Z", "2026-10-01T12:00:00Z"))
        self.assertEqual((foreign.read_bytes(), foreign.stat().st_mtime_ns), before)
        self.assertLessEqual({"gadget", "widget"}, self.api_features())


class RobustnessFlowTests(RobustBoard):
    """Acceptance flows 1 to 5."""

    def test_f1_obsidian_rewrite(self) -> None:
        """F1: the run writes a note; it is changed to YAML block style; the next run writes it again in one JSON
        value per line, with the same times, and `notes` lists it."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_path("gadget")
        before = self.own_note("gadget")
        path.write_text(yaml_block(before))
        out = self.ok(cwd=wt)
        after = read_note(path)
        self.assertEqual(after["stage_times"], before["stage_times"])
        self.assertEqual(after["tasks"], before["tasks"])
        self.assertIn(str(path), [str(Path(p).resolve()) for p in out["notes"]])

    def test_f2_late_first_run(self) -> None:
        """F2: a worktree with a plan, a ledger born at T0, Task 1 brief (T1), Task 1 tests log (T2), Task 2 brief
        (T3); the first run at T4 shows Stage 5 at T0, Task 1 from T1 to T2, Task 2 doing from T3, backfilled."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=3)
        folder = self.sdd(wt, plan)
        now = int(time.time())
        self.ledger(wt, plan)
        ledger = folder / "progress.md"
        set_time(ledger, now - 4 * 3600)
        files = {"task-1-brief.md": now - 3 * 3600, "task-1-tests.log": now - 2 * 3600, "task-2-brief.md": now - 3600}
        for name, at in files.items():
            put(folder / name, f"{name}\n")
            set_time(folder / name, at)
        self.ledger(wt, plan, "Task 1: complete")
        t0, t1, t3 = birth(ledger), birth(folder / "task-1-brief.md"), birth(folder / "task-2-brief.md")
        t2 = (folder / "task-1-tests.log").stat().st_mtime
        self.ok(cwd=wt)
        note = self.own_note("gadget")
        self.assert_time(note["stage_times"].get("5"), t0, "Stage 5")
        tasks = note["tasks"]
        self.assertEqual([t["state"] for t in tasks], ["done", "doing", "todo"])
        self.assert_time(tasks[0].get("started"), t1, "Task 1 started")
        self.assert_time(tasks[0].get("done"), t2, "Task 1 done")
        self.assert_time(tasks[1].get("started"), t3, "Task 2 started")
        self.assertIs(note.get("backfilled"), True)

    def test_f3_parallel_tasks(self) -> None:
        """F3: native items `Task 5: ...` and `Task 7: ...` are in_progress: tasks 5 and 7 are doing."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=7)
        self.ledger(wt, plan, *(f"Task {n}: complete" for n in range(1, 5)))
        self.native("sess-north", *((f"Task {n}: Step {n}", "completed") for n in range(1, 5)),
                    ("Task 5: Step 5", "in_progress"), ("Task 6: Step 6", "pending"), ("Task 7: Step 7", "in_progress"))
        self.hook_event("session-start", wt, "sess-north")
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["done"] * 4 + ["doing", "todo", "doing"])

    @slow
    def test_f4_long_turn_post_tool_refreshes_the_note(self) -> None:
        """F4: `board.sh post-tool` after `task-done` refreshes the note, with no Stop and no prompt between.
        Rows: the Claude hook, then the Codex hook, each from the main checkout kit."""
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=3)
        self.ledger(wt, plan)
        shutil.copytree(SCRIPTS.parent, self.repo / ".agents" / "skills" / "dev-loop",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            shutil.copytree(source, self.repo / ".dev-loop" / "hooks" / harness)
        self.ok(cwd=wt)
        self.assertEqual(self.states("gadget"), ["doing", "todo", "todo"])
        refreshed = False
        for n, harness in ((1, "claude"), (2, "codex")):
            with self.subTest(harness):
                self.ledger(wt, plan, *(f"Task {k}: complete" for k in range(1, n + 1)))
                put(self.sdd(wt, plan) / f"task-{n}-tests.log", "ok\n")
                env = {k: v for k, v in self.env.items() if k != "CLAUDE_PROJECT_DIR"}
                if harness == "claude":
                    env["CLAUDE_PROJECT_DIR"] = str(self.repo)
                payload = {"session_id": "sess-north", "cwd": str(wt), "hook_event_name": "PostToolUse",
                           "tool_name": "Bash", "tool_input": {"command": f"task-done {n}"}}
                script = self.repo / ".dev-loop" / "hooks" / harness / "board.sh"
                result = subprocess.run(["bash" if harness == "claude" else "sh", str(script), "post-tool"], cwd=wt,
                                        input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=60)
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if self.own_path("gadget").is_file() and self.states("gadget")[n - 1] == "done":
                        refreshed = True
                        break
                    time.sleep(0.2)
                self.assertEqual(self.states("gadget")[n - 1], "done")
        if refreshed:  # The hook runs `board` without --no-serve: let it end and its server come up, then stop it.
            time.sleep(1.5)
            deadline = time.monotonic() + 10
            while health(self.port, timeout=1) is None and time.monotonic() < deadline:
                time.sleep(0.2)
            self.board("--stop-server", timeout=30)

    @slow
    def test_f5_session_from_main(self) -> None:
        """F5: events with one session_id come from the worktree and from the main checkout; waiting follows
        that feature."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.stage5("widget", "Widget Import")
        self.hook_event("session-start", wt, "sess-north")
        self.hook_event("stop", self.repo, "sess-north")
        self.assertEqual(self.own_note("gadget")["waiting"], "reply in the session")
        self.hook_event("prompt", self.repo, "sess-north")
        self.assertEqual(self.own_note("gadget")["waiting"], "")
        self.hook_event("stop", wt, "sess-north")
        self.hook_event("prompt", self.repo, "sess-north")
        self.assertEqual(self.own_note("gadget")["waiting"], "")
        self.hook_event("stop", self.repo, "sess-north")
        self.assertEqual(self.own_note("gadget")["waiting"], "reply in the session")
        self.assertEqual(self.own_note("widget")["waiting"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

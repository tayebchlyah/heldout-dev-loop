#!/usr/bin/env python3
"""Robustness tests for `devloop.py board` (plan Review Focus). Same sandbox as the oracle tests."""

from __future__ import annotations

import fcntl
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skill" / "scripts"))
sys.path.insert(1, str(Path(__file__).resolve().parent))
from board import server
from board_fixtures import (DEVLOOP, ISO_Z, MACHINE, PROJECT, VERSION, Board, git, health, note_name, note_text, put,
                            read_note, snapshot, status_note, yaml_block)


class BoardRobustTests(Board):
    def setUp(self) -> None:
        super().setUp()
        log = mock.patch.object(server, "LOG", self.root / "dev-loop-board.log")
        log.start()
        self.addCleanup(log.stop)

    def test_malformed_board_files_are_left_alone(self) -> None:
        """Review Focus 1: a bad JSON line, no frontmatter, a sync conflict copy: unchanged, exit 0."""
        self.worktree("tiny", prefix="fix")
        ghost_tree = self.worktree("ghost", prefix="fix")
        self.ok()
        own = self.own_notes()
        # ghost leaves the write set, so a parse that skips the bad line would delete its note.
        git(self.repo, "worktree", "remove", "--force", str(ghost_tree))
        git(self.repo, "branch", "-D", "fix/ghost")
        ghost = own["ghost"]
        ghost.write_text(ghost.read_text().replace("stage: 5\n", "stage: [5\n"))
        put(self.board_dir / "features" / "plain.md", "Just some words.\n")
        copy = f"{own['tiny'].stem} 2.md"
        put(self.board_dir / "features" / copy, own["tiny"].read_text())
        names = [ghost.name, "plain.md", copy]
        before = snapshot(self.board_dir / "features")
        self.ok()
        after = snapshot(self.board_dir / "features")
        for name in names:
            self.assertEqual(after.get(name), before[name], name)

    def test_spec_without_goal_or_assumptions_and_plan_without_tasks(self) -> None:
        """Review Focus 4: no `## Goal`, no `## Assumptions`, no task heading: empty description, decisions, tasks."""
        spec = self.notes / "specs" / "20261001 Bare Thing.md"
        put(spec, "---\ntype: spec\nreview_status: approved\nfeature: bare\ntrack: M\nshipped: \n---\n\n# Bare Thing\n")
        wt = self.worktree("bare")
        self.acceptance(wt, "bare")
        put(self.notes / "plans" / "20261002 Bare Thing Plan.md", f"---\nspec: {spec.name}\n---\n\n# Plan\n\nNo tasks.\n")
        self.ok(cwd=wt)
        note = read_note(self.board_dir / "features" / note_name(MACHINE["id"], PROJECT, "bare"))
        self.assertEqual((note["stage"], note["title"]), (5, "Bare Thing"))
        self.assertEqual((note["description"], note["decisions"], note["tasks"]), ("", [], []))

    def test_event_with_bad_stdin_changes_no_waiting(self) -> None:
        """Review Focus 2: an event whose stdin is empty or not JSON has no scope: exit 0, waiting unchanged."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.event("stop", wt)
        for stdin in ("", "not json"):
            with self.subTest(stdin=stdin):
                result = subprocess.run([sys.executable, str(DEVLOOP), "board", "--event", "prompt", "--no-serve"],
                                        cwd=wt, env=self.env, input=stdin, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.note("alpha")["waiting"], "reply in the session")

    def test_bad_machine_json_gives_a_json_error(self) -> None:
        """A malformed or incomplete machine.json: exit 1 with one JSON `error`, no traceback, the file unchanged."""
        self.worktree("tiny", prefix="fix")
        path = self.home / ".config" / "dev-loop" / "machine.json"
        for text in ("{not json\n", json.dumps({"id": MACHINE["id"]}) + "\n"):
            with self.subTest(text=text):
                put(path, text)
                code, payload = self.board()
                self.assertEqual(code, 1, payload)
                self.assertIsInstance(payload.get("error"), str)
                self.assertEqual(path.read_text(), text)

    def test_bad_port_env_reports_error(self) -> None:
        """Review Focus 5: DEVLOOP_BOARD_PORT that is not a port: exit 0, server.state error with a message."""
        self.worktree("tiny", prefix="fix")
        for port in ("abc", "70000"):
            with self.subTest(port=port):
                code, payload = self.board(env={**self.env, "DEVLOOP_BOARD_PORT": port}, serve=True)
                self.assertEqual(code, 0, payload)
                self.assertEqual(payload["server"]["state"], "error")
                self.assertTrue(payload["server"].get("message"))

    def hold_lock(self):
        lock = self.repo / ".git" / "dev-loop" / "board.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock, "a+")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle

    def test_busy_event_is_queued_and_applied_by_the_next_run(self) -> None:
        """D2: a stop that finds the lock busy exits 0 with `busy`; the next run applies it."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.ok(cwd=wt)
        with self.hold_lock():
            out = self.event("stop", wt)
        self.assertTrue(self.skipped(out, "busy", None), out)
        self.assertEqual(self.note("alpha")["waiting"], "")
        self.ok()
        self.assertEqual(self.note("alpha")["waiting"], "reply in the session")
        self.assertFalse((self.repo / ".git" / "dev-loop" / "board-events.jsonl").exists())

    def test_catch_up_pass_writes_a_worktree_made_while_the_holder_writes(self) -> None:
        """D13: a worktree made once the holder has listed them: the queued busy event makes a second pass that
        reads the worktrees again and writes the new feature's note. The holder's git calls are slow (a wrapper)."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        holder = self.slow_holder(wt)
        self.spec("beta", "Beta Report")
        self.worktree("beta")
        out = self.ok("--event", "stop", stdin={"session_id": "s", "cwd": str(self.repo), "hook_event_name": "Stop"})
        self.assertTrue(self.skipped(out, "busy", None), f"the holder had ended: {out}")
        result, err = holder.communicate(timeout=90)
        self.assertEqual(holder.returncode, 0, result + err)
        self.assertEqual(self.note("beta")["stage"], 3)

    def slow_holder(self, cwd: Path, *args: str) -> subprocess.Popen:
        """A board run whose git calls are slow (a wrapper), returned once it holds the lock."""
        slow_bin = self.root / "slow-bin"
        put(slow_bin / "git", f'#!/bin/sh\nsleep 0.3\nexec "{shutil.which("git")}" "$@"\n')
        (slow_bin / "git").chmod(0o755)
        holder = subprocess.Popen([sys.executable, str(DEVLOOP), "board", "--no-serve", *args], cwd=cwd,
                                  env={**self.env, "PATH": f"{slow_bin}{os.pathsep}{self.env['PATH']}"},
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: holder.poll() is None and holder.kill())
        lock = self.repo / ".git" / "dev-loop" / "board.lock"
        deadline = time.monotonic() + 30
        while True:
            self.assertIsNone(holder.poll(), "the holder ended before it held the lock")
            self.assertLess(time.monotonic(), deadline, "no lock held in 30 s")
            if lock.exists():
                with open(lock, "a+") as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        return holder
                    fcntl.flock(handle, fcntl.LOCK_UN)
            time.sleep(0.02)

    def queue_stop(self) -> None:
        out = self.ok("--event", "stop", stdin={"session_id": "s", "cwd": str(self.repo), "hook_event_name": "Stop"})
        self.assertTrue(self.skipped(out, "busy", None), f"the holder had ended: {out}")

    def finish(self, holder: subprocess.Popen) -> dict:
        result, err = holder.communicate(timeout=90)
        self.assertEqual(holder.returncode, 0, result + err)
        return json.loads(result)

    def test_catch_up_pass_with_a_spec_placeholder_keeps_the_claim(self) -> None:
        """D5, D13: a spec evicted during the first write: no catch-up pass, the claim, its note and the queued event
        stay, skip `placeholder`. In process, so the eviction and the queued event land right after the first write."""
        from board import writer
        from test_devloop import load_devloop

        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        claims = self.claims.read_text()
        queue = self.repo / ".git" / "dev-loop" / "board-events.jsonl"
        real = writer.write
        passes: list[int] = []

        def write(*args: Any) -> None:
            real(*args)
            passes.append(1)
            if len(passes) == 1:
                spec.rename(spec.with_name(f".{spec.name}.icloud"))
                put(queue, json.dumps({"event": "stop", "cwd": str(self.repo), "session_id": "s", "at": "x"}) + "\n")

        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(writer, "write", write):
            code, payload = writer.run(load_devloop(), self.repo, self.notes, self.home, event=None, hook_cwd=None,
                                       hook_session=None, claim=None)
        self.assertEqual((code, len(passes)), (0, 1), payload)
        self.assertTrue(self.skipped(payload, "placeholder", None), payload)
        self.assertTrue(queue.is_file())
        self.assertEqual(self.claims.read_text(), claims)
        self.assertEqual(self.note("gadget")["stage"], 2)

    def test_catch_up_pass_reports_a_skip_once(self) -> None:
        """A per-feature skip seen by two passes is listed once in the payload and in last_run."""
        self.worktree("tiny", prefix="fix")
        put(self.board_dir / "features" / f".{note_name(MACHINE['id'], PROJECT, 'tiny')}.icloud", "")
        holder = self.slow_holder(self.repo)
        self.queue_stop()
        out = self.finish(holder)
        want = [{"feature": "tiny", "reason": "placeholder"}]
        self.assertEqual(out["skipped"], want)
        state = json.loads((self.repo / ".git" / "dev-loop" / "board-state.json").read_text())
        self.assertEqual(state["last_run"]["skipped"], want)

    def test_own_legacy_note_that_does_not_parse_moves_into_features(self) -> None:
        """D3, D4: an own note in YAML block form in dev-loop-board/ (2.1.0) is gone after one run; features/ holds
        the note with the times of the state. With a features/ copy already there, the legacy file is deleted."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.board_dir / "features" / note_name(MACHINE["id"], PROJECT, "gadget")
        legacy = self.board_dir / path.name
        before = read_note(path)
        for keep in (False, True):
            with self.subTest(features_copy=keep):
                if not keep:
                    path.unlink()
                legacy.write_text(yaml_block(before))
                self.ok(cwd=wt)
                self.assertFalse(legacy.exists())
                after = read_note(path)
                self.assertEqual(after["stage_times"], before["stage_times"])
                self.assertEqual(after["tasks"], before["tasks"])

    def test_crashed_run_is_reported_by_status(self) -> None:
        """A board run that raises: exit 1, last_run saved with reason `error`, `status` reports it."""
        self.worktree("tiny", prefix="fix")
        put(self.board_dir / "features", "not a folder\n")
        code, payload = self.board()
        self.assertEqual(code, 1, payload)
        result = subprocess.run([sys.executable, str(DEVLOOP), "status"], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=60)
        board = json.loads(result.stdout).get("board")
        self.assertIsInstance(board, dict, result.stdout)
        [entry] = board["skipped"]
        self.assertEqual((entry["feature"], entry["reason"]), (None, "error"))
        self.assertRegex(entry["message"], r"^\w+Error: ")

    def test_spec_placeholder_skips_the_whole_run(self) -> None:
        """D5: an iCloud placeholder in specs/: skip `placeholder`, exit 0, claims and notes unchanged."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        claims = self.claims.read_text()
        spec.rename(spec.with_name(f".{spec.name}.icloud"))
        before = snapshot(self.notes)
        out = self.ok()
        self.assertTrue(self.skipped(out, "placeholder", None), out)
        self.assertEqual(snapshot(self.notes), before)
        self.assertEqual(self.claims.read_text(), claims)

    def test_placeholder_in_features_is_skipped(self) -> None:
        """Review Focus 3: an iCloud placeholder of an own note in features/: skip `placeholder`, no note written."""
        self.worktree("tiny", prefix="fix")
        name = note_name(MACHINE["id"], PROJECT, "tiny")
        put(self.board_dir / "features" / f".{name}.icloud", "")
        out = self.ok()
        self.assertTrue(self.skipped(out, "placeholder", "tiny"), out)
        self.assertEqual(sorted(self.board_dir.glob("features/*.md")), [])
        self.assertFalse((self.board_dir / name).exists())

    def test_start_delivery_clears_once_the_worktree_exists(self) -> None:
        """D6: "start Delivery" goes when the claimed feature has a worktree, with no event."""
        self.spec("gadget", "Gadget Export")
        self.ok("--claim", "gadget")
        self.assertEqual(self.note("gadget")["waiting"], "start Delivery")
        self.worktree("gadget")
        self.ok()
        self.assertEqual(self.note("gadget")["waiting"], "")

    def test_claim_waits_for_the_lock(self) -> None:
        """D7: --claim waits up to 10 s for the lock: saved after a release, else exit 1 and not saved.
        The busy case runs with DEVLOOP_TEST_CLAIM_WAIT=1, so it does not wait the full 10 s."""
        self.spec("gadget", "Gadget Export", review="draft")
        for hold, code, env in ((1, 0, self.env), (4, 1, {**self.env, "DEVLOOP_TEST_CLAIM_WAIT": "1"})):
            with self.subTest(hold=hold):
                timer = threading.Timer(hold, self.hold_lock().close)
                timer.start()
                try:
                    got, out = self.board("--claim", "gadget", env=env, timeout=30)
                finally:
                    timer.join()
                self.assertEqual(got, code, out)
                if code:
                    self.assertEqual(out, {"error": "board lock busy, claim not saved"})
                    self.assertFalse(self.claims.exists())
                else:
                    self.assertIn("gadget", json.loads(self.claims.read_text()))
                    self.claims.unlink()

    def test_tasks_before_stage_5_are_todo(self) -> None:
        """D10: below Stage 5 (a plan, no acceptance file: Stage 3), every task not done is `todo`, no `started`."""
        spec = self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.plan(spec, "Gadget Export", 2)
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual(note["stage"], 3)
        self.assertEqual(note["tasks"], [{"n": 1, "title": "Step 1", "state": "todo"},
                                         {"n": 2, "title": "Step 2", "state": "todo"}])

    def test_board_note_at_a_release_candidate_is_rewritten_by_a_newer_one(self) -> None:
        """D2: a board note at 3.0.0-rc.1 is older than kit 3.0.0-rc.2: written again with the kit version."""
        from board import writer

        folder = self.board_dir / "features"
        for have, kit, rewritten in [("3.0.0-rc.1", "3.0.0-rc.2", True), ("3.0.0-rc.2", "3.0.0-rc.2", False),
                                     ("3.0.0-rc.2", "3.0.0-rc.1", False)]:
            with self.subTest(have=have, kit=kit):
                put(folder / "Dev Loop Board.md", writer.board_text(have))
                put(self.repo / ".dev-loop" / "MANIFEST.json", json.dumps({"version": kit, "files": {}}))
                writer.board_note(self.repo, folder, {"skipped": []})
                want = writer.board_text(kit if rewritten else have)
                self.assertEqual((folder / "Dev Loop Board.md").read_text(), want)

    def test_shipped_note_in_the_write_set_is_never_rewritten(self) -> None:
        """O5 regression (Task 3 fix 2): an own note with shipped_at, feature still in the write set: unchanged."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.ok(cwd=wt)
        path = self.own_notes()["alpha"]
        path.write_text(note_text({**read_note(path), "shipped_at": "2026-10-01T00:00:00Z"}))
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.ok(cwd=wt)
        self.event("stop", wt)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_six_own_shipped_notes_keep_the_five_latest(self) -> None:
        """Board Ledger D5: 6 own shipped notes in one project, the next run keeps the 5 latest shipped_at."""
        paths = []
        for day in range(1, 7):
            slug = f"done{day}"
            path = self.board_dir / "features" / note_name(MACHINE["id"], PROJECT, slug)
            put(path, note_text(status_note(feature=slug, machine_id=MACHINE["id"], owner=MACHINE["owner"],
                                            machine=MACHINE["machine"], shipped_at=f"2026-10-0{day}T12:00:00Z")))
            paths.append(path)
        self.ok()
        self.assertEqual([p.exists() for p in paths], [False, True, True, True, True, True])

    def test_features_file_drops_a_feature_that_left_with_no_note(self) -> None:
        """O5 regression (Task 3 fix 3): a feature that left the write set and has no own note leaves the file."""
        wt = self.worktree("tiny", prefix="fix")
        self.ok()
        features = self.repo / ".git" / "dev-loop" / "board-features.json"
        self.assertEqual(json.loads(features.read_text()), ["tiny"])
        git(self.repo, "worktree", "remove", "--force", str(wt))
        git(self.repo, "branch", "-D", "fix/tiny")
        self.ok()
        self.assertEqual(self.own_notes(), {})
        self.assertEqual(json.loads(features.read_text()), [])

    def test_decision_log_rows_follow_the_new_assumptions(self) -> None:
        """Session B Autonomy D18: each `decision` row as `<Decision>: <Why>`, `Risk: ` on a risk row, no baseline for
        the log (rows present at the first run show), discovery rows left out; Track S reads its git dir log."""
        log = ("\n## Decision log\n\n| Time | Stage | Session | Kind | Risk | Decision | Why | Evidence | Result |\n"
               "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
               "| 2026-10-25T10:00Z | 5 |  | decision |  | Use JSON | stdlib only | a.py:3 |  |\n"
               "| 2026-10-25T10:01Z | 5 |  | decision | risk | Drop cache | too slow | b.py:1 |  |\n"
               "| 2026-10-25T10:02Z | 5 |  | discovery |  | Port busy | other run | log |  |\n")
        wt, plan = self.stage5("logged", "Logged Thing")
        self.ledger(wt, plan, "started")
        spec = self.notes / "specs" / "20261001 Logged Thing.md"
        spec.write_text(spec.read_text() + log)
        self.ok(cwd=wt)
        spec.write_text(spec.read_text().replace("- Widgets are blue.\n", "- Widgets are blue.\n- Keep v1\n"))
        self.ok(cwd=wt)
        self.assertEqual(self.note("logged")["decisions"], ["Keep v1", "Use JSON: stdlib only", "Risk: Drop cache: too slow"])
        quick = self.worktree("quick", prefix="fix")
        put(Path(git(quick, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "decisions.md", log)
        self.ok(cwd=quick)
        self.assertEqual(self.note("quick")["decisions"], ["Use JSON: stdlib only", "Risk: Drop cache: too slow"])

    def test_track_s_note_after_its_branch_is_gone(self) -> None:
        """A1, D11, D12: worktree and branch removed; its `head` on origin/main ships the note, else it is deleted."""
        for slug, merged in (("tiny", True), ("stray", False)):
            with self.subTest(slug=slug):
                wt = self.worktree(slug, prefix="fix")
                put(wt / f"{slug}.txt", "work\n")
                git(wt, "add", f"{slug}.txt")
                git(wt, "commit", "-q", "-m", slug)
                self.ok(cwd=wt)
                path = self.own_notes()[slug]
                if merged:
                    git(self.repo, "cherry-pick", f"fix/{slug}")
                    git(self.repo, "push", "-q", "origin", "main")
                    git(self.repo, "fetch", "-q", "origin")
                git(self.repo, "worktree", "remove", "--force", str(wt))
                git(self.repo, "branch", "-D", f"fix/{slug}")
                out = self.ok()
                if merged:
                    self.assertRegex(read_note(path)["shipped_at"], ISO_Z)
                else:
                    self.assertFalse(path.exists())
                    self.assertIn(str(path), [str(Path(p).resolve()) for p in out["removed"]])

    def test_note_is_written_only_on_a_change(self) -> None:
        """A2: a run with no change leaves the note bytes and mtime and still lists it; a ledger change writes it."""
        wt, plan = self.stage5("alpha", "Alpha Report")
        self.ok(cwd=wt)
        path = self.own_notes()["alpha"]
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        time.sleep(1.1)
        out = self.ok(cwd=wt)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertIn(str(path), [str(Path(p).resolve()) for p in out["notes"]])
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        note = read_note(path)
        self.assertEqual(note["tasks"][0]["state"], "done")
        self.assertEqual(note["updated"], note["tasks"][0]["done"])

    def test_corrupt_state_file_is_rebuilt(self) -> None:
        """Board Robustness D1, D2: a state file that does not parse: exit 0, the note keeps its stage_times
        (copied from the note again), and the state file parses after the run."""
        wt, plan = self.stage5("alpha", "Alpha Report")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_notes()["alpha"]
        before = read_note(path)["stage_times"]
        state = self.repo / ".git" / "dev-loop" / "board-state.json"
        state.write_text('{"features": [')
        self.ok(cwd=wt)
        self.assertEqual(read_note(path)["stage_times"], before)
        self.assertEqual(json.loads(state.read_text())["features"]["alpha"]["stage_times"], before)

    def test_state_drops_features_with_no_note(self) -> None:
        """Board Robustness D1: a state entry for a slug with no spec, no worktree and no note is gone after a run."""
        self.worktree("tiny", prefix="fix")
        self.ok()
        state = self.repo / ".git" / "dev-loop" / "board-state.json"
        data = json.loads(state.read_text())
        data["features"]["ghost"] = {"stage_times": {"2": "2026-10-01T08:00:00Z"}, "tasks": {}}
        state.write_text(json.dumps(data))
        self.ok()
        self.assertEqual(sorted(json.loads(state.read_text())["features"]), ["tiny"])

    def test_seeded_old_kit_note_keeps_its_updated(self) -> None:
        """Board Robustness D1, D2: the first run of the new kit on an old-kit note (no backfilled key) keeps its
        `updated`, so an idle feature stays idle after the upgrade."""
        wt, plan = self.stage5("alpha", "Alpha Report")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_notes()["alpha"]
        old = {k: v for k, v in read_note(path).items() if k != "backfilled"}
        old["updated"] = "2026-10-01T00:00:00Z"
        path.write_text(note_text(old))
        (self.repo / ".git" / "dev-loop" / "board-state.json").unlink()
        self.ok(cwd=wt)
        note = read_note(path)
        self.assertEqual(note["updated"], "2026-10-01T00:00:00Z")
        self.assertIn("backfilled", note)

    def test_hand_edited_time_is_restored_with_the_same_updated(self) -> None:
        """Board Robustness D1: a parsed note with a hand-changed time is written again with the state time and the
        same `updated`."""
        wt, plan = self.stage5("alpha", "Alpha Report")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_notes()["alpha"]
        before = read_note(path)
        path.write_text(note_text({**before, "stage_times": {**before["stage_times"], "5": "2020-01-01T00:00:00Z"}}))
        time.sleep(1.1)
        self.ok(cwd=wt)
        self.assertEqual(read_note(path), before)

    def test_spec_change_while_the_note_does_not_parse_moves_updated(self) -> None:
        """Board Robustness D1: a note that does not parse is written again; a spec title change in that run is a
        real change, so `updated` moves."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.ok(cwd=wt)
        path = self.own_notes()["alpha"]
        state = self.repo / ".git" / "dev-loop" / "board-state.json"
        data = json.loads(state.read_text())
        data["features"]["alpha"]["updated"] = "2026-10-01T00:00:00Z"
        state.write_text(json.dumps(data))
        path.write_text("---\nstage: [5\n---\n")
        spec = self.notes / "specs" / "20261001 Alpha Report.md"
        spec.write_text(spec.read_text().replace("# Alpha Report", "# Alpha Summary"))
        self.ok(cwd=wt)
        note = read_note(path)
        self.assertEqual(note["title"], "Alpha Summary")
        self.assertNotEqual(note["updated"], "2026-10-01T00:00:00Z")

    def test_new_note_shows_before_the_folder_walk_expires(self) -> None:
        """D8: folders are cached for 60 s, the .md files of each folder are listed on every request."""
        note = {"type": "dev-loop-status", "schema": 1, "feature": "alpha"}
        put(self.board_dir / "a.md", note_text(note))
        index = server.Index([self.vault])
        self.assertEqual([f["feature"] for f in index.status()[0]], ["alpha"])
        put(self.board_dir / "b.md", note_text({**note, "feature": "beta"}))
        put(self.board_dir / ".c.md", note_text({**note, "feature": "hidden"}))
        os.symlink(self.board_dir / "a.md", self.board_dir / "d.md")
        self.assertEqual([f["feature"] for f in index.status()[0]], ["alpha", "beta"])

    def test_restart_carries_this_kit_version(self) -> None:
        """D3: a newer server without this root is restarted with the version of this kit, the code it runs."""
        other = self.root / "other-vault"
        other.mkdir()
        real_popen = subprocess.Popen
        children: list[subprocess.Popen] = []

        def spawn(*args: Any, **kwargs: Any) -> subprocess.Popen:
            children.append(real_popen(*args, **kwargs))
            return children[-1]

        def reap() -> None:
            self.stop_port()
            for child in children:
                child.wait(timeout=10)  # No ResourceWarning for a child nobody waited on.

        self.addCleanup(reap)
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), mock.patch.object(subprocess, "Popen", spawn):  # C4: its pidfile goes to the sandboxed HOME.
            self.assertEqual(server.start(self.port, "99.0.0", [other], "x", "y", "started")["state"], "started")
        self.worktree("tiny", prefix="fix")
        code, out = self.board(serve=True, timeout=90)
        self.assertEqual((code, out["server"]["state"]), (0, "restarted"), out)
        found = health(self.port)
        assert found is not None
        self.assertEqual(found["version"], VERSION)

    def stop_port(self) -> None:
        found = health(self.port, timeout=1)
        if found and isinstance(found.get("pid"), int):
            os.kill(found["pid"], signal.SIGKILL)

    def test_start_keeps_one_old_log_generation(self) -> None:
        """A log over the cap moves to `.1` at launch, replacing the older one; a log at the cap stays."""
        log, old = server.LOG, self.root / "dev-loop-board.log.1"
        child = mock.Mock(pid=0)
        child.poll.return_value = 0
        for size, kept, moved in ((server.LOG_BYTES + 1, "", "x" * (server.LOG_BYTES + 1)),
                                  (server.LOG_BYTES, "x" * server.LOG_BYTES, "oldest\n")):
            with self.subTest(size=size):
                put(old, "oldest\n")
                put(log, "x" * size)
                with mock.patch.object(subprocess, "Popen", return_value=child):
                    server.start(self.port, VERSION, [self.vault], "x", "y", "started")
                self.assertEqual((log.read_text(), old.read_text()), (kept, moved))

    def test_fifo_note_does_not_block_the_reader(self) -> None:
        """C1, D21: a note swapped for a FIFO after the listing is skipped within 2 s; the other notes are read."""
        note = {"type": "dev-loop-status", "schema": 1, "feature": "alpha"}
        put(self.board_dir / "a.md", note_text(note))
        fifo = self.board_dir / "x.md"
        put(fifo, note_text({**note, "feature": "piped"}))

        class Swapped(server.Index):
            def notes(self, folders: list[str]) -> list[Path]:
                found = super().notes(folders)
                fifo.unlink()
                os.mkfifo(fifo)
                return found

        got: list[list[str]] = []
        reader = threading.Thread(target=lambda: got.append([f["feature"] for f in Swapped([self.vault]).status()[0]]),
                                  daemon=True)
        reader.start()
        reader.join(2)
        if reader.is_alive():  # Release a reader stuck in open() so the test ends.
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            reader.join(2)
            self.fail("the reader blocked on a FIFO")
        self.assertEqual(got, [["alpha"]])

    def pidfile(self) -> Path:
        return self.home / ".config" / "dev-loop" / f"board-server.{self.port}.pid"

    def harmless_child(self) -> subprocess.Popen:
        child = subprocess.Popen(["sleep", "60"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        return child

    def test_stop_returns_once_the_port_refuses(self) -> None:
        """C2: a board that stops answering as the board but still listens for 1.5 s: stop waits for the refusal."""
        script = ("import http.server, json, os, signal, sys, threading\n"
                  "dying = []\n"
                  "class H(http.server.BaseHTTPRequestHandler):\n"
                  "    def do_GET(self):\n"
                  "        body = json.dumps({'name': 'dev-loop board', 'version': '2.1.0', 'roots': [], 'pid': os.getpid()})\n"
                  "        self.send_response(404 if dying else 200)\n"
                  "        self.end_headers()\n"
                  "        self.wfile.write(body.encode())\n"
                  "    def log_message(self, *args): pass\n"
                  "def term(*args):\n"
                  "    dying.append(1)\n"
                  "    threading.Timer(1.5, os._exit, (0,)).start()\n"
                  "signal.signal(signal.SIGTERM, term)\n"
                  "http.server.ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n")
        fake = subprocess.Popen([sys.executable, "-c", script, str(self.port)])
        self.addCleanup(fake.wait)
        self.addCleanup(fake.kill)
        put(self.pidfile(), f"{fake.pid}\n")
        deadline = time.monotonic() + 5
        while health(self.port, timeout=1) is None and time.monotonic() < deadline:
            time.sleep(0.1)
        code, out = self.board("--stop-server", timeout=30)
        self.assertEqual((code, out["server"]["state"]), (0, "stopped"), out)
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", self.port), timeout=2).close()

    def test_two_runs_racing_on_a_free_port_both_succeed(self) -> None:
        """C3: two `board` runs started at the same moment: both exit 0, neither reports `error`, one server runs."""
        self.addCleanup(self.stop_port)
        self.worktree("tiny", prefix="fix")
        results: list[tuple[int, dict]] = []
        runs = [threading.Thread(target=lambda: results.append(self.board(serve=True, timeout=90))) for _ in range(2)]
        for run in runs:
            run.start()
        for run in runs:
            run.join()
        for code, out in results:
            self.assertEqual(code, 0, out)
            self.assertIn(out["server"]["state"], {"started", "running"}, out)
        found = health(self.port)
        assert found is not None
        self.assertEqual(self.pidfile().read_text().strip(), str(found["pid"]))
        self.assertEqual(self.servers(), {found["pid"]})

    def servers(self) -> set[int]:
        """The pids of the board server processes started for this test port."""
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True).stdout
        mark = f"server.py --port {self.port} "
        return {int(line.split()[0]) for line in out.splitlines() if mark in line}

    def test_racing_ensures_leave_one_server(self) -> None:
        """C5: four `ensure` calls a few ms apart on a free port, five times: when a call returns, its own child is
        either the server that answers /health with the pidfile's pid or gone; one server process is left."""
        real_popen = subprocess.Popen
        children: list[subprocess.Popen] = []

        def spawn(*args: Any, **kwargs: Any) -> subprocess.Popen:
            children.append(real_popen(*args, **kwargs))
            local.child = children[-1]
            return children[-1]

        def reap() -> None:
            self.stop_port()
            for child in children:
                child.kill()
                child.wait(timeout=10)

        self.addCleanup(reap)
        local = threading.local()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), mock.patch.object(subprocess, "Popen", spawn):
            for round in range(5):
                gate = threading.Barrier(4)
                leaked: list[int] = []
                states: list[str] = []

                def run(delay: float) -> None:
                    gate.wait()
                    time.sleep(delay)
                    local.child = None
                    states.append(server.ensure(self.port, VERSION, [self.vault], "x", "y")["state"])
                    found = health(self.port) or {}
                    if local.child is not None and local.child.poll() is None and local.child.pid != found.get("pid"):
                        leaked.append(local.child.pid)

                runs = [threading.Thread(target=run, args=(i * 0.03,)) for i in range(4)]
                for thread in runs:
                    thread.start()
                for thread in runs:
                    thread.join()
                self.assertLessEqual(set(states), {"started", "running"}, f"round {round}")
                self.assertEqual(leaked, [], f"round {round}: {states}")
                found = health(self.port)
                assert found is not None
                self.assertEqual(self.pidfile().read_text().strip(), str(found["pid"]))
                self.assertEqual(self.servers(), {found["pid"]}, f"round {round}")
                self.assertEqual(server.stop(self.port)["state"], "stopped")

    def test_a_board_answer_with_another_pid_is_not_trusted(self) -> None:
        """C4: /health names the board with a pid that is not the pidfile's: no signal, `port-busy`."""
        named, recorded = self.harmless_child(), self.harmless_child()
        put(self.pidfile(), f"{recorded.pid}\n")
        answer = json.dumps({"name": "dev-loop board", "version": "99.0.0", "roots": [str(self.notes)],
                             "pid": named.pid}).encode()

        class Fake(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, format: str, *args: object) -> None:
                pass

        fake = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Fake)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        self.addCleanup(fake.server_close)
        self.addCleanup(fake.shutdown)
        self.worktree("tiny", prefix="fix")
        for args in (("--stop-server",), ()):
            with self.subTest(args=args):
                code, out = self.board(*args, serve=True, timeout=90)
                self.assertEqual((code, out["server"]["state"]), (0, "port-busy"), out)
                self.assertEqual((named.poll(), recorded.poll()), (None, None))

    def test_task_10_in_progress_is_not_task_1(self) -> None:
        """Review Focus (Task 4, D9 a): a native `Task 10: x` in progress makes only task 10 `doing`, not task 1."""
        claude = self.root / "claude-config"
        self.env["CLAUDE_CONFIG_DIR"] = str(claude)
        wt, plan = self.stage5("gadget", "Gadget Export", tasks=10)
        self.ledger(wt, plan, *(f"Task {n}: complete" for n in range(2, 10)))
        put(claude / "tasks" / "sess-north" / "1.json",
            json.dumps({"id": "1", "subject": "Task 10: x", "status": "in_progress"}) + "\n")
        self.ok("--event", "prompt", cwd=wt, stdin={"session_id": "sess-north", "cwd": str(wt)})
        states = [t["state"] for t in self.note("gadget")["tasks"]]
        self.assertEqual(states, ["todo"] + ["done"] * 8 + ["doing"])

    def test_hook_input_without_session_id_is_ignored(self) -> None:
        """Review Focus (Task 4, D12): a hook input with no session_id, or a non-string one, maps no session."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        for stdin in ({"cwd": str(wt)}, {"cwd": str(wt), "session_id": 7}):
            with self.subTest(stdin=stdin):
                self.ok("--event", "stop", cwd=wt, stdin=stdin)
                state = json.loads((self.repo / ".git" / "dev-loop" / "board-state.json").read_text())
                self.assertEqual(state.get("sessions"), {})
        self.ok("--event", "stop", cwd=wt, stdin={"cwd": str(wt), "session_id": "sess-north"})
        state = json.loads((self.repo / ".git" / "dev-loop" / "board-state.json").read_text())
        self.assertEqual(state["sessions"]["sess-north"]["feature"], "gadget")  # The control: a string id maps.


if __name__ == "__main__":
    unittest.main(verbosity=2)

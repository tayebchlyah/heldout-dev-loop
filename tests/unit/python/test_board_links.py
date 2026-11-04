"""#239: the board writes and reads no file through a link: state files, the server log, linked specs and plans."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import devloop  # noqa: E402
from board import server, state, writer  # noqa: E402
from board_fixtures import MACHINE, Board, put  # noqa: E402

STATE_FILES = ("board.lock", "board-claims.json", "board-features.json", "board-state.json", "board-events.jsonl")


class BoardStateLinkTests(Board):
    def test_link_at_a_state_file_leaves_its_target_untouched(self) -> None:
        """NV8: a link at any state file, to a file or dangling: the target is unchanged or absent, skip `state-link`."""
        self.worktree("a/$(>marker)", prefix="fix")
        state_dir = self.repo / ".git" / "dev-loop"
        for name in STATE_FILES:
            for dangling in (False, True):
                with self.subTest(name=name, dangling=dangling):
                    shutil.rmtree(state_dir, ignore_errors=True)
                    state_dir.mkdir()
                    victim = self.root / f"victim-{name}"
                    victim.unlink(missing_ok=True)
                    if not dangling:
                        put(victim, "keep\n")
                    os.symlink(victim, state_dir / name)
                    code, out = self.board()
                    self.assertEqual(code, 0, out)
                    self.assertTrue(self.skipped(out, "state-link"), out)
                    if dangling:
                        self.assertFalse(victim.exists(), name)
                    else:
                        self.assertEqual(victim.read_text(), "keep\n", name)

    def test_linked_state_folder_is_refused(self) -> None:
        """NV8: the state folder itself a link: nothing is written through it."""
        target = self.root / "elsewhere"
        target.mkdir()
        os.symlink(target, self.repo / ".git" / "dev-loop")
        self.worktree("tiny", prefix="fix")
        code, out = self.board()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.skipped(out, "state-link"), out)
        self.assertEqual(list(target.iterdir()), [])

    def test_state_folder_of_another_user_is_refused(self) -> None:
        """NV8: a state folder this user does not own gives `state-owner`; `save_last_run` writes nothing there."""
        folder = self.root / "state"
        folder.mkdir()
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(state.unsafe(folder), "state-owner")
            state.save_last_run(folder, "2026-11-04T00:00:00Z", [])
        self.assertEqual(list(folder.iterdir()), [])
        self.assertIsNone(state.unsafe(folder))
        self.assertIsNone(state.unsafe(self.root / "missing"))


class StateFolderFdTests(Board):
    """#239: the state folder is opened once; every state file operation goes through that descriptor."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = self.root / "state"
        self.folder.mkdir()

    def opened(self) -> int:
        fd, reason = state.open_dir(self.folder)
        self.assertIsNone(reason)
        self.addCleanup(os.close, fd)
        return fd

    def test_open_dir_refuses_a_link_a_file_and_another_owner(self) -> None:
        """A link: `state-link`; not a folder, or a folder of another user: `state-owner`."""
        link, plain = self.root / "link", self.root / "plain"
        os.symlink(self.folder, link)
        put(plain, "x\n")
        self.assertEqual(state.open_dir(link), (None, "state-link"))
        self.assertEqual(state.open_dir(plain), (None, "state-owner"))
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(state.open_dir(self.folder), (None, "state-owner"))

    def test_folder_swapped_for_a_link_after_the_open_is_not_followed(self) -> None:
        """A folder swapped for a link after the open: the JSON write and the queue drain stay in the opened folder."""
        fd = self.opened()
        target = self.root / "elsewhere"
        target.mkdir()
        event = {"event": "stop", "cwd": str(self.repo), "session_id": None, "agent_id": None, "transcript_path": None}
        put(self.folder / "board-events.jsonl", json.dumps(event) + "\n")
        moved = self.root / "moved"
        self.folder.rename(moved)
        os.symlink(target, self.folder)
        state.save(self.folder, {"features": {}, "sessions": {}, "last_run": None}, fd)
        self.assertEqual(len(writer.drain(fd, "board-events.jsonl")), 1)
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue((moved / "board-state.json").is_file())
        self.assertFalse((moved / "board-events.jsonl").exists())

    def test_state_files_planted_as_links_are_not_read(self) -> None:
        """A link at a state file after the check: `load` gives the default, `read_slugs` nothing."""
        host = self.root / "host.json"
        put(host, json.dumps({"features": {"x": {}}, "evil": 1}))
        os.symlink(host, self.folder / "board-state.json")
        put(self.root / "host-list.json", json.dumps(["leak"]))
        os.symlink(self.root / "host-list.json", self.folder / "board-claims.json")
        self.assertEqual(state.load(self.folder), {"features": {}, "sessions": {}, "last_run": None})
        self.assertEqual(state.load(self.folder, self.opened()), {"features": {}, "sessions": {}, "last_run": None})
        self.assertEqual(writer.read_slugs(self.opened(), "board-claims.json"), [])

    def test_unreadable_queue_is_an_error_not_an_empty_queue(self) -> None:
        """EACCES on the queue is not dropped silently as an empty queue."""
        if os.getuid() == 0:
            self.skipTest("root reads any file")
        fd = self.opened()
        queue = self.folder / "board-events.jsonl"
        put(queue, "{}\n")
        queue.chmod(0)
        with self.assertRaises(PermissionError):
            writer.drain(fd, "board-events.jsonl")

    def test_fifo_in_the_state_folder_never_blocks_a_run(self) -> None:
        """A FIFO at the queue (idle or lock busy) or at the lock: the run ends, no hang."""
        self.worktree("tiny", prefix="fix")
        state_dir = self.repo / ".git" / "dev-loop"
        for name, busy, reason in (("board-events.jsonl", False, None), ("board-events.jsonl", True, "busy"),
                                   ("board.lock", False, "state-link")):
            with self.subTest(name=name, busy=busy):
                shutil.rmtree(state_dir, ignore_errors=True)
                state_dir.mkdir()
                holder = None
                if busy:
                    holder = os.open(state_dir / "board.lock", os.O_RDWR | os.O_CREAT, 0o600)
                    fcntl.flock(holder, fcntl.LOCK_EX)
                os.mkfifo(state_dir / name)
                try:
                    code, out = self.board(timeout=30)
                except subprocess.TimeoutExpired:
                    self.fail(f"a FIFO at {name} blocked the run")
                finally:
                    if holder is not None:
                        os.close(holder)
                self.assertEqual(code, 0, out)
                if reason:
                    self.assertTrue(self.skipped(out, reason), out)


class BoardLogLinkTests(Board):
    def test_dangling_log_link_creates_nothing(self) -> None:
        """F9: a dangling link at the old temp log and at the per-user log: no target is created, the server starts."""
        targets = [self.root / "made-by-temp-log", self.root / "made-by-home-log"]
        os.symlink(targets[0], self.root / "dev-loop-board.log")
        os.symlink(targets[1], self.home / ".config" / "dev-loop" / "board.log")
        self.worktree("tiny", prefix="fix")
        code, out = self.board(serve=True, timeout=90)
        self.assertEqual((code, out["server"]["state"]), (0, "started"), out)
        self.assertEqual([t.exists() for t in targets], [False, False])

    def test_log_is_a_private_file_beside_the_pidfile(self) -> None:
        """F9: the server log is `~/.config/dev-loop/board.log`, mode 0600."""
        self.worktree("tiny", prefix="fix")
        code, out = self.board(serve=True, timeout=90)
        self.assertEqual((code, out["server"]["state"]), (0, "started"), out)
        log = self.home / ".config" / "dev-loop" / "board.log"
        self.assertEqual(stat.S_IMODE(log.lstat().st_mode), 0o600)
        self.assertFalse((self.root / "dev-loop-board.log").exists())


class LinkedSpecTests(Board):
    def setUp(self) -> None:
        super().setUp()
        follow = mock.patch.object(devloop, "FOLLOW_NOTE_LINKS", False)
        follow.start()
        self.addCleanup(follow.stop)

    def test_linked_spec_plan_or_folder_gives_no_entry(self) -> None:
        """F10: a linked Spec.md, feature folder, old `specs/` file or folder: no specs entry; a linked plan: none.
        Outside the board (`decide`, #51) a linked spec still counts."""
        secret = self.repo / ".env"
        put(secret, "---\nfeature: leak\n---\n# SECRET-TOKEN-123\n")
        real = self.spec("real", "Real Thing")
        (self.notes / "Features" / "Leak").mkdir()
        os.symlink(secret, self.notes / "Features" / "Leak" / "Spec.md")
        outside = self.root / "outside"
        put(outside / "Spec.md", "---\nfeature: far\n---\n# Far\n")
        os.symlink(outside, self.notes / "Features" / "Far")
        self.assertEqual(devloop.read_specs(self.notes), [("real", real)])
        with mock.patch.object(devloop, "FOLLOW_NOTE_LINKS", True):
            self.assertEqual([slug for slug, _ in devloop.read_specs(self.notes)], ["far", "leak", "real"])
        os.symlink(secret, real.parent / "Plan.md")
        self.assertIsNone(devloop.find_plan(self.notes, real))

    def test_linked_old_layout_folders_give_nothing(self) -> None:
        """F10: old layout: a linked `specs/` or `plans/` folder, or a linked file in them, is skipped."""
        secret = self.repo / ".env"
        put(secret, "---\nfeature: leak\nspec: old.md\n---\n# SECRET-TOKEN-123\n")
        put(self.notes / "specs" / "old.md", "---\nfeature: old\n---\n# Old\n")
        os.symlink(secret, self.notes / "specs" / "leak.md")
        (self.notes / "plans").mkdir()
        os.symlink(secret, self.notes / "plans" / "leak.md")
        old = self.notes / "specs" / "old.md"
        self.assertEqual(devloop.read_specs(self.notes), [("old", old)])
        self.assertIsNone(devloop.find_plan(self.notes, old))
        shutil.rmtree(self.notes / "specs")
        shutil.rmtree(self.notes / "plans")
        far = self.root / "far"
        put(far / "specs" / "x.md", "---\nfeature: x\n---\n# X\n")
        put(far / "plans" / "x.md", "---\nspec: x.md\n---\n# Plan\n")
        os.symlink(far / "specs", self.notes / "specs")
        os.symlink(far / "plans", self.notes / "plans")
        self.assertEqual(devloop.read_specs(self.notes), [])
        self.assertIsNone(devloop.find_plan(self.notes, far / "specs" / "x.md"))

    def test_linked_spec_never_reaches_a_board_note(self) -> None:
        """F10: the first `#` line of a linked spec's target is not a note title."""
        put(self.repo / ".env", "---\nfeature: leak\n---\n# SECRET-TOKEN-123\n")
        (self.notes / "Features" / "Leak").mkdir()
        os.symlink(self.repo / ".env", self.notes / "Features" / "Leak" / "Spec.md")
        self.worktree("leak")
        self.ok()
        for path in (self.board_dir / "Features").glob("*.md"):
            self.assertNotIn("SECRET-TOKEN-123", path.read_text(), path)
        self.assertNotEqual(self.note("leak").get("title"), "SECRET-TOKEN-123")

    def merged(self, spec: Path | None, worktree: Path | None = None, stage: int = 3) -> dict:
        """`writer.merge` of one feature, as the hook runs it after the specs were listed."""
        ctx = writer.Context(dl=devloop, repo=self.repo, notes=self.notes, folder=self.board_dir, state=self.root,
                             state_fd=-1, machine=MACHINE, project="p", data={"sessions": {}},
                             claude=self.root / "claude", shipped_kept=5)
        found = {"stage": stage, "track": "M", "worktree": str(worktree) if worktree else None}
        return writer.merge(ctx, {}, "leak", found, spec, "2026-11-04T00:00:00Z", "")

    def test_spec_and_plan_swapped_for_links_after_the_listing_are_not_read(self) -> None:
        """#239 finding 1: `merge` reads the spec, its frontmatter and the plan through no link, whatever the listing saw."""
        secret = self.repo / ".env"
        put(secret, "---\nacceptance: LEAK\n---\n# SECRET-TOKEN-123\n\n## Goal\n\nSECRET-GOAL\n\n## Assumptions\n\n"
                    "- SECRET-ASSUMPTION\n\n### Task 1: SECRET-TASK\n")
        folder = self.notes / "Features" / "Leak"
        folder.mkdir()
        os.symlink(secret, folder / "Spec.md")
        os.symlink(secret, folder / "Plan.md")
        with mock.patch.object(devloop, "find_plan", return_value=folder / "Plan.md"):
            note = self.merged(folder / "Spec.md")
        self.assertEqual((note["title"], note["description"], note["acceptance"], note["tasks"]), ("leak", "", "", []))
        self.assertNotIn("SECRET", json.dumps(note))

    def test_spec_below_a_swapped_folder_is_not_read(self) -> None:
        """#239 finding 1: a `Features` or `Features/<x>` folder swapped for a link after the listing: no text."""
        real = self.spec("leak", "Leak")
        self.assertEqual(self.merged(real)["title"], "Leak")
        away = self.root / "away"
        real.parent.rename(away)
        os.symlink(away, real.parent)
        self.assertEqual(self.merged(real)["title"], "leak")
        real.parent.unlink()
        features = self.notes / "Features"
        features.rename(self.root / "away-features")
        os.symlink(self.root / "away-features", features)
        self.assertEqual(self.merged(features / "Leak" / "Spec.md")["title"], "leak")

    def test_linked_features_folder_gives_no_entry(self) -> None:
        """#239 finding 2: a linked `<notes>/Features` folder is not listed and gives no plan."""
        far = self.root / "far"
        put(far / "Leak" / "Spec.md", "---\nfeature: leak\n---\n# Far\n")
        put(far / "Leak" / "Plan.md", "# Plan\n")
        shutil.rmtree(self.notes / "Features")
        os.symlink(far, self.notes / "Features")
        self.assertEqual(devloop.read_specs(self.notes), [])
        self.assertIsNone(devloop.find_plan(self.notes, self.notes / "Features" / "Leak" / "Spec.md"))
        with mock.patch.object(devloop, "FOLLOW_NOTE_LINKS", True):
            self.assertEqual([slug for slug, _ in devloop.read_specs(self.notes)], ["leak"])

    def test_track_s_decision_log_is_read_through_no_link(self) -> None:
        """#239 finding 3: a linked `decisions.md`, a linked `dev-loop` folder or a FIFO gives an empty log."""
        quick = self.worktree("quick", prefix="fix")
        git_dir = Path(devloop.git(quick, "rev-parse", "--absolute-git-dir"))
        log = git_dir / "dev-loop" / "decisions.md"
        sample = ("## Decision log\n\n| Time | Stage | Session | Kind | Risk | Decision | Why | Evidence | Result |\n"
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                  "| t | 5 |  | decision |  | SECRET-DECISION | why | e |  |\n")
        put(self.root / "secret.md", sample)
        put(log, sample)
        self.assertEqual(self.merged(None, quick, 6)["decisions"], ["SECRET-DECISION: why"])
        log.unlink()
        os.symlink(self.root / "secret.md", log)
        self.assertEqual(self.merged(None, quick, 6)["decisions"], [])
        log.unlink()
        os.mkfifo(log)
        self.assertEqual(self.merged(None, quick, 6)["decisions"], [])
        log.unlink()
        shutil.move(str(log.parent), str(self.root / "real-state"))
        os.symlink(self.root / "real-state", log.parent)
        put(self.root / "real-state" / "decisions.md", sample)
        self.assertEqual(self.merged(None, quick, 6)["decisions"], [])

    def test_claim_of_a_linked_feature_says_the_link_is_skipped(self) -> None:
        """#239 finding 4: `--claim` of a feature whose spec is linked: the error names the skipped link."""
        os.symlink(self.repo / "GLOSSARY.md", self.notes / "Features" / "leak")
        code, out = self.board("--claim", "leak")
        self.assertEqual(code, 1)
        self.assertIn("no spec for leak", out["error"])
        self.assertIn("linked", out["error"])
        (self.notes / "Features" / "leak").unlink()
        code, out = self.board("--claim", "plain")
        self.assertEqual(code, 1)
        self.assertNotIn("linked", out["error"])


class ServerStartMessageTests(Board):
    def start_error(self, fd: int | None) -> str:
        proc = mock.Mock()
        proc.poll.return_value = 1
        with mock.patch.object(server, "open_log", return_value=fd), mock.patch.object(server.subprocess, "Popen", return_value=proc), \
                mock.patch.object(server, "probe", return_value=("none", {})):
            out = server.start(self.port, "1.0.0", [], "here", "me", "started")
        self.assertEqual(out["state"], "error")
        return out["message"]

    def test_message_names_the_log_only_when_it_was_opened(self) -> None:
        """A refused log (link, not owned): the message says so and names no log."""
        opened = os.open(self.root / "log", os.O_WRONLY | os.O_CREAT, 0o600)
        self.assertIn(", see ", self.start_error(opened))
        message = self.start_error(None)
        self.assertNotIn("see", message)
        self.assertIn("refused", message)

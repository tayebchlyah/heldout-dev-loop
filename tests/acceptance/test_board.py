#!/usr/bin/env python3
"""Held-out oracle tests for the note `devloop.py board` writes (spec: Progress Board).

Each test names the `## Acceptance oracle` number (O<n>) or flow (F<n>) it checks. The tests use
only the public seams: the stdout JSON of `devloop.py board`, the files it writes, the HTTP answers
of the server it starts, and the stdout of the hook scripts. Every name in here is invented (D33).
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import (BOARD_NOTE, GIT, ISO_Z, MACHINE, OTHER, PROJECT, VERSION, Board, frontmatter_version, git,
                            note_name, note_text, put, read_note, slow, snapshot, status_note)
from feature_folders_support import NewLayout


class BoardWriterTests(NewLayout, Board):
    def test_o1_note_path_type_schema_and_machine_id(self) -> None:
        """O1, superseded by Board Robustness oracle 1, then by Feature Folders: in a feature worktree, writes
        <notes>/Dev Loop Board/Features/<project>-<slug>-<hash>.md."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        out = self.ok(cwd=wt)
        path = self.features_dir / note_name(MACHINE["id"], PROJECT, "gadget")
        self.assertTrue(path.is_file(), sorted(str(p.relative_to(self.board_dir)) for p in self.board_dir.rglob("*")))
        fields = read_note(path)
        self.assertEqual((fields["type"], fields["schema"], fields["machine_id"]), ("dev-loop-status", 1, MACHINE["id"]))
        self.assertIn(str(path), [str(Path(p).resolve()) for p in out["notes"]])

    def test_o2_first_run_creates_machine_json_later_runs_keep_it(self) -> None:
        """O2, D3: the first run writes machine.json (16 hex id, owner from git user.name); later runs never change it."""
        machine = self.home / ".config" / "dev-loop" / "machine.json"
        machine.unlink()
        self.worktree("tiny", prefix="fix")
        self.ok()
        data = json.loads(machine.read_text())
        self.assertRegex(data["id"], r"^[0-9a-f]{16}$")
        self.assertEqual(data["owner"], "Robin")
        self.assertIsInstance(data["machine"], str)
        self.assertTrue(data["machine"])
        before = (machine.read_bytes(), machine.stat().st_mtime_ns)
        self.ok()
        self.assertEqual((machine.read_bytes(), machine.stat().st_mtime_ns), before)
        machine.write_text(json.dumps({**data, "machine": "bench-mac"}) + "\n")
        before = (machine.read_bytes(), machine.stat().st_mtime_ns)
        self.ok()
        self.assertEqual((machine.read_bytes(), machine.stat().st_mtime_ns), before)
        self.assertEqual(self.note("tiny", data["id"])["machine"], "bench-mac")

    def test_o3_project_is_the_same_for_two_clones_in_other_folders(self) -> None:
        """O3, D4: project comes from the origin URL, not the clone folder name."""
        one = self.root / "clone-one"
        two = self.root / "desk" / "widgets-copy"
        two.parent.mkdir()
        for path, slug in ((one, "alpha"), (two, "beta")):
            git(self.root, "clone", "-q", str(self.origin), str(path))
            wt = self.worktree(slug, prefix="fix", repo=path)
            self.ok(cwd=wt)
        self.assertEqual(self.note("alpha")["project"], self.note("beta")["project"])

    def test_o4_notes_for_the_write_set_only(self) -> None:
        """O4, D9: worktree features, claimed features and Track S worktrees; no other feature."""
        self.spec("alpha", "Alpha Report")
        self.worktree("alpha")
        self.spec("beta", "Beta Import", review="draft")
        self.ok("--claim", "beta")
        self.spec("gamma", "Gamma Draft", review="draft")
        self.spec("delta", "Delta Approved")
        self.spec("epsilon", "Epsilon Done", shipped="2026-10-10")
        self.worktree("epsilon")
        self.worktree("tiny", prefix="fix")
        out = self.ok()
        own = self.own_notes()
        self.assertEqual(set(own), {"alpha", "beta", "tiny"})
        self.assertEqual(read_note(own["tiny"])["track"], "S")
        self.assertEqual({str(Path(p).resolve()) for p in out["notes"]}, {str(p) for p in own.values()})

    def test_o5_claim_then_stop_gives_stage_2_approve_the_spec(self) -> None:
        """O5: --claim on the default branch, then --event stop: Stage 2, waiting "approve the spec"."""
        self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.event("stop", self.repo)
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (2, "approve the spec"))

    def test_o6_claim_without_a_spec_exits_1_and_writes_nothing(self) -> None:
        """O6: --claim <slug> with no spec for the slug exits 1 and writes nothing."""
        self.spec("gadget", "Gadget Export", review="draft")
        before = snapshot(self.root)
        code, _ = self.board("--claim", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.claims.exists())

    def test_o7_unclaimed_draft_gets_no_note(self) -> None:
        """O7: a draft spec without a claim on this machine gets no note."""
        self.spec("gamma", "Gamma Draft", review="draft")
        self.ok()
        self.event("stop", self.repo)
        self.assertNotIn("gamma", self.own_notes())

    def test_o8_approved_claim_shows_stage_3_start_delivery(self) -> None:
        """O8, D17, amended by one-session-effort: after approval of a claimed spec, the next run writes Stage 3
        "start Delivery"."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.ok()
        self.set_field(spec, "review_status", "approved")
        self.ok()
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (3, "start Delivery"))

    def test_o9_worktree_drops_the_claim_and_keeps_file_and_stage_times(self) -> None:
        """O9, D10: a worktree removes the claim; the note keeps its file and stage_times."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.ok()
        self.set_field(spec, "review_status", "approved")
        self.ok()
        path = self.own_notes()["gadget"]
        times = read_note(path)["stage_times"]
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        self.assertTrue(not self.claims.exists() or "gadget" not in self.claims.read_text())
        self.assertEqual(self.own_notes(), {"gadget": path})
        after = read_note(path)["stage_times"]
        for stage, at in times.items():
            self.assertEqual(after.get(stage), at)

    @slow
    def test_o10_note_of_another_machine_never_changes(self) -> None:
        """O10, D2, as Feature Folders replaced it: a file with another machine_id in `Dev Loop Board` stays
        unchanged after any run, also after the ship."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.board_dir.mkdir(parents=True)
        foreign = {
            "gadget": status_note(feature="gadget", title="Gadget Export", stage=5),
            **{f"old-{n}": status_note(feature=f"old-{n}", shipped_at=f"2026-09-0{n}T10:00:00Z") for n in range(1, 5)},
        }
        for slug, fields in foreign.items():
            put(self.board_dir / note_name(OTHER["id"], PROJECT, slug), note_text(fields))
        before = snapshot(self.board_dir)
        foreign_names = [note_name(OTHER["id"], PROJECT, slug) for slug in foreign]
        self.ok(cwd=wt)
        self.event("stop", wt)
        self.event("prompt", wt)
        self.set_field(self.spec_path("Gadget Export"), "shipped", "2026-10-15")
        self.ok(cwd=wt)
        self.ok()
        after = snapshot(self.board_dir)
        for name in foreign_names:
            self.assertEqual(after.get(name), before[name], name)

    def test_o11_no_dev_loop_json_writes_nothing(self) -> None:
        """O11, D8: a repo without dev-loop.json: nothing written, exit 0, `no-dev-loop` in skipped."""
        plain = self.root / "plain-repo"
        plain.mkdir()
        git(plain, "init", "-q")
        put(plain / "GLOSSARY.md", "# Glossary\n")
        git(plain, "add", "-A")
        git(plain, "commit", "-q", "-m", "base")
        git(plain, "switch", "-q", "-c", "fix/tiny")
        before = snapshot(self.vault)
        out = self.ok(cwd=plain)
        self.assertTrue(self.skipped(out, "no-dev-loop", None), out)
        self.assertEqual(snapshot(self.vault), before)

    def test_o12_rebase_keeps_that_note_and_updates_the_others(self) -> None:
        """O12, D8: a worktree mid-rebase keeps its note unchanged, listed `rebase`; other features update."""
        wt_a, plan_a = self.stage5("alpha", "Alpha Report")
        wt_b, plan_b = self.stage5("beta", "Beta Import")
        self.ok()
        note_a = self.own_notes()["alpha"]
        before = (note_a.read_bytes(), note_a.stat().st_mtime_ns)
        put(wt_a / "shared.txt", "alpha\n")
        git(wt_a, "add", "shared.txt")
        git(wt_a, "commit", "-q", "-m", "alpha side")
        put(self.repo / "shared.txt", "main\n")
        git(self.repo, "add", "shared.txt")
        git(self.repo, "commit", "-q", "-m", "main side")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        rebase = subprocess.run([*GIT, "rebase", "origin/main"], cwd=wt_a, capture_output=True, text=True)
        self.assertNotEqual(rebase.returncode, 0, "the fixture rebase must stop on a conflict")
        self.ledger(wt_a, plan_a, "Task 1: complete")
        self.ledger(wt_b, plan_b, "Task 1: complete")
        out = self.ok()
        self.assertTrue(self.skipped(out, "rebase", "alpha"), out)
        self.assertEqual((note_a.read_bytes(), note_a.stat().st_mtime_ns), before)
        self.assertEqual(self.note("beta")["tasks"][0]["state"], "done")

    def test_o13_notes_inside_the_repo_write_nothing(self) -> None:
        """O13, D8: the real path of the notes folder inside a worktree or the git folder: `notes-in-repo`."""
        self.worktree("tiny", prefix="fix")
        in_tree = self.repo / "docs" / "notes"
        in_git = self.repo / ".git" / "vault-notes"
        link = self.root / "notes-link"
        cases = {"worktree": in_tree, "git folder": in_git, "link to worktree": link}
        in_tree.mkdir(parents=True)
        in_git.mkdir(parents=True)
        link.symlink_to(in_tree)
        if sys.platform == "darwin":
            upper = self.repo.parent / self.repo.name.upper() / "docs" / "notes"
            if upper.is_dir():
                cases["other case"] = upper
        for name, notes in cases.items():
            with self.subTest(name):
                env = {**self.env, "DEV_LOOP_NOTES": str(notes)}
                out = self.ok(env=env)
                self.assertTrue(self.skipped(out, "notes-in-repo", None), out)
                for real in (in_tree, in_git):
                    self.assertEqual(sorted(p.name for p in real.iterdir()), [])

    def test_o14_busy_lock_writes_nothing_and_returns_at_once(self) -> None:
        """O14, D8: a busy board.lock: nothing written, `busy`, exit 0 at once."""
        self.worktree("tiny", prefix="fix")
        lock = self.repo / ".git" / "dev-loop" / "board.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                pass
            before = snapshot(self.vault)
            start = time.monotonic()
            code, out = self.board(timeout=30)
            elapsed = time.monotonic() - start
        self.assertEqual(code, 0, out)
        self.assertTrue(self.skipped(out, "busy", None), out)
        self.assertEqual(snapshot(self.vault), before)
        self.assertLess(elapsed, 10)

    def test_o15_link_is_left_unchanged(self) -> None:
        """O15, D7, as Feature Folders replaced it: a link in the board folder: link and target unchanged, `link`
        listed. Rows: the `Dev Loop Board` folder is a link; an own note path is a link."""

        def board_folder_link() -> tuple[Path, Path, dict]:
            self.worktree("tiny", prefix="fix")
            target = self.root / "elsewhere-board"
            target.mkdir()
            self.board_dir.symlink_to(target)
            out = self.ok()
            self.assertEqual(list(target.iterdir()), [])
            return self.board_dir, target, out

        def note_link() -> tuple[Path, Path, dict]:
            wt = self.worktree("tiny", prefix="fix")
            self.ok()
            path = self.own_notes()["tiny"]
            path.unlink()
            target = self.root / "outside.md"
            target.write_text("outside\n")
            path.symlink_to(target)
            out = self.event("stop", wt)
            self.assertEqual(target.read_text(), "outside\n")
            return path, target, out

        for n, (name, run) in enumerate((("board folder link", board_folder_link), ("note link", note_link))):
            with self.subTest(name):
                if n:
                    self.setUp()  # a fresh fixture per row, removed by doCleanups below
                try:
                    link, target, out = run()
                    self.assertTrue(link.is_symlink())
                    self.assertEqual(os.readlink(link), str(target))
                    self.assertTrue(self.skipped(out, "link"), out)
                finally:
                    self.doCleanups()

    def test_o16_link_above_the_board_folder_works(self) -> None:
        """O16, D7, as Feature Folders replaced it: a notes path through a link above `Dev Loop Board` works."""
        self.worktree("tiny", prefix="fix")
        link = self.root / "vault-link"
        link.symlink_to(self.vault)
        env = {**self.env, "DEV_LOOP_NOTES": str(link / "Projects" / "Acme Widgets")}
        out = self.ok(env=env)
        self.assertFalse(self.skipped(out, "link"), out)
        self.assertIn("tiny", self.own_notes())

    def test_o17_every_frontmatter_value_is_json(self) -> None:
        """O17: each frontmatter value of a status note parses as JSON, and the schema 1 keys are there."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_notes()["gadget"]
        lines = path.read_text().splitlines()
        end = lines.index("---", 1)
        for line in lines[1:end]:
            key, _, value = line.partition(":")
            with self.subTest(key):
                json.loads(value.strip())
        note = read_note(path)
        keys = {"type", "schema", "project", "feature", "title", "description", "machine_id", "owner", "machine",
                "track", "stage", "stage_times", "tasks", "waiting", "decisions", "assumptions_baseline",
                "shipped_at", "updated"}
        self.assertLessEqual(keys, set(note))
        self.assertEqual((note["feature"], note["title"], note["track"], note["stage"]), ("gadget", "Gadget Export", "M", 5))
        self.assertEqual((note["owner"], note["machine"]), (MACHINE["owner"], MACHINE["machine"]))
        self.assertEqual(note["description"], "Gadget Export for the widget shop.")
        self.assertRegex(note["updated"], ISO_Z)

    def test_note_carries_the_spec_acceptance_value(self) -> None:
        """A spec `acceptance:` value goes into the note in lower case, empty when the spec has none."""
        spec = self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        self.assertEqual(self.note("gadget")["acceptance"], "")
        put(spec, spec.read_text().replace("track: M\n", "track: M\nacceptance: None\n", 1))
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual((note["acceptance"], note["stage"]), ("none", 4))

    @slow
    def test_o18_second_run_keeps_stage_times(self) -> None:
        """O18, D13: a second run keeps every stage_times value of the first."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan)  # Board Robustness D6: Stage 5 has a time only once the ledger exists.
        self.ok(cwd=wt)
        first = self.note("gadget")["stage_times"]
        self.assertIn("5", first)
        time.sleep(1.1)
        self.ok(cwd=wt)
        second = self.note("gadget")["stage_times"]
        for stage, at in first.items():
            self.assertEqual(second[stage], at)

    def test_o19_task_times_come_from_the_files(self) -> None:
        """O19, superseded by Board Robustness oracles 11 to 13: Task 1 `done` is the mtime of its tests log,
        Task 2 `done` the committer date of <b> in its `complete` line, Task 3 `started` the birth time of its
        brief. On macOS a utime earlier than the birth time moves the birth time back too."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        folder = wt / ".superpowers" / "sdd" / plan.stem
        first = git(wt, "rev-parse", "--short", "HEAD").strip()
        put(wt / "gadget.txt", "export\n")
        git(wt, "add", "gadget.txt")
        subprocess.run([*GIT, "commit", "-q", "-m", "gadget"], cwd=wt, check=True, capture_output=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": "2026-10-03T10:20:30+02:00"})
        last = git(wt, "rev-parse", "--short", "HEAD").strip()
        now = int(time.time())
        put(folder / "task-1-tests.log", "ok\n")
        os.utime(folder / "task-1-tests.log", (now - 7200, now - 7200))
        put(folder / "task-3-brief.md", "# Task 3\n")
        os.utime(folder / "task-3-brief.md", (now - 3600, now - 3600))
        self.ledger(wt, plan, "Task 1: complete", f"Task 2: complete (commits {first}..{last}, tests green)")
        st = (folder / "task-3-brief.md").stat()
        brief = getattr(st, "st_birthtime", st.st_mtime)
        self.ok(cwd=wt)
        tasks = self.note("gadget")["tasks"]
        self.assertEqual([t["state"] for t in tasks], ["done", "done", "doing"])
        at = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        self.assertLess(abs(at(tasks[0]["done"]) - (now - 7200)), 1)
        self.assertEqual(at(tasks[1]["done"]), at("2026-10-03T08:20:30Z"))
        self.assertLess(abs(at(tasks[2]["started"]) - brief), 1)

    def test_o23_decisions_are_assumptions_added_after_stage_3(self) -> None:
        """O23, D18, amended by session-b-autonomy: decisions lists the Assumptions items added after the first
        run at Stage 3 or later, then each `decision` row of `## Decision log` as `<Decision>: <Why>`, with the
        prefix `Risk: ` on a `risk` row."""
        spec = self.spec("gadget", "Gadget Export", assumptions=("Widgets are blue.", "Exports are CSV."))
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual(note["decisions"], [])
        self.assertEqual(len(note["assumptions_baseline"]), 2)
        spec.write_text(spec.read_text() + "- Retries use a queue.\n")
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual(len(note["decisions"]), 1)
        self.assertIn("Retries use a queue.", note["decisions"][0])
        self.assertEqual(len(note["assumptions_baseline"]), 2)
        spec.write_text(spec.read_text() + "\n## Decision log\n\n"
                        "| Time | Stage | Session | Kind | Risk | Decision | Why | Evidence | Result |\n"
                        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                        "| 2026-10-02T09:00Z | 5 | 01234567 | decision |  | Use JSON | stdlib only | src/a.py:3 |  |\n"
                        "| 2026-10-02T09:05Z | 5 | 01234567 | decision | risk | Drop cache | too slow | bench.log |  |\n"
                        "| 2026-10-02T09:10Z | 5 | 01234567 | discovery |  | Parser drops tabs | tabs in input | t.py:1 |  |\n")
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual(len(note["decisions"]), 3, note["decisions"])
        self.assertIn("Retries use a queue.", note["decisions"][0])
        self.assertEqual(note["decisions"][1:], ["Use JSON: stdlib only", "Risk: Drop cache: too slow"])
        self.assertEqual(len(note["assumptions_baseline"]), 2)

    def test_o27_note_of_a_feature_that_left_the_write_set_is_deleted(self) -> None:
        """O27, D11: an own note of a feature that left the write set and is not shipped is deleted."""
        self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        path = self.own_notes()["gadget"]
        git(self.repo, "worktree", "remove", "--force", str(wt))
        out = self.ok()
        self.assertFalse(path.exists())
        self.assertIn(str(path), [str(Path(p).resolve()) for p in out["removed"]])

    @slow
    def test_o28_writes_only_in_its_own_board_folder(self) -> None:
        """O28, D1, as Feature Folders replaced it: no write outside <notes>/Dev Loop Board/ of its own project."""
        other = self.vault / "Projects" / "Other Thing" / "dev-loop-board"
        put(other / "zephyr.md", note_text(status_note(project="other-thing", feature="zephyr")))
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.spec("beta", "Beta Import", review="draft")
        self.worktree("tiny", prefix="fix")
        before = {k: v for k, v in snapshot(self.root).items() if not k.startswith("remotes")}
        self.ok("--claim", "beta")
        self.ok()
        self.event("stop", wt)
        self.ledger(wt, plan, "Task 1: complete")
        self.event("prompt", wt)
        self.event("stop", self.repo)
        after = {k: v for k, v in snapshot(self.root).items() if not k.startswith("remotes")}
        own = str(self.board_dir.relative_to(self.root)) + os.sep
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        for name in ("progress.md", "plan-path"):
            changed.discard(str((wt / ".superpowers" / "sdd" / plan.stem / name).relative_to(self.root)))
        self.assertTrue(changed)
        self.assertEqual({k for k in changed if not k.startswith(own)}, set())

    def test_o29_no_temporary_file_stays(self) -> None:
        """O29, D7: no temp file of the run stays; own temp files older than 1 hour are deleted."""
        self.worktree("tiny", prefix="fix")
        self.board_dir.mkdir(parents=True)
        old = self.board_dir / f".{note_name(MACHINE['id'], PROJECT, 'tiny')}.4242.tmp"
        old.write_text("partial\n")
        stamp = time.time() - 7200
        os.utime(old, (stamp, stamp))
        self.ok()
        self.assertEqual([p.name for p in self.board_dir.iterdir() if p.name.endswith(".tmp")], [])

    def test_o30_board_note_written_when_missing_or_older_only(self) -> None:
        """O30, D24: Dev Loop Board.md is written when missing or older, not when same or newer."""
        self.worktree("tiny", prefix="fix")
        self.ok()
        path = self.board_dir / BOARD_NOTE
        self.assertTrue(path.is_file())
        self.assertEqual(frontmatter_version(path), VERSION)
        path.write_text('---\nversion: "0.0.1"\n---\n\nold body marker\n')
        self.ok()
        self.assertEqual(frontmatter_version(path), VERSION)
        self.assertNotIn("old body marker", path.read_text())
        for version in (VERSION, "99.0.0"):
            with self.subTest(version):
                path.write_text(f'---\nversion: "{version}"\n---\n\nkept body marker\n')
                before = (path.read_bytes(), path.stat().st_mtime_ns)
                self.ok()
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_o31_board_note_holds_no_status_data(self) -> None:
        """O31, D24: Dev Loop Board.md holds code only: a dataviewjs block with renderBoard, no status data."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.event("stop", wt)
        text = (self.board_dir / BOARD_NOTE).read_text()
        self.assertIn("```dataviewjs", text)
        self.assertIn("renderBoard", text)
        for value in ("gadget", "Gadget Export", MACHINE["id"], MACHINE["machine"], MACHINE["owner"], "reply in the session"):
            self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()

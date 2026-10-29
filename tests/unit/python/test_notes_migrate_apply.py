"""notes_migrate: the migration texts and the apply run."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from notes_migrate import apply, texts
from support import SKILL_ROOT, git_in, make_git_repo, write
from test_notes_migrate import DEMO, DEMO_MOVES

DEVLOOP = SKILL_ROOT / "scripts" / "devloop.py"
SHA = "a" * 40


class TextPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tmp = tmp.resolve()
        self.repo = make_git_repo(self.tmp, {"dev-loop.json": "{}\n"})
        self.notes = self.tmp / "notes"
        self.notes.mkdir()

    def put(self, files: dict[str, str], root: Path | None = None) -> None:
        for rel, text in files.items():
            path = (root or self.notes) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode())

    def build(self) -> texts.Plan:
        before = self.snapshot()
        built = texts.build_plan(self.repo, self.notes, "2.0.0")
        self.assertEqual(self.snapshot(), before)
        return built

    def snapshot(self) -> dict[str, bytes]:
        return {str(p): p.read_bytes() for p in sorted(self.tmp.rglob("*"))
                if p.is_file() and ".git" not in p.parts and os.access(p, os.R_OK)}

    def written(self, built: texts.Plan) -> dict[str, str]:
        return {w.path.relative_to(self.notes).as_posix(): w.text for w in built.writes}

    def test_frontmatter_roles(self) -> None:
        self.put({
            **DEMO,
            "plans/20261021 Demo Work.md": "---\nspec: 20261020 Demo Work.md\nowner: me\n---\nbody\n",
            "plans/20261020 Early.md": "---\nspec: 20261020 Demo Work\n---\n",
            "plans/20261013 Orphan.md": "# o\n",
            "Project Note.md": "[[specs/20261020 Demo Work]]\n",
        })
        built = self.build()
        self.assertEqual(self.written(built), {
            "Project Note.md": "[[Features/20261019 Demo Work/Spec|specs/20261020 Demo Work]]\n",
            "Features/20261019 Demo Work/Spec.md": "---\nfeature: demo-work\ntype: spec\n---\n# Demo\n",
            "Features/20261019 Demo Work/Plan.md":
                "---\nspec: Spec.md\nowner: me\ntype: plan\nfeature: demo-work\n---\nbody\n",
            "Features/20261019 Demo Work/20261020 Early.md": "---\nspec: Spec.md\ntype: plan\nfeature: demo-work\n---\n",
            "Features/20261019 Demo Work/Framing.md": "---\ntype: framing\nfeature: demo-work\n---\n",
            "Features/20261013 Orphan/Plan.md": "---\ntype: plan\nfeature: orphan\n---\n# o\n",
        })
        self.assertEqual(sorted(built.frontmatter), sorted(set(self.written(built)) - {"Project Note.md"}))
        sources = {m.dst: m.src for m in built.moves}
        for write_ in built.writes:
            old = sources.get(write_.path, write_.path).read_bytes()
            self.assertEqual((write_.old_sha, write_.new_sha),
                             (hashlib.sha256(old).hexdigest(), hashlib.sha256(write_.text.encode()).hexdigest()))

    def test_frontmatter_crlf_and_bom_kept(self) -> None:
        self.put({
            "specs/20261020 Demo Work.md": "\ufeff---\r\nfeature: demo-work\r\n---\r\n# Demo\r\n",
            "plans/20261013 Orphan.md": "\ufeff# o\r\n",
        })
        self.assertEqual(self.written(self.build()), {
            "Features/20261020 Demo Work/Spec.md": "\ufeff---\r\nfeature: demo-work\r\ntype: spec\r\n---\r\n# Demo\r\n",
            "Features/20261013 Orphan/Plan.md": "\ufeff---\r\ntype: plan\r\nfeature: orphan\r\n---\r\n# o\r\n",
        })

    def test_unclosed_frontmatter_left_alone(self) -> None:
        self.put({"specs/20261020 Demo Work.md": "---\nfeature: demo-work\n# Demo\n"})
        built = self.build()
        self.assertEqual((len(built.moves), built.writes), (1, []))

    def test_frontmatter_drops_continuation_of_replaced_key(self) -> None:
        self.put({
            "specs/20261020 Demo Work.md": "---\nfeature: demo-work\ntype:\n  - x\n---\n",
            "plans/20261021 Demo Work.md": '---\nfeature: demo-work\nspec:\n  - "[[20261020 Demo Work]]"\n- y\nowner: me\n---\n',
        })
        self.assertEqual(self.written(self.build()), {
            "Features/20261020 Demo Work/Spec.md": "---\nfeature: demo-work\ntype: spec\n---\n",
            "Features/20261020 Demo Work/Plan.md": "---\nfeature: demo-work\nspec: Spec.md\nowner: me\ntype: plan\n---\n",
        })

    def test_board_removal_maps_nothing(self) -> None:
        status = "---\ntype: dev-loop-status\n---\n"
        self.put({"dev-loop-board/c.md": status, "dev-loop-board/features/c.md": status,
                  "Project Note.md": "[[dev-loop-board/c]] [[dev-loop-board/features/c]]\n"})
        os.utime(self.notes / "dev-loop-board/c.md", (100, 100))
        os.utime(self.notes / "dev-loop-board/features/c.md", (200, 200))
        self.assertEqual(self.written(self.build()), {
            "Project Note.md": "[[dev-loop-board/c]] [[Dev Loop Board/Features/c|dev-loop-board/features/c]]\n",
        })

    def test_link_above_notes_is_fine(self) -> None:
        real = self.tmp / "real"
        rows = [(self.tmp / "vault", self.tmp / "vault/Software", real / "Software", "Software/Notes"),
                (self.tmp / "vault2", self.tmp / "vault2/Notes", real / "notes", "Notes")]
        for vault, link, target, notes in rows:
            with self.subTest(link=link.name):
                shutil.rmtree(real, ignore_errors=True)
                (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
                target.mkdir(parents=True)
                link.symlink_to(target)
                self.notes = vault / notes
                self.put({**DEMO, "Project Note.md": "[[specs/20261020 Demo Work]]\n"})
                built = self.build()
                self.assertEqual(built.blocked, [])
                self.assertIn("Project Note.md", self.written(built))
                self.assertEqual(len(built.writes), 4)

    def test_audit_ids(self) -> None:
        project = (
            "## Release audits\n"
            f"- 2026-10-25 run-3 at {SHA}: confirmed 1\n"
            "## Deferred\n"
            "### Release audit run-3, 2026-10-25\n"
            "- 2026-10-25 (security, confirmed, run-3): x\n"
            "- 2026-10-25 (security, needs-validation, release-security-audit): y\n"
            "- 2026-10-25 (security, hardening, run-3): see security-audit/run-3/REPORT.md\n"
            "keep `security-audit/run-3/` and my-security-audit/run-3/\n"
            "- 2026-10-26 (security, hardening, run-4)\n"
            "plain run-5\n"
        )
        gardener = (
            "- 2026-10-25 (security, hardening, run-3): security-audit/run-3/REPORT.md\n"
            "[[security-audit/run-3/REPORT]] [[security-audit/run-3/REPORT|security-audit/run-3/]]\n"
        )
        self.put({"Project Note.md": project, "Gardener Log.md": gardener,
                  "security-audit/run-3/REPORT.md": "see security-audit/run-3/x\n"})
        built = self.build()
        self.assertEqual(self.written(built), {
            "Project Note.md": (
                "## Release audits\n"
                f"- 2026-10-25 Run 3 at {SHA}: confirmed 1\n"
                "## Deferred\n"
                "### Release audit Run 3, 2026-10-25\n"
                "- 2026-10-25 (security, confirmed, Run 3): x\n"
                "- 2026-10-25 (security, needs-validation, release-security-audit): y\n"
                "- 2026-10-25 (security, hardening, Run 3): see Security Audit/Run 3/REPORT.md\n"
                "keep `security-audit/run-3/` and my-security-audit/run-3/\n"
                "- 2026-10-26 (security, hardening, Run 4)\n"
                "plain run-5\n"
            ),
            "Gardener Log.md": (
                "- 2026-10-25 (security, hardening, run-3): Security Audit/Run 3/REPORT.md\n"
                "[[Security Audit/Run 3/REPORT|security-audit/run-3/REPORT]] "
                "[[Security Audit/Run 3/REPORT|Security Audit/Run 3/]]\n"
            ),
        })
        self.assertEqual(built.audit_ids, 8)

    def test_outside_links_and_skipped(self) -> None:
        vault = self.tmp / "vault"
        self.notes = vault / "Notes"
        (vault / ".obsidian").mkdir(parents=True)
        self.put({**DEMO, "Project Note.md": "[[specs/20261020 Demo Work]]\n"})
        self.put({"Other/Note.md": "a\n[[specs/20261020 Demo Work]] [[Elsewhere]]\n", "Other/.Evicted.md.icloud": "",
                  "Other/.img.png.icloud": ""}, vault)
        built = self.build()
        self.assertEqual(built.outside_links, [{"file": "Other/Note.md", "line": 2, "link": "[[specs/20261020 Demo Work]]"}])
        self.assertEqual((built.outside_skipped, built.blocked), (["Other/.Evicted.md.icloud"], []))
        self.assertEqual(built.links, {"files": ["Project Note.md"], "count": 1})
        self.assertNotIn("Other/Note.md", [w.path.relative_to(vault).as_posix() for w in built.writes])

    def test_unreadable_blocks_inside_skips_outside(self) -> None:
        vault = self.tmp / "vault"
        self.notes = vault / "Notes"
        (vault / ".obsidian").mkdir(parents=True)
        self.put({**DEMO, "specs/20261022 Locked.md": "# l\n"})
        (self.notes / "Broken.md").symlink_to(self.tmp / "missing.md")
        (vault / "Other").mkdir()
        (vault / "Other/Broken.md").symlink_to(self.tmp / "missing.md")
        locked = self.notes / "specs/20261022 Locked.md"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o644)
        built = self.build()
        self.assertEqual(sorted(built.blocked), [("Broken.md", "unreadable"), ("specs/20261022 Locked.md", "unreadable")])
        self.assertEqual(built.outside_skipped, ["Other/Broken.md"])

    def test_outside_scan_skips_dot_folders(self) -> None:
        vault = self.tmp / "vault"
        self.notes = vault / "Notes"
        self.put(DEMO)
        self.put({".obsidian/app.json": "{}", ".trash/Old.md": "[[specs/20261020 Demo Work]]\n",
                  ".trash/.Gone.md.icloud": ""}, vault)
        built = self.build()
        self.assertEqual((built.outside_links, built.outside_skipped), ([], []))

    def test_linked_note_blocks(self) -> None:
        self.put(DEMO)
        elsewhere = self.tmp / "elsewhere"
        self.put({"Linked.md": "[[specs/20261020 Demo Work]]\n", "Plain.md": "no link\n",
                  "shared/Note.md": "[[specs/20261020 Demo Work]]\n"}, elsewhere)
        (self.notes / "Linked.md").symlink_to(elsewhere / "Linked.md")
        (self.notes / "Plain.md").symlink_to(elsewhere / "Plain.md")
        (self.notes / "Shared").symlink_to(elsewhere / "shared")
        built = self.build()
        self.assertEqual(sorted(built.blocked), [("Linked.md", "link"), ("Shared/Note.md", "link")])
        self.assertEqual([w for w in built.writes if w.path.parent == self.notes or "Shared" in w.path.parts], [])

    def test_repo_links(self) -> None:
        self.put({**DEMO, "maps/release-cycle/map.md": "m\n"})
        lines = [
            ("x", False),
            ("see `maps/release-cycle/map.md`.", True),
            ("see specs/20261020 Demo Work.md, then", True),
            ("[[specs/20261020 Demo Work]]", True),
            ("<plans/20261021 Demo Work.md>", True),
            ("- framing/20261019 demo-work.md:", True),
            ("[m](maps/)", True),
            ("source maps. the answers", False),
            ("roadmaps/release-cycle/x and maps-old/y and specs/20261020 Demo Works", False),
        ]
        write(self.repo / "docs/agents/issue-tracker.md", "".join(line + "\n" for line, _ in lines))
        write(self.repo / "tests/acceptance/x.md", "maps/release-cycle/map.md\n")
        (self.repo / "docs/blob.bin").write_bytes(b"\xff\xfe maps/release-cycle/\n")
        git_in(self.repo, "add", "-A")
        self.assertEqual(self.build().repo_links, [{"file": "docs/agents/issue-tracker.md", "line": n}
                                                   for n, (_, hit) in enumerate(lines, 1) if hit])

    def test_git_case(self) -> None:
        both = ["git rm -r --cached answers", "git add Answers", "git rm -r --cached maps", "git add Maps"]
        for tracked, expected in [(["answers"], both[:2]), (["answers", "maps"], both)]:
            with self.subTest(tracked=tracked):
                shutil.rmtree(self.notes)
                self.put({"answers/20261020 Q.md": "a\n", "maps/auth/map.md": "m\n"})
                git_in(self.notes, "init", "-q")
                git_in(self.notes, "config", "core.ignorecase", "true")
                git_in(self.notes, "add", *tracked)
                index = git_in(self.notes, "ls-files", "--stage")
                self.assertEqual(self.build().git_case, expected)
                self.assertEqual(git_in(self.notes, "ls-files", "--stage"), index)

    def test_plan_hash_changes_with_a_new_spec(self) -> None:
        self.put(DEMO)
        first = self.build().plan_hash
        self.assertEqual(self.build().plan_hash, first)
        self.put({"specs/20261022 New.md": "# n\n"})
        self.assertNotEqual(self.build().plan_hash, first)


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tmp = tmp.resolve()
        self.repo = make_git_repo(self.tmp, {"dev-loop.json": "{}\n"})
        self.notes = self.tmp / "notes"
        self.notes.mkdir()
        self.log = f"{time.strftime('%Y%m%d')} Notes Migration.md"
        self.folder = self.notes / "Features/20261019 Demo Work"
        self.home = self.tmp / "home"

    def put(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            write(self.notes / rel, text)

    def tree(self) -> dict[str, bytes]:
        return {p.relative_to(self.tmp).as_posix(): p.read_bytes() for p in sorted(self.tmp.rglob("*"))
                if p.is_file() and ".git" not in p.parts}

    def env(self) -> dict[str, str]:
        return {"DEV_LOOP_NOTES": str(self.notes), "HOME": str(self.home)}

    def run_cli(self, *args: str) -> tuple[dict, int]:
        done = subprocess.run([sys.executable, str(DEVLOOP), "notes-migrate", "--repo", str(self.repo), *args],
                              capture_output=True, text=True, env={**os.environ, **self.env()})
        return json.loads(done.stdout), done.returncode

    def call(self, plan: str, *patches) -> tuple[dict, int]:
        args = SimpleNamespace(repo=str(self.repo), apply=True, plan=plan)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, self.env()))
            for patch in patches:
                stack.enter_context(patch)
            out = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code = apply.cmd_notes_migrate(args)
        return json.loads(out.getvalue()), code

    def stopped_at_plan(self) -> str:
        """An `--apply` stopped at the `Plan.md` write."""
        self.put({**DEMO, "Project Note.md": "[[specs/20261020 Demo Work]]\n",
                  "specs/20261020 Demo Work.md": "---\nfeature: demo-work\n---\n[note](<../Project Note.md>)\n"})
        planned = self.run_cli()[0]["plan_hash"]
        real = os.replace

        def stop(src, dst):
            if Path(dst).name == "Plan.md":
                raise OSError("stopped")
            real(src, dst)

        out, code = self.call(planned, mock.patch("os.replace", stop))
        self.assertEqual((code, out["error"]), (1, "io"))
        return planned

    def test_dry_run_changes_nothing(self) -> None:
        self.put(DEMO)
        before = self.tree()
        out, code = self.run_cli()
        self.assertEqual(self.tree(), before)
        self.assertEqual((code, out["applied"]), (0, False))
        self.assertEqual(list(out), "applied plan_hash moves frontmatter links audit_ids outside_links outside_skipped "
                         "repo_links git_case gitignore ambiguous blocked log error".split())
        self.assertRegex(out["plan_hash"], "^[0-9a-f]{64}$")
        self.assertEqual(out["moves"], [{"from": src, "to": dst} for src, dst, _, _ in DEMO_MOVES])

    def test_apply_with_plan(self) -> None:
        self.put(DEMO)
        out, code = self.run_cli("--apply", "--plan", self.run_cli()[0]["plan_hash"])
        self.assertEqual((code, out["applied"], out["error"], out["log"]), (0, True, None, self.log))
        self.assertEqual(sorted(os.listdir(self.notes)), sorted(["Features", self.log]))
        self.assertEqual(sorted(os.listdir(self.folder)), ["Framing.md", "Plan.md", "Spec.md"])
        self.assertEqual((self.home / ".config/dev-loop/notes-migrate.key").stat().st_mode & 0o777, 0o600)
        log =(self.notes / self.log).read_text()
        self.assertTrue(log.startswith("---\ntype: note\n---\n"))
        for src, dst, _, _ in DEMO_MOVES:
            self.assertIn(f"`{src}` to `{dst}`", log)

    def test_plan_changed(self) -> None:
        self.put(DEMO)
        planned = self.run_cli()[0]["plan_hash"]
        self.put({"specs/20261022 New.md": "# n\n"})
        before = self.tree()
        out, code = self.run_cli("--apply", "--plan", planned)
        self.assertEqual((code, out["error"], out["applied"]), (1, "plan-changed", False))
        self.assertIn(planned, out["message"])
        self.assertEqual(self.tree(), before)

    def test_apply_needs_plan(self) -> None:
        self.put(DEMO)
        before = self.tree()
        self.assertEqual(self.run_cli("--apply")[1], 2)
        self.assertEqual(self.tree(), before)

    def test_resume_from_journal(self) -> None:
        for extra in ([], ["--plan"]):
            with self.subTest(plan=bool(extra)):
                shutil.rmtree(self.notes)
                self.notes.mkdir()
                planned = self.stopped_at_plan()
                folder = self.folder
                framing = (folder / "Framing.md").read_bytes()
                out, code = self.run_cli("--apply", *extra, *([planned] if extra else []))
                self.assertEqual((code, out["applied"], out["plan_hash"]), (0, True, planned))
                self.assertEqual((folder / "Framing.md").read_bytes(), framing)
                self.assertIn("spec: Spec.md", (folder / "Plan.md").read_text())
                self.assertIn("[note](<../../Project Note.md>)", (folder / "Spec.md").read_text())
                self.assertEqual((self.notes / "Project Note.md").read_text(),
                                 "[[Features/20261019 Demo Work/Spec|specs/20261020 Demo Work]]\n")
                self.assertEqual(sorted(os.listdir(self.notes)), sorted(["Features", "Project Note.md", self.log]))

    def test_other_host_journal_locked(self) -> None:
        planned = self.stopped_at_plan()
        journal = self.notes / ".notes-migrate.json"
        journal.write_text(json.dumps({**json.loads(journal.read_text()), "host": "other-host"}))
        before = self.tree()
        out, code = self.run_cli("--apply", "--plan", planned)
        self.assertEqual((code, out["error"]), (1, "locked"))
        self.assertIn("other-host", out["message"])
        self.assertEqual(self.tree(), before)

    def test_tampered_journal_refused(self) -> None:
        planned = self.stopped_at_plan()
        secret = self.tmp / "outside/secret.md"
        write(secret, "secret\n")
        (self.notes / "Features/out").symlink_to(secret.parent)
        path = self.notes / ".notes-migrate.json"
        journal = json.loads(path.read_text())
        first = journal["moves"][0]

        def forged(dst: str) -> dict:
            moves = [{**first, "dst": dst}, *journal["moves"][1:]]
            notes = self.notes
            return {**journal, "moves": moves, "plan_hash": texts.plan_hash(
                [(notes / m["src"], notes / m["dst"]) for m in moves],
                [(notes / w["path"], w["old"], w["new"]) for w in journal["writes"]])}

        rows = [
            ("not a dict", [], "io"),
            ("move without role", {**journal, "moves": [{k: v for k, v in first.items() if k != "role"}]}, "io"),
            ("absolute source", {**journal, "moves": [{**first, "src": str(secret)}]}, "io"),
            ("dot-dot target", {**journal, "moves": [{**first, "dst": "../outside/k.md"}]}, "io"),
            ("dot-dot write", {**journal, "writes": [{**journal["writes"][0], "path": "Features/../../outside/secret.md"}]},
             "io"),
            ("absolute log", {**journal, "log": str(secret)}, "io"),
            ("symlinked parent", {**journal, "moves": [{**first, "dst": "Features/out/k.md"}]}, "io"),
            ("plan hash not of the steps", {**journal, "plan_hash": planned[::-1]}, "io"),
            ("forged steps and hash", forged("Features/Evil.md"), "locked"),
            ("bad hmac", {**journal, "hmac": "0" * 64}, "locked"),
            ("no hmac", {k: v for k, v in journal.items() if k != "hmac"}, "locked"),
        ]
        for name, planted, error in rows:
            path.write_text(json.dumps(planted))
            before = self.tree()
            for args in ([], ["--apply"], ["--apply", "--plan", planned]):
                with self.subTest(name, args=args):
                    out, code = self.run_cli(*args)
                    self.assertEqual((code, out["error"]), (1, error))
                    self.assertEqual(self.tree(), before)

    def test_journal_after_lock_checked(self) -> None:
        self.put(DEMO)
        planned = self.run_cli()[0]["plan_hash"]
        real = apply._lock

        def planted(path: Path) -> str | None:
            (self.notes / ".notes-migrate.json").write_text("[]")
            return real(path)

        out, code = self.call(planned, mock.patch.object(apply, "_lock", planted))
        self.assertEqual((code, out["error"]), (1, "io"))
        self.assertEqual(sorted(os.listdir(self.notes)), [".notes-migrate.json", "framing", "plans", "specs"])

    def test_case_variant_synced_in_after_plan(self) -> None:
        self.put(DEMO)
        planned = self.run_cli()[0]["plan_hash"]
        real = os.rename

        def synced(src, dst):
            real(src, dst)
            if Path(dst).name == "Spec.md":
                write(self.folder / "plan.md", "keep")

        out, code = self.call(planned, mock.patch("os.rename", synced))
        self.assertEqual((code, out["error"]), (1, "io"))
        self.assertEqual((self.folder / "plan.md").read_text(), "keep")
        self.assertTrue((self.notes / "plans/20261021 Demo Work.md").is_file())

    def test_journal_write_error_is_json(self) -> None:
        self.put(DEMO)
        planned = self.run_cli()[0]["plan_hash"]
        before = self.tree()

        def fail(src, dst):
            raise OSError("disk full")

        out, code = self.call(planned, mock.patch("os.replace", fail))
        self.assertEqual((code, out["error"]), (1, "io"))
        self.assertIn("disk full", out["message"])
        self.assertEqual({k: v for k, v in self.tree().items() if not k.startswith("home/")}, before)

    def test_resume_after_log_writes_one_log(self) -> None:
        self.put(DEMO)
        planned = self.run_cli()[0]["plan_hash"]
        real = Path.unlink

        def stop(path: Path, *args, **kwargs) -> None:
            if path.name == ".notes-migrate.json":
                raise OSError("stopped")
            real(path, *args, **kwargs)

        self.assertEqual(self.call(planned, mock.patch.object(Path, "unlink", stop))[0]["error"], "io")
        out, code = self.run_cli("--apply")
        self.assertEqual((code, out["log"]), (0, self.log))
        self.assertEqual(sorted(os.listdir(self.notes)), sorted(["Features", self.log]))

    def test_stale_lock_removed(self) -> None:
        self.put(DEMO)
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()
        (self.notes / ".notes-migrate.lock").write_text(json.dumps({"host": socket.gethostname(), "pid": dead.pid}))
        out, code = self.run_cli("--apply", "--plan", self.run_cli()[0]["plan_hash"])
        self.assertEqual((code, out["applied"]), (0, True))
        self.assertNotIn(".notes-migrate.lock", os.listdir(self.notes))

    def test_changed_note_io(self) -> None:
        planned = self.stopped_at_plan()
        spec = self.folder / "Spec.md"
        spec.write_text("changed\n")
        out, code = self.run_cli("--apply", "--plan", planned)
        self.assertEqual((code, out["error"]), (1, "io"))
        self.assertIn("Features/20261019 Demo Work/Spec.md", out["message"])
        self.assertEqual(spec.read_text(), "changed\n")
        self.assertTrue((self.notes / ".notes-migrate.json").is_file())

    def test_second_apply_noop(self) -> None:
        self.put(DEMO)
        self.run_cli("--apply", "--plan", self.run_cli()[0]["plan_hash"])
        before = self.tree()
        out, code = self.run_cli("--apply", "--plan", self.run_cli()[0]["plan_hash"])
        self.assertEqual((code, {k: v for k, v in out.items() if v not in ([], 0, None)}),
                         (0, {"applied": True, "plan_hash": out["plan_hash"], "links": {"files": [], "count": 0}}))
        self.assertEqual(self.tree(), before)

    def test_second_log_same_day(self) -> None:
        self.put(DEMO)
        self.run_cli("--apply", "--plan", self.run_cli()[0]["plan_hash"])
        first = (self.notes / self.log).read_bytes()
        self.put({"answers/20261020 Q.md": "a\n"})
        out, code = self.run_cli("--apply", "--plan", self.run_cli()[0]["plan_hash"])
        second = self.log.replace(".md", " 2.md")
        self.assertEqual((code, out["log"]), (0, second))
        self.assertEqual((self.notes / self.log).read_bytes(), first)
        self.assertIn("`answers` to `Answers`", (self.notes / second).read_text())

    def test_notes_in_repo(self) -> None:
        self.notes = self.repo / "notes"
        self.put(DEMO)
        before = self.tree()
        out, code = self.run_cli()
        self.assertEqual((code, out["error"]), (1, "notes-in-repo"))
        self.assertEqual(self.tree(), before)

    def test_blocked_changes_nothing(self) -> None:
        self.put({**DEMO, "specs/img.png": "x"})
        before = self.tree()
        out, code = self.run_cli()
        self.assertEqual((code, out["blocked"]), (1, [{"path": "specs/img.png", "reason": "unknown-entry"}]))
        out, code = self.run_cli("--apply", "--plan", out["plan_hash"])
        self.assertEqual((code, out["applied"]), (1, False))
        self.assertEqual(self.tree(), before)


if __name__ == "__main__":
    unittest.main()

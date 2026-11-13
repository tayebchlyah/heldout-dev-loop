"""The Codex kit copy fill: staged bytes checked against the MANIFEST, one complete copy."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import PACKAGE_ROOT

FILL = PACKAGE_ROOT / "lib/codex_fill.py"
HOOKS = (".dev-loop/hooks/codex/guard.sh", ".dev-loop/hooks/codex/session.sh")
SCRIPTS = (
    ".agents/skills/dev-loop/scripts/devloop.py",
    ".agents/skills/dev-loop/scripts/board/writer.py",
)
D6 = HOOKS + SCRIPTS
MANIFEST = ".dev-loop/MANIFEST.json"


def load():
    spec = importlib.util.spec_from_file_location("codex_fill", FILL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class CodexFillTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.source = self.tmp / "main checkout"
        self.fill = load()
        self.files = {rel: f"content of {rel}\n".encode() for rel in D6}
        self.files[".agents/skills/dev-loop/SKILL.md"] = b"skill\n"
        entries = {rel: f"sha256:{sha(data)}" for rel, data in self.files.items()}
        entries[".claude/skills/dev-loop"] = "link:../../.agents/skills/dev-loop"
        self.manifest = json.dumps({"version": "x", "files": entries}).encode()
        for rel, data in self.files.items():
            put(self.source, rel, data)
        put(self.source, MANIFEST, self.manifest)
        self.name = sha(self.manifest)
        self.kit = self.tmp / "codex home/dev-loop/kit" / self.name

    def run_main(self, mode: str, source: str | None = None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.fill.main([mode, str(self.kit), str(self.source) if source is None else source])
        return code, out.getvalue(), err.getvalue()

    def parent_names(self) -> list[str]:
        return sorted(p.name for p in self.kit.parent.iterdir())

    def copied(self) -> set[str]:
        return {p.relative_to(self.kit).as_posix() for p in self.kit.rglob("*") if p.is_file()}

    def test_fill_copies_the_manifest_and_d6_entries_only(self) -> None:
        self.assertEqual(self.run_main("block")[0], 0)
        found = self.copied()
        self.assertEqual(found, set(D6) | {MANIFEST})
        for rel in found:
            copy = self.kit / rel
            self.assertEqual(copy.read_bytes(), (self.source / rel).read_bytes())
            self.assertEqual(copy.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.parent_names(), [self.name])

    def test_a_changed_d6_file_blocks_with_its_path(self) -> None:
        for rel in (HOOKS[0], SCRIPTS[0], SCRIPTS[1]):
            with self.subTest(rel=rel):
                (self.source / rel).write_bytes(b"changed\n")
                code, out, err = self.run_main("block")
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(err, self.fill.message(str(self.source), rel) + "\n")
                self.assertFalse(self.kit.exists())
                self.assertEqual(self.parent_names(), [])
                (self.source / rel).write_bytes(self.files[rel])

    def test_a_manifest_listing_the_changed_hash_blocks_on_the_manifest(self) -> None:
        entries = json.loads(self.manifest)["files"]
        entries[SCRIPTS[0]] = f"sha256:{sha(b'changed')}"
        self.manifest = json.dumps({"version": "x", "files": entries}).encode()
        put(self.source, MANIFEST, self.manifest)
        code, _, err = self.run_main("block")
        self.assertEqual(code, 2)
        self.assertEqual(err, self.fill.message(str(self.source), MANIFEST) + "\n")
        self.assertFalse(self.kit.exists())

    def test_no_source_or_no_manifest_blocks_on_the_manifest(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        for source in ("", "relative", str(empty)):
            with self.subTest(source=source):
                code, out, err = self.run_main("block", source)
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(err, self.fill.message(source, MANIFEST) + "\n")
                self.assertFalse(self.kit.exists())

    def test_warn_mode_prints_one_system_message(self) -> None:
        (self.source / SCRIPTS[0]).write_bytes(b"changed\n")
        code, out, err = self.run_main("warn")
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out), {"systemMessage": self.fill.message(str(self.source), SCRIPTS[0])})

    def test_session_mode_prints_the_message_and_the_skill_instruction(self) -> None:
        (self.source / SCRIPTS[0]).write_bytes(b"changed\n")
        code, out, err = self.run_main("session")
        text = self.fill.message(str(self.source), SCRIPTS[0])
        self.assertEqual((code, err), (2, ""))
        self.assertEqual(json.loads(out), {"systemMessage": text, "hookSpecificOutput": {"hookEventName": "SessionStart",
            "additionalContext": f"This repo runs the dev-loop skill. Invoke it before any other action.\n{text}"}})

    def test_a_source_change_after_staging_never_reaches_the_copy(self) -> None:
        original = self.fill.stage

        def staged(source_file: str, target: str) -> bytes:
            data = original(source_file, target)
            Path(source_file).write_bytes(b"evil")
            return data

        self.fill.stage = staged
        self.assertIsNone(self.fill.fill(str(self.kit), str(self.source)))
        for rel in D6:
            self.assertEqual((self.kit / rel).read_bytes(), self.files[rel])

    def test_a_symlinked_source_file_is_copied_as_content(self) -> None:
        target = self.tmp / "elsewhere.py"
        target.write_bytes(self.files[SCRIPTS[0]])
        link = self.source / SCRIPTS[0]
        link.unlink()
        link.symlink_to(target)
        self.assertEqual(self.run_main("block")[0], 0)
        copy = self.kit / SCRIPTS[0]
        self.assertFalse(copy.is_symlink())
        self.assertEqual(copy.read_bytes(), self.files[SCRIPTS[0]])

    def test_an_existing_copy_wins(self) -> None:
        self.kit.mkdir(parents=True)
        (self.kit / "sentinel").write_text("keep")
        self.assertEqual(self.run_main("block")[0], 0)
        self.assertEqual((self.kit / "sentinel").read_text(), "keep")
        self.assertEqual(self.parent_names(), [self.name])

    def test_eight_parallel_fills_leave_one_complete_copy(self) -> None:
        procs = [
            subprocess.Popen([sys.executable, "-I", str(FILL), "block", str(self.kit), str(self.source)])
            for _ in range(8)
        ]
        self.assertEqual([p.wait() for p in procs], [0] * 8)
        self.assertEqual(self.copied(), set(D6) | {MANIFEST})
        self.assertEqual(self.parent_names(), [self.name])

    def test_a_stale_staging_folder_and_a_missing_parent(self) -> None:
        self.assertFalse(self.kit.parent.exists())
        self.assertEqual(self.run_main("block")[0], 0)
        shutil.rmtree(self.kit)
        (self.kit.parent / ".stage-old").mkdir()
        self.assertEqual(self.run_main("block")[0], 0)
        self.assertEqual(self.parent_names(), sorted([self.name, ".stage-old"]))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any file")
    def test_an_unreadable_source_file_blocks_with_its_path(self) -> None:
        path = self.source / SCRIPTS[1]
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        code, _, err = self.run_main("block")
        self.assertEqual(code, 2)
        self.assertEqual(err, self.fill.message(str(self.source), SCRIPTS[1]) + "\n")
        self.assertNotIn("Traceback", err)
        self.assertFalse(self.kit.exists())

    def test_the_file_holds_no_single_quote(self) -> None:
        self.assertNotIn("'", FILL.read_text())


if __name__ == "__main__":
    unittest.main()

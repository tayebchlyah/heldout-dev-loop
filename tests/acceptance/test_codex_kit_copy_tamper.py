#!/usr/bin/env python3
"""Held-out oracle tests for the Codex kit copy (spec: Codex Kit Copy), flow F2: a changed or planted kit.

Each docstring names the `## Acceptance oracle` lines (O<n>) it proves. Seam 1: a scratch project made by
`bin/install.mjs init`, a linked feature worktree, HOME in a temporary folder; each Codex hook command of
`.codex/hooks.json` runs through `sh -c` with hook JSON on stdin. O17 needs the native Codex launcher (codex-cli
0.166.0 or later) and skips without it. Fixtures: `codex_kit_copy_support.py`. Every name is invented.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from codex_kit_copy_support import (BLOCK_PART, D6, ECHO, HOOKS_DIR, MANIFEST, SCRIPTS_DIR, SEARCH, STALE, START,
                                    STOP, TAG_PUSH, Scratch, digest_of, put)

CHANGES = (HOOKS_DIR + "git-guard.sh", SCRIPTS_DIR + "devloop.py", SCRIPTS_DIR + "status.py")
WALLS = (("PreToolUse", "git-guard.sh", TAG_PUSH), ("PreToolUse", "acceptance-wall.sh", SEARCH))
NOTICES = (("SessionStart", "board.sh", START), ("SessionStart", "session-start.sh", START))
PLANT_HOOKS = (("PreToolUse", "git-guard.sh", ECHO), ("PreToolUse", "acceptance-wall.sh", ECHO),
               ("SessionStart", "board.sh", START), ("SessionStart", "session-start.sh", START),
               ("Stop", "seal.sh", STOP), ("Stop", "gate.sh", STOP))


class CodexKitCopyTamperFlow(unittest.TestCase):
    def test_f2_changed_or_planted_kit_never_runs_on_the_host(self) -> None:
        """F2, O3, O4, O5, O6, O11: a changed main kit blocks with no copy and is ignored with one."""
        s = Scratch(self)
        digest = s.manifest_hash()
        for rel in CHANGES:
            self.assertIn(rel, s.manifest_files(), f"{rel} is not a MANIFEST entry")

        for rel in CHANGES:
            with self.subTest(oracle="O3", changed=rel):
                s.change(rel)
                for event, script, fields in WALLS:
                    s.assert_wall_blocks(s.call(event, script, **fields), s.block(rel))
                for event, script, fields in NOTICES:
                    self.assertIn(s.block(rel), s.system_message(s.call(event, script, **fields)), script)
                s.settle()
                self.assertEqual(s.markers(), "", "the changed code ran")
                self.assertEqual(s.kit_children(), [], "a copy or a staging folder is left")
                s.restore()

        baseline = {(event, script): s.result(s.call(event, script, **fields))
                    for event, script, fields in WALLS + (NOTICES[0],)}
        self.assertTrue(s.copy(digest).is_dir(), "the unchanged kit made no copy")
        for rel in CHANGES:
            with self.subTest(oracle="O4, O11", changed=rel):
                s.change(rel)
                for event, script, fields in WALLS + (NOTICES[0],):
                    self.assertEqual(s.result(s.call(event, script, **fields)), baseline[(event, script)], script)
                done = s.call("SessionStart", "session-start.sh", **START)
                self.assertEqual(done.returncode, 0, done.stderr[-1200:])
                lines = [c[c.index(STALE) + len(STALE):] for c in s.contexts(done) if STALE in c]
                self.assertTrue(lines, f"no '{STALE}' line: {done.stdout[-1200:]}")
                self.assertIn(rel, lines[0].splitlines()[0])
                s.settle()
                self.assertEqual(s.markers(), "", "the changed code ran with a copy present")
                s.restore()

        with self.subTest(oracle="O5"):
            s.drop_copy()
            rel = SCRIPTS_DIR + "devloop.py"
            files = s.manifest_files()
            files[rel] = "sha256:" + digest_of(s.change(rel))
            manifest = json.loads((s.main / MANIFEST).read_text())
            manifest["files"] = files
            put(s.main / MANIFEST, json.dumps(manifest, indent=2) + "\n")
            for event, script, fields in WALLS:
                s.assert_wall_blocks(s.call(event, script, **fields), s.block(MANIFEST))
            for event, script, fields in NOTICES:
                self.assertIn(s.block(MANIFEST), s.system_message(s.call(event, script, **fields)), script)
            s.settle()
            self.assertEqual(s.markers(), "")
            self.assertEqual(s.kit_children(), [])
            s.restore()

        with self.subTest(oracle="O6"):
            self.planted_repos_run_the_copy_or_block(s, baseline=s.result(s.call("PreToolUse", "git-guard.sh",
                                                                                   **ECHO)))

    def plant_kit(self, s: Scratch, top: Path, manifest: str | None) -> None:
        """Marker code at each D6 path of main under `top`; `manifest`: `main` (verbatim), `own` (its hashes)."""
        files = s.manifest_files()
        for rel in files:
            if rel.startswith(D6):
                path = top / rel
                put(path, s.marker_code(rel, f"planted {top.name} {rel}"), rel.endswith(".sh"))
        if manifest == "main":
            put(top / MANIFEST, (s.main / MANIFEST).read_text())
        elif manifest == "own":
            for rel in files:
                if rel.startswith(D6):
                    files[rel] = "sha256:" + digest_of((top / rel).read_bytes())
            put(top / MANIFEST, json.dumps({"version": "9.9.9", "files": files}, indent=2) + "\n")

    def planted_repos_run_the_copy_or_block(self, s: Scratch, baseline: tuple[int, str]) -> None:
        """O6: a repo in a feature subfolder, an ancestor gitfile to a planted repo, a planted repo with a kit."""
        sub = s.feature / "pkg"
        sub.mkdir()
        s.git(sub, "init", "-q", "-b", "main")
        self.plant_kit(s, sub, None)

        gitfile_target = s.tmp / "planted gitfile repo"
        gitfile_target.mkdir()
        s.git(gitfile_target, "init", "-q", "-b", "main")
        self.plant_kit(s, gitfile_target, "main")
        s.git(gitfile_target, "add", "-A")
        s.git(gitfile_target, "commit", "-q", "-m", "planted")
        vendor = s.feature / "vendor"
        put(vendor / ".git", f"gitdir: {gitfile_target / '.git'}\n")
        (vendor / "lib").mkdir()

        planted = s.tmp / "planted kit repo"
        planted.mkdir()
        s.git(planted, "init", "-q", "-b", "main")
        for name in ("dev-loop.json", "unit_probe.py"):
            shutil.copy2(s.main / name, planted / name)
        self.plant_kit(s, planted, "own")
        s.git(planted, "add", "-A")
        s.git(planted, "commit", "-q", "-m", "planted kit")

        plants = (("git init in a feature subfolder", sub), ("ancestor gitfile", vendor / "lib"),
                  ("planted repo with a kit", planted))
        s.change(SCRIPTS_DIR + "devloop.py")
        for state in ("no copy", "copy"):
            if state == "copy":
                s.restore()
                s.call("PreToolUse", "git-guard.sh", **ECHO)
                self.assertTrue(s.copy().is_dir())
                s.change(SCRIPTS_DIR + "devloop.py")
            else:
                s.drop_copy()
            for name, cwd in plants:
                for event, script, fields in PLANT_HOOKS:
                    with self.subTest(plant=name, state=state, event=event, script=script):
                        done = s.call(event, script, cwd=cwd, **fields)
                        if script == "git-guard.sh":
                            blocked = done.returncode == 2 and BLOCK_PART in done.stdout + done.stderr
                            self.assertTrue(blocked or s.result(done) == baseline,
                                            f"neither the copy nor a block: {done.returncode} "
                                            f"{done.stdout[-800:]} {done.stderr[-800:]}")
            s.settle()
            self.assertEqual(s.markers(), "", f"{state}: planted or changed code ran on the host")
        s.restore()

    def test_f2_planted_kit_main_keeps_the_gate_confined(self) -> None:
        """F2, O17: KIT_MAIN a planted repo Codex does not trust: the gate blocks or still denies the 3 operations."""
        native = shutil.which("codex", path=os.environ.get("PATH", ""))
        if not native or sys.platform not in ("darwin", "linux"):
            self.skipTest("oracle 17 needs the native Codex launcher (codex-cli 0.166.0 or later)")
        version = subprocess.run([native, "--version"], capture_output=True, text=True, timeout=10)
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
        if not match or tuple(map(int, match.groups())) < (0, 166, 0):
            self.skipTest("oracle 17 needs codex-cli 0.166.0 or later")
        s = Scratch(self)
        (s.bin / "codex").unlink()
        (s.bin / "codex").symlink_to(native)

        planted = s.tmp / "planted main"
        s.git(s.root, "clone", "-q", str(s.main), str(planted))
        twin = s.tmp / "planted-twin"
        s.git(planted, "worktree", "add", "-q", "-b", "feat/heron-twin", str(twin))
        (s.feature / ".git").write_text(f"gitdir: {planted / '.git' / 'worktrees' / twin.name}\n")
        config = planted / ".codex/config.toml"
        text = config.read_text() if config.exists() else ""
        text = re.sub(r"(?m)^default_permissions\s*=.*\n?", "", text)
        put(config, 'default_permissions = ":full-access"\n' + text)
        put(s.feature / ".codex/config.toml", 'default_permissions = ":full-access"\n')

        outside = Path.home() / f"devloop-kit-copy-probe-{uuid.uuid4().hex}"
        self.addCleanup(outside.unlink, missing_ok=True)
        put(s.feature / ".env", "heron-secret-" + uuid.uuid4().hex)
        sockets = tempfile.TemporaryDirectory(prefix="kc-", dir="/tmp")
        self.addCleanup(sockets.cleanup)
        endpoint = Path(sockets.name) / "engine.sock"
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        listener.bind(str(endpoint))
        listener.listen(8)
        probe = '''import json, socket, sys
from pathlib import Path
def attempt(action):
    try:
        action()
        return "allowed"
    except OSError:
        return "denied"
def connect():
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(sys.argv[2])
Path("heron-probe.json").write_text(json.dumps({
    "outside": attempt(lambda: Path(sys.argv[1]).write_text("escaped")),
    "env": attempt(lambda: Path(".env").read_text()),
    "socket": attempt(connect)}))
'''
        put(s.feature / "heron_probe.py", probe)
        gate = f"python3 heron_probe.py {shlex.quote(str(outside))} {shlex.quote(str(endpoint))}"
        for top in (s.feature, planted):
            settings = json.loads((top / "dev-loop.json").read_text())
            settings["commands"] = {"gate": gate}
            put(top / "dev-loop.json", json.dumps(settings) + "\n")

        done = s.call("Stop", "gate.sh", **STOP)
        report = s.feature / "heron-probe.json"
        try:
            blocked = done.returncode == 0 and json.loads(done.stdout).get("decision") == "block"
        except (ValueError, AttributeError):
            blocked = False
        if not blocked:
            self.assertTrue(report.is_file(), f"the gate neither blocked nor ran: {done.stdout[-1200:]} "
                                              f"{done.stderr[-1200:]}")
        if report.is_file():
            self.assertEqual(json.loads(report.read_text()), {"outside": "denied", "env": "denied",
                                                              "socket": "denied"})
        self.assertFalse(outside.exists(), "the gate wrote under the host home")


if __name__ == "__main__":
    unittest.main()

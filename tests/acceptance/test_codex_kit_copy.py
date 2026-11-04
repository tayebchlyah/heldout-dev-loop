#!/usr/bin/env python3
"""Held-out oracle tests for the Codex kit copy (spec: Codex Kit Copy): flows F1 and F3, the budget, the kit text.

Each docstring names the `## Acceptance oracle` lines (O<n>) or flows (F<n>) it proves. Seam 1: a scratch project
made by `bin/install.mjs init`, a linked feature worktree, HOME and CODEX_HOME in temporary folders; each Codex
hook command of `.codex/hooks.json` runs through `sh -c` with hook JSON on stdin. Fixtures:
`codex_kit_copy_support.py`. O8 is a unit seam (the fill function) and O20 the Stage 6 live probe (seam 2): not
here. Every name in the scratch projects is invented.
"""

from __future__ import annotations

import re
import shutil
import statistics
import subprocess
import sys
import time
import unittest
from pathlib import Path

from codex_kit_copy_support import (ECHO, HEAD, HOOKS_DIR, MANIFEST, ROOT, SCRIPTS, SCRIPTS_DIR, SEARCH, START,
                                    STOP, SUBAGENT, TAG_PUSH, Scratch, put)

KIT_REFERENCE = ROOT / "reference" / "kit.md"
CODEX_ROUTE = ROOT / "skill" / "references" / "codex.md"


class CodexKitCopyInstallFlow(unittest.TestCase):
    def test_f1_init_then_first_hook_fills_the_copy_and_runs_it(self) -> None:
        """F1, O1, O2, O9, O10, O14, O15: `init` names the copy by H; the first hook fills it and runs from it."""
        s = Scratch(self, codex_home="codex home")
        digest = s.manifest_hash()

        with self.subTest(oracle="O1"):
            hooks = s.all_hooks()
            self.assertEqual(len(s.kit_entries()), len(hooks), "an init entry without the kit copy head")
            for event, _, hook in hooks:
                found = s.entry(hook["command"])
                self.assertEqual(found[0], digest, f"{event}: H is not the sha256 of {MANIFEST}")
                self.assertIn(f'{HEAD}{digest}/{HOOKS_DIR}{found[1]}"', hook["command"])
            self.assertEqual({s.entry(hook["command"])[1] for _, _, hook in hooks}, set(SCRIPTS))

        with self.subTest(oracle="O15", step="init"):
            s.assert_no_kit_filesystem_entry()

        # O2: no copy; a hook from the feature worktree fills it.
        self.assertFalse(s.kit_dir.exists(), "init wrote under CODEX_HOME")
        first = s.call("PreToolUse", "git-guard.sh", **ECHO)
        copy = s.copy(digest)
        with self.subTest(oracle="O2"):
            self.assertEqual(s.kit_children(), [digest], "a staging folder is left, or another copy")
            self.assertEqual(s.copy_files(copy), s.kit_files(), "copy is not exactly MANIFEST and the D6 entries")
            for path in s.copy_files(copy):
                self.assertEqual((copy / path).stat().st_mode & 0o777, 0o644, path)

        # O2, D7: the command runs its script from the copy, with KIT_ROOT and KIT_MAIN.
        guard = copy / HOOKS_DIR / "git-guard.sh"
        original = guard.read_bytes()
        guard.write_text(f'#!/bin/sh\nprintf "%s\\n%s\\n" "$KIT_ROOT" "$KIT_MAIN" > "{s.marker}"\n')
        s.call("PreToolUse", "git-guard.sh", **ECHO)
        lines = s.markers().splitlines()
        self.assertEqual(len(lines), 2, "the command did not run the copy's script")
        self.assertEqual(Path(lines[0]).resolve(), copy.resolve(), "KIT_ROOT")
        self.assertEqual(Path(lines[1]).resolve(), s.main, "KIT_MAIN")
        guard.write_bytes(original)
        s.marker.unlink()
        baseline = s.result(s.call("PreToolUse", "git-guard.sh", **ECHO))
        self.assertEqual(s.result(first), baseline, "the filling run exits other than a run with the copy")

        with self.subTest(oracle="O14"):
            config = s.codex_home / "config.toml"
            plain = config.read_text()
            config.write_text(plain + "".join(
                f'\n[hooks.state."{path}/.codex/hooks.json:pre_tool_use:0:0"]\ntrusted_hash = "sha256:{"0" * 64}"\n'
                for path in (s.main, s.feature, s.root)))
            self.assertEqual(s.result(s.call("PreToolUse", "git-guard.sh", **ECHO)), baseline, "hit, headers")
            s.drop_copy()
            self.assertEqual(s.result(s.call("PreToolUse", "git-guard.sh", **ECHO)), baseline, "miss, headers")
            self.assertEqual(s.copy_files(copy), s.kit_files())
            config.write_text(plain)

        with self.subTest(oracle="O9"):
            s.drop_copy()
            started = [s.start("PreToolUse", "git-guard.sh", **ECHO) for _ in range(8)]
            results = []
            for process in started:
                out, _ = process.communicate(timeout=180)
                results.append((process.returncode, out))
            self.assertEqual(results, [baseline] * 8)
            self.assertEqual(s.kit_children(), [digest], "a staging folder is left, or a second copy")
            self.assertEqual(s.copy_files(copy), s.kit_files())

        with self.subTest(oracle="O10"):
            self.direct_runs_take_the_cli_of_the_first_worktree(s)

    def direct_runs_take_the_cli_of_the_first_worktree(self, s: Scratch) -> None:
        """O10: a script run with no KIT_ROOT takes devloop.py and check_setup.py from the stdin cwd's main."""
        loose = s.root / "loose kit"
        for folder in (HOOKS_DIR, SCRIPTS_DIR):
            shutil.copytree(s.main / folder, loose / folder)
        for label, top in (("main", s.main), ("feature", s.feature), ("loose", loose)):
            for name in ("devloop.py", "check_setup.py"):
                s.change(SCRIPTS_DIR + name, top, label)
        elsewhere = s.root / "elsewhere"
        elsewhere.mkdir()
        transcript = s.root / "heron-transcript.jsonl"
        transcript.write_text("")
        env = {key: value for key, value in s.env.items() if key not in ("KIT_ROOT", "KIT_MAIN")}
        runs = [("board.sh", ["session-start"], "SessionStart", START),
                ("acceptance-wall.sh", [], "PreToolUse", SEARCH),
                ("session-start.sh", [], "SessionStart", START),
                ("git-guard.sh", [], "PreToolUse", TAG_PUSH)]
        if shutil.which("jq", path=env.get("PATH")):
            runs.append(("seal.sh", ["stop"], "SubagentStop",
                         {**SUBAGENT, "agent_transcript_path": str(transcript)}))
        for script, args, event, fields in runs:
            with self.subTest(script=script):
                s.marker.unlink(missing_ok=True)
                subprocess.run(["sh", str(loose / HOOKS_DIR / script), *args], cwd=elsewhere, env=env,
                               input=s.payload(event, s.feature, fields), capture_output=True, text=True,
                               timeout=180)
                deadline = time.monotonic() + 15
                while not s.markers() and time.monotonic() < deadline:
                    time.sleep(0.1)
                time.sleep(0.5)
                self.assertEqual(set(s.markers().split()), {"main"}, f"{script} took another CLI")
        s.restore()

    def test_f3_stop_hooks_with_no_kit_warn_and_never_block(self) -> None:
        """F3, O7: gate.sh and seal.sh on Stop, SubagentStop and SubagentStart give a systemMessage, no block."""
        s = Scratch(self)
        put(s.feature / "heron_change.py", "value = 1\n")
        changed = SCRIPTS_DIR + "devloop.py"
        stops = (("Stop", "gate.sh", STOP), ("Stop", "seal.sh", STOP), ("SubagentStop", "gate.sh", {**SUBAGENT, **STOP}),
                 ("SubagentStop", "seal.sh", {**SUBAGENT, **STOP}), ("SubagentStart", "seal.sh", SUBAGENT))
        for state, path in (("changed kit file", changed), ("no MANIFEST", MANIFEST)):
            if path == MANIFEST:
                (s.main / MANIFEST).unlink()
            else:
                s.change(path)
            for event, script, fields in stops:
                with self.subTest(state=state, event=event, script=script):
                    done = s.call(event, script, **fields)
                    self.assertIn(s.block(path), s.system_message(done))
                    self.assertNotEqual(s.stdout_json(done).get("decision"), "block")
                    self.assertNotRegex(done.stdout, r'"decision"\s*:\s*"block"')
            self.assertEqual(s.kit_children(), [], f"{state}: a copy or staging folder was written")
            s.settle()
            self.assertEqual(s.markers(), "", f"{state}: changed code ran")
            s.restore()


class CodexKitCopyBudget(unittest.TestCase):
    def test_o16_hook_start_budget_with_and_without_the_copy(self) -> None:
        """O16: with 50 linked worktrees, a hit starts its script in 0.05 s (median of 20), a miss in 1 s (of 5)."""
        if sys.platform != "darwin":
            self.skipTest("oracle 16 states its budget on the owner's Mac")
        s = Scratch(self)
        for n in range(49):
            s.git(s.main, "worktree", "add", "-q", "--no-checkout", "-b", f"heron-{n}",
                  str(s.root / "worktrees" / f"heron {n}"))
        misses = []
        for _ in range(5):
            s.drop_copy()
            began = time.perf_counter()
            process = s.start("PreToolUse", "git-guard.sh", **ECHO)
            while not s.copy().exists():
                self.assertIsNone(process.poll(), "the hook ended before the copy appeared")
                time.sleep(0.001)
            misses.append(time.perf_counter() - began)
            process.communicate(timeout=180)
        put(s.copy() / HOOKS_DIR / "git-guard.sh", "#!/bin/sh\nexit 0\n")
        hits = []
        for _ in range(20):
            began = time.perf_counter()
            done = s.call("PreToolUse", "git-guard.sh", **ECHO)
            hits.append(time.perf_counter() - began)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLessEqual(statistics.median(hits), 0.05, f"hit times {hits}")
        self.assertLessEqual(statistics.median(misses), 1.0, f"miss times {misses}")


class CodexKitCopyText(unittest.TestCase):
    def test_o21_kit_reference_and_codex_route_name_the_copy_and_the_hooks_gap(self) -> None:
        """O21: the #227 upgrade note in `reference/kit.md`; the `codex.md` gaps name `.codex/hooks.json`."""
        kit = KIT_REFERENCE.read_text()
        notes = [p for p in re.split(r"\n\s*\n", kit) if p.lstrip().startswith("Codex kit copy upgrade (#227)")]
        self.assertEqual(len(notes), 1, "no single 'Codex kit copy upgrade (#227)' note")
        for needle in ("/hooks", "1 MB", "CODEX_HOME"):
            self.assertIn(needle, notes[0])
        route = CODEX_ROUTE.read_text()
        gaps = [line for line in route.splitlines() if "Gaps" in line]
        section = re.search(r"^## Gaps\n(.*?)(?=^## |\Z)", route, re.M | re.S)
        if section:
            gaps.append(section.group(1))
        self.assertTrue(any(".codex/hooks.json" in block for block in gaps),
                        "no codex.md gaps entry names .codex/hooks.json")


if __name__ == "__main__":
    unittest.main()

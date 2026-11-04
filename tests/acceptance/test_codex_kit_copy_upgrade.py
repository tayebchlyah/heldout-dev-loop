#!/usr/bin/env python3
"""Held-out oracle tests for the Codex kit copy (spec: Codex Kit Copy), flow F4: upgrade and uninstall.

Each docstring names the `## Acceptance oracle` lines (O<n>) it proves, and the earlier line it combines with
(Codex Hook Sandbox oracle 5, as amended). Seams: `bin/install.mjs init`, `upgrade` and `uninstall-global` as
processes on scratch projects with HOME in a temporary folder, `codexHookKey` of `lib/kit.mjs` in `node`, and the
Codex hook commands run through `sh -c`. The earlier heads are the command texts earlier kits wrote. Fixtures:
`codex_kit_copy_support.py`. Every name is invented.
"""

from __future__ import annotations

import json
import unittest

from codex_kit_copy_support import (ARGS, D6, ECHO, EARLIER_FORMS, HOOKS_DIR, INSTALLER, MANIFEST, SCRIPTS_DIR,
                                    START, STOP, SUBAGENT, Scratch, digest_of, earlier_head, hook_keys, kit_source,
                                    put, tree)

FIELDS = {"PreToolUse": ECHO, "PostToolUse": ECHO, "SessionStart": START, "Stop": STOP,
          "SubagentStop": {**SUBAGENT, **STOP}, "SubagentStart": SUBAGENT, "UserPromptSubmit": {"prompt": "heron"}}
USER = {"type": "command", "command": "echo heron-user-hook"}


class CodexKitCopyUpgradeFlow(unittest.TestCase):
    def test_f4_upgrade_from_earlier_heads_then_to_a_later_manifest(self) -> None:
        """F4, O12, O13, O18; O15 and O19 on upgrade; with Codex Hook Sandbox O5: the hooks run the copy."""
        s = Scratch(self, harness="both")
        claude = (s.main / ".claude/settings.json").read_bytes()
        self.assertNotIn(b"dev-loop/kit/", claude, "a Claude entry got the Codex kit copy head")
        fresh = s.kit_entries()
        self.assertTrue(fresh, "init wrote no Codex entry with the kit copy head")
        self.assertEqual(len(fresh), len(s.all_hooks()))
        first = s.manifest_hash()
        pairs = sorted({(event, s.entry(command)[1]) for event, _, command in fresh})

        with self.subTest(oracle="O12", seam="codexHookKey"):
            commands = [command for _, _, command in fresh]
            other = [command.replace(first, "0123abcd" * 8) for command in commands]
            earlier = [earlier_head(form, script, ARGS.get((event, script), ""))
                       for form in EARLIER_FORMS for event, script in pairs]
            keys = hook_keys(commands + other + earlier + [USER["command"]])
            self.assertNotIn(None, keys[:-1], "a kit head codexHookKey does not know")
            self.assertIsNone(keys[-1], "codexHookKey took a user hook for a kit entry")

        for form in EARLIER_FORMS:
            with self.subTest(oracle="O12", head=form):
                hooks = s.hooks()
                for event, groups in hooks.items():
                    for group in groups:
                        for hook in group["hooks"]:
                            found = s.entry(hook.get("command", ""))
                            if found:
                                hook["command"] = earlier_head(form, found[1], ARGS.get((event, found[1]), ""))
                stop = [hook for group in hooks["Stop"] for hook in group["hooks"]]
                if USER not in stop:
                    hooks["Stop"].append({"hooks": [USER]})
                s.save_hooks(hooks)
                if form == "#222":
                    old = s.main / HOOKS_DIR / "kit-main.sh"
                    put(old, "#!/bin/sh\nprintf %s \"$PWD\"\n", True)
                    manifest = json.loads((s.main / MANIFEST).read_text())
                    manifest["files"][HOOKS_DIR + "kit-main.sh"] = "sha256:" + digest_of(old.read_bytes())
                    put(s.main / MANIFEST, json.dumps(manifest, indent=2) + "\n")
                done = s.install("upgrade")
                self.assertIn("/hooks", done.stdout)
                digest = s.manifest_hash()
                self.assertEqual(s.kit_entries(), sorted((event, matcher, command.replace(first, digest))
                                                         for event, matcher, command in fresh))
                others = [hook for _, _, hook in s.all_hooks() if not s.entry(hook.get("command", ""))]
                self.assertEqual(others, [USER], "a user hook was lost, or an earlier head was kept")
                self.assertFalse((s.main / HOOKS_DIR / "kit-main.sh").exists(), "kit-main.sh is left")
                self.assertEqual((s.main / ".claude/settings.json").read_bytes(), claude, "O19: Claude entries")
                s.assert_no_kit_filesystem_entry()

        with self.subTest(earlier="Codex Hook Sandbox O5"):
            for rel in sorted(s.manifest_files()):
                if rel.startswith(D6) and rel.endswith((".sh", ".py")):
                    s.change(rel, s.feature, f"feature {rel}")
            put(s.feature / "json.py", s.marker_code("json.py", "feature cwd import"))
            env = {**s.env, "PYTHONPATH": str(s.feature)}
            s.drop_copy()
            for event, _, command in s.kit_entries():
                script = s.entry(command)[1]
                s.call(event, script, env=env, **FIELDS[event])
            s.settle()
            self.assertEqual(s.markers(), "", "an installed entry ran feature-controlled code on the host")
            self.assertEqual(s.copy_files(s.copy()), s.kit_files(), "the hooks did not run the kit copy")

        with self.subTest(oracle="O18"):
            put(s.home / ".codex/dev-loop/kit" / ("ab" * 32) / MANIFEST, "{}\n")
            s.run(s.root, "node", str(INSTALLER), "uninstall-global", "--yes", "--force")
            self.assertFalse((s.home / ".codex/dev-loop/kit").exists(), "uninstall-global left the kit copies")

        with self.subTest(oracle="O13"):
            later = Scratch(self, installer=kit_source(self))
            before = later.manifest_hash()
            later.call("PreToolUse", "git-guard.sh", **ECHO)
            home = tree(later.codex_home)
            done = later.install("upgrade")
            self.assertIn("/hooks", done.stdout)
            after = later.manifest_hash()
            self.assertNotEqual(after, before, "the later kit has the same MANIFEST")
            entries = later.kit_entries()
            self.assertEqual(len(entries), len(fresh))
            self.assertEqual({later.entry(command)[0] for _, _, command in entries}, {after})
            self.assertNotIn(before, (later.main / ".codex/hooks.json").read_text())
            self.assertEqual(tree(later.codex_home), home, "upgrade wrote under CODEX_HOME")
            later.assert_no_kit_filesystem_entry()
            self.assertTrue((later.main / SCRIPTS_DIR / "devloop.py").is_file())


if __name__ == "__main__":
    unittest.main()

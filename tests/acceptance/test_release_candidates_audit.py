#!/usr/bin/env python3
"""Held-out oracle tests of the kit copies in the release audit: `release-plan` `exclude` and `release-check`
(spec: Release Candidates). Seams, fixture choices and shared helpers: `release_candidates_support.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from release_candidates_support import K1, K2, K4, KIT_ROOTS, MANIFEST, REPO_ROOT, AuditCase, put, sha


def reasons(payload: dict, name: str) -> list[dict]:
    return [r for r in payload.get("reasons") or [] if isinstance(r, dict) and r.get("reason") == name]


class AuditExclusionTests(AuditCase):

    def test_f3_incremental_audit(self) -> None:
        """F3 (O18 as amended by kit-copy-reference, O26): record at the rc.1 commit, then an upgrade and a code
        change, in this repo with no kit source: `release-plan` excludes nothing; `release-check` still gives
        stale-audit with every changed path."""
        self.record_at(self.A)
        B = self.upgrade_commit()
        payload = self.plan()
        self.assertEqual(payload.get("scope"), f"{self.A}..{B}", payload)
        self.assertEqual(payload["exclude"], [], payload)
        self.assertNotIn("src/app.py", payload["exclude"])
        self.assertNotIn("dev-loop.json", payload["exclude"])
        code, check = self.devloop("release-check", "--sha", B)
        self.assertEqual(code, 1, check)
        stale = reasons(check, "stale-audit")
        self.assertEqual(len(stale), 1, check)
        paths = set(stale[0].get("paths") or [])
        for path in (K1, K2, MANIFEST, "dev-loop.json", "src/app.py"):
            self.assertIn(path, paths, check)

    def test_f4_hand_edited_copy_with_rewritten_hash(self) -> None:
        """F4 (O20), threat 1: copies edited and their hashes rewritten, MANIFEST version unchanged: exclude []."""
        self.record_at(self.A)
        self.upgrade_commit(version="3.0.0-rc.1")
        self.assertEqual(self.plan()["exclude"], [])

    def test_o19_copy_that_does_not_match_its_hash_stays_in_scope(self) -> None:
        """O19, threat 1: K2 differs from its MANIFEST hash at B: K2 is not in exclude (no kit source: O18 as
        amended by kit-copy-reference excludes nothing)."""
        self.record_at(self.A)
        self.upgrade_commit(entries={K2: sha("STAGE = 'something else'\n")})
        self.assertEqual(self.plan()["exclude"], [])

    def test_o21_product_path_in_the_manifest_is_not_excluded(self) -> None:
        """O21, threat 1: a MANIFEST key `src/app.py` with the right hash, outside every kit root: not excluded
        (no kit source: O18 as amended by kit-copy-reference excludes nothing)."""
        self.record_at(self.A)
        self.upgrade_commit(entries={"src/app.py": sha("def colour():\n    return 'blue'\n")})
        self.assertEqual(self.plan()["exclude"], [])

    def test_o22_first_audit_excludes_nothing(self) -> None:
        """O22 (and O28): no `## Release audits` record: scope whole, exclude []; `release-check --tag
        v3.0.0-rc.1` gives no-audit, as for a final tag."""
        self.no_record()
        self.upgrade_commit()
        payload = self.plan()
        self.assertEqual(payload.get("scope"), "whole", payload)
        self.assertEqual(payload["exclude"], [])
        self.tag(self.repo, "v3.0.0-rc.1")
        code, check = self.devloop("release-check", "--tag", "v3.0.0-rc.1")
        self.assertEqual(code, 1, check)
        self.assertIs(check.get("ok"), False, check)
        self.assertTrue(reasons(check, "no-audit"), check)

    def test_o23_no_manifest_at_head(self) -> None:
        """O23: no `.dev-loop/MANIFEST.json` at HEAD: exclude []."""
        self.record_at(self.A)
        self.upgrade_commit(manifest=False)
        self.assertEqual(self.plan()["exclude"], [])

    def test_o24_record_not_an_ancestor(self) -> None:
        """O24, threat 1: the record commit is not an ancestor of HEAD (merge-base fallback), with the upgrade
        of O18: exclude []."""
        self.git(self.repo, "checkout", "-q", "-b", "rewritten")
        side = self.commit(self.repo, {"src/other.py": "OTHER = 1\n"}, "R: rewritten history")
        self.git(self.repo, "checkout", "-q", "main")
        self.record_at(side)
        self.upgrade_commit()
        payload = self.plan()
        self.assertEqual(payload.get("base"), self.A, payload)
        self.assertEqual(payload["exclude"], [])

    def test_o25_deleted_kit_copy_stays_in_scope(self) -> None:
        """O25: a kit copy at A deleted at B (and dropped from the MANIFEST) is not in exclude (no kit source: O18
        as amended by kit-copy-reference excludes nothing)."""
        self.record_at(self.A)
        self.upgrade_commit(extra={K4: None}, entries={K4: None})
        self.assertEqual(self.plan()["exclude"], [])

    def test_o27_merged_config_files_are_never_excluded(self) -> None:
        """O27, threat 1: `.claude/settings.json` and `.codex/hooks.json` change in B (listed in the MANIFEST
        with their hashes): neither is in exclude (no kit source: O18 as amended by kit-copy-reference excludes
        nothing)."""
        settings, hooks = '{"hooks": {"Stop": []}}\n', '{"hooks": {"Stop": []}}\n'
        self.record_at(self.A)
        self.upgrade_commit(extra={".claude/settings.json": settings, ".codex/hooks.json": hooks},
                            entries={".claude/settings.json": sha(settings), ".codex/hooks.json": sha(hooks)})
        self.assertEqual(self.plan()["exclude"], [])

    def test_o29_kit_roots_equal_the_installer_kit_roots(self) -> None:
        """O29, threat 1: every kit path a real `init` writes lies under the six kit roots, each root is used,
        and `release-plan` excludes no matching kit copy under any folder in this repo with no kit source (O18 as
        amended by kit-copy-reference)."""
        project = self.new_repo("installed")
        init = subprocess.run(["node", str(REPO_ROOT / "bin" / "install.mjs"), "init", "--yes", "--harness=both"],
                              cwd=project, env=self.env, capture_output=True, text=True, timeout=300)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        keys = list(json.loads((project / MANIFEST).read_text())["files"])
        self.assertEqual([k for k in keys if not k.startswith(KIT_ROOTS)], [])
        self.assertEqual([r for r in KIT_ROOTS if not any(k.startswith(r) for k in keys)], [])

        regular = {".claude/agents/spec-tester.md": "agent", ".codex/agents/spec-tester.toml": "agent",
                   ".codex/rules/dev-loop.rules": "rule", ".dev-loop/hooks/claude/gate.sh": "hook",
                   ".claude/commands/notes.md": "other", ".codex/prompts/notes.md": "other",
                   ".claude/hooks/extra.sh": "other", ".dev-loop/extra/notes.sh": "other"}
        link = ".claude/skills/dev-loop"

        def kit_commit(n: int, version: str) -> str:
            """Every path of `regular` and K1 at text n, the link at target v<n>, all listed with their hashes."""
            files = {path: f"{kind} {n}\n" for path, kind in regular.items()}
            files[K1] = f"# dev-loop skill {n}\n"
            for path, text in files.items():
                put(self.repo / path, text)
            target = self.repo / link
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            os.symlink(f"../../.agents/skills/v{n}", target)
            listed = {**{p: sha(t) for p, t in self.kit.items()}, **{p: sha(t) for p, t in files.items()},
                      link: f"link:../../.agents/skills/v{n}"}
            put(self.repo / MANIFEST, self.manifest(version, listed))
            self.git(self.repo, "add", "-A")
            self.git(self.repo, "commit", "-q", "-m", f"kit {version}")
            return self.git(self.repo, "rev-parse", "HEAD")

        self.record_at(kit_commit(1, "3.0.0-rc.1"))
        kit_commit(2, "3.0.0-rc.2")
        self.assertEqual(self.plan()["exclude"], [])


if __name__ == "__main__":
    unittest.main()

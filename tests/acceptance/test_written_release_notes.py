#!/usr/bin/env python3
"""Held-out acceptance flow tests of spec Written Release Notes, one test per `## Acceptance flows` item (F<n>).

Seams: `devloop.py release-notes`, `devloop.py ci-skip`, `devloop.py release-check`, `check_setup.py` and
`scripts/repo_rules.py promoted --sha` as processes on scratch git repos, run from the kit source. Scratch repos,
fixtures and helpers: `release_candidates_support.py`. Every name in the scratch repos is invented.

Fixture choices the spec leaves open:
- `--output`, `--repo` and the output file are absolute paths.
- The kit copies of a scratch repo live under `.agents/skills/`; their MANIFEST hashes are `sha256:` of the
  file content (the rule of Release Candidates D9).
- `check_setup.py` runs on a project made by `init` of the kit source; a configuration failure is a line of its
  output that the same run on the untouched project does not print.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from release_candidates_support import (CHECKER, DEVLOOP, K1, K2, MANIFEST, REPO_RULES, InstallerCase, Scratch, put,
                                        sha)
from release_security_audit_support import note_text, record_line

PACKAGE = """{
  "name": "acme-widgets",
  "version": "%s",
  "dependencies": {
    "lodash": "%s"
  }
}
"""

OLD_CHANGELOG = """# Changelog

## [2.1.1] - 2026-10-16

### Fixes

- Widget colour no longer resets.
"""


def new_changelog(version: str) -> str:
    return OLD_CHANGELOG.replace("# Changelog\n\n", f"""# Changelog

## [{version}] - 2026-10-27

### Highlights

**Gadget dials.** Gadget dials turn by themselves, so a user sets them once.

""", 1)


def config(version: str, **extra: object) -> str:
    return json.dumps({"version": version, "harnesses": ["claude", "codex"], **extra}, indent=2) + "\n"


def manifest(version: str, files: dict[str, str]) -> str:
    return json.dumps({"version": version, "files": {p: sha(t) for p, t in sorted(files.items())}}, indent=2) + "\n"


class FlowCase(Scratch):

    def run_json(self, args: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, *args], cwd=cwd, env=env or self.env, capture_output=True,
                                text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            payload = {"unparsed_stdout": result.stdout}
        payload.setdefault("_stderr", result.stderr)
        return result.returncode, payload

    def devloop(self, repo: Path, *args: str) -> tuple[int, dict]:
        return self.run_json([str(DEVLOOP), *args], repo)


# ---------------------------------------------------------------------------------------------------------------
# F1: written notes to a release body (oracle 1, 3)
# ---------------------------------------------------------------------------------------------------------------

WRITTEN = """# Changelog

## [3.0.0] - 2026-11-01

### Highlights

**Widget trays.** Widget trays hold every widget in one place, so a user finds them at once.

## [2.1.1] - 2026-10-16

### Fixes

- Gadget dial no longer sticks after a restart.
"""


class ReleaseNotesFlow(FlowCase):

    def test_f1_written_notes_to_a_release_body(self) -> None:
        """F1 (oracle 1, 3): `release-notes --tag v3.0.0 --output` writes the `### Highlights` paragraph of the
        `## [3.0.0]` section, without its heading or any `2.1.1` line; `--tag v3.0.1` fails with `no-section`
        and writes no file."""
        repo = self.new_repo("notes-repo")
        self.commit(repo, {"CHANGELOG.md": WRITTEN}, "notes")
        out = self.root / "out.md"
        code, payload = self.devloop(repo, "release-notes", "--tag", "v3.0.0", "--repo", str(repo),
                                     "--output", str(out))
        self.assertEqual(code, 0, payload)
        self.assertEqual((payload.get("ok"), payload.get("tag"), payload.get("version")), (True, "v3.0.0", "3.0.0"),
                         payload)
        body = out.read_text()
        self.assertIn("### Highlights", body)
        self.assertIn("**Widget trays.** Widget trays hold every widget in one place", body)
        self.assertNotIn("## [3.0.0]", body)
        for line in ("## [2.1.1] - 2026-10-16", "### Fixes", "Gadget dial no longer sticks"):
            self.assertNotIn(line, body)
        missing = self.root / "missing.md"
        code, payload = self.devloop(repo, "release-notes", "--tag", "v3.0.1", "--repo", str(repo),
                                     "--output", str(missing))
        self.assertEqual(code, 1, payload)
        self.assertEqual((payload.get("ok"), payload.get("reason")), (False, "no-section"), payload)
        self.assertFalse(missing.exists())


# ---------------------------------------------------------------------------------------------------------------
# F2 to F4: ci-skip (oracle 8, 10, 12, 15, 18)
# ---------------------------------------------------------------------------------------------------------------

KIT_OLD = {K1: "# dev-loop skill one\n", K2: "STAGE = 1\n"}
KIT_NEW = {K1: "# dev-loop skill two\n", K2: "STAGE = 2\n"}


class ReleaseRepoCase(FlowCase):
    """Base on `main`: `package.json` 2.1.1 (in `version_files`), `CHANGELOG.md` at 2.1.1, kit 2.1.1 (two kit
    copies and the MANIFEST), `.claude/settings.json` and `src/app.py`. Each head is a branch from the base."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.new_repo("acme-widgets")
        self.base = self.commit(self.repo, {
            "dev-loop.json": config("2.1.1", version_files=["package.json"]),
            "package.json": PACKAGE % ("2.1.1", "4.17.20"), "CHANGELOG.md": OLD_CHANGELOG,
            "src/app.py": "def colour():\n    return 'red'\n", ".claude/settings.json": '{"hooks": {}}\n',
            **KIT_OLD, MANIFEST: manifest("2.1.1", KIT_OLD)}, "base")

    def head(self, name: str, files: dict[str, str | None]) -> str:
        self.git(self.repo, "checkout", "-q", "-b", name, self.base)
        rev = self.commit(self.repo, files, name)
        self.git(self.repo, "checkout", "-q", "main")
        return rev

    def ci_skip(self, head: str) -> dict:
        code, payload = self.devloop(self.repo, "ci-skip", "--base", self.base, "--head", head,
                                     "--repo", str(self.repo))
        self.assertEqual(code, 0, payload)
        return payload


class CiSkipFlow(ReleaseRepoCase):

    def test_f2_release_pr_skips_ci(self) -> None:
        """F2 (oracle 8, 10): a version bump in `package.json` plus its new `CHANGELOG.md` section: `skip` true,
        `kind` release, `blocking` []; the same diff with a dependency bump: `skip` false, `package.json`
        blocking."""
        release = self.head("release", {"CHANGELOG.md": new_changelog("2.2.0"),
                                        "package.json": PACKAGE % ("2.2.0", "4.17.20")})
        payload = self.ci_skip(release)
        self.assertEqual((payload.get("skip"), payload.get("kind"), payload.get("blocking")), (True, "release", []),
                         payload)
        bumped = self.head("release-and-dependency", {"CHANGELOG.md": new_changelog("2.2.0"),
                                                      "package.json": PACKAGE % ("2.2.0", "4.17.21")})
        payload = self.ci_skip(bumped)
        self.assertIs(payload.get("skip"), False, payload)
        self.assertIn("package.json", payload.get("blocking") or [], payload)

    def test_f3_kit_upgrade_skips_ci_a_hook_config_change_does_not(self) -> None:
        """F3 (oracle 15 as amended by kit-copy-reference, 18): in this repo with no kit source, kit copies that
        match the head MANIFEST, the MANIFEST with a new `version` and `dev-loop.json` with only `version` changed:
        `skip` false, every changed kit path in `blocking`; plus a change to `.claude/settings.json`: `skip` false,
        that path blocking."""
        upgrade = {**KIT_NEW, MANIFEST: manifest("2.2.0", KIT_NEW),
                   "dev-loop.json": config("2.2.0", version_files=["package.json"])}
        payload = self.ci_skip(self.head("upgrade", upgrade))
        self.assertIs(payload.get("skip"), False, payload)
        for path in (K1, K2):
            self.assertIn(path, payload.get("blocking") or [], payload)
        hooked = self.head("upgrade-and-hooks", {**upgrade, ".claude/settings.json": '{"hooks": {"Stop": []}}\n'})
        payload = self.ci_skip(hooked)
        self.assertIs(payload.get("skip"), False, payload)
        self.assertIn(".claude/settings.json", payload.get("blocking") or [], payload)

    def test_f4_version_that_does_not_match_the_notes_runs_full_ci(self) -> None:
        """F4 (oracle 12), abuse: `package.json` moves to 2.3.0 while the new `CHANGELOG.md` section is 2.2.0:
        `skip` false."""
        head = self.head("mismatch", {"CHANGELOG.md": new_changelog("2.2.0"),
                                      "package.json": PACKAGE % ("2.3.0", "4.17.20")})
        payload = self.ci_skip(head)
        self.assertIs(payload.get("skip"), False, payload)


# ---------------------------------------------------------------------------------------------------------------
# F5: configuration shapes (oracle 25, 26, 27)
# ---------------------------------------------------------------------------------------------------------------

class ConfigurationFlow(InstallerCase):

    def checker(self, repo: Path) -> list[str]:
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--home", str(self.home),
                                 "--repo", str(repo)], cwd=repo, env=self.env, capture_output=True, text=True,
                                timeout=120)
        return (result.stdout + result.stderr).splitlines()

    def test_f5_configuration_shapes(self) -> None:
        """F5 (oracle 25, 26, 27): no `release_notes` key: no line about it; `release_notes` "ai": a line that
        names `release_notes`, `cliff` and `written`; `version_files` ["VERSION"] with no such file: a line that
        names `VERSION`; ["/etc/passwd"] or ["../x"]: a line; `commands.release_verify` "": a line."""
        repo = self.init_at("3.0.0")
        path = repo / "dev-loop.json"
        original = json.loads(path.read_text())
        self.assertNotIn("release_notes", original)
        baseline = self.checker(repo)
        self.assertEqual([line for line in baseline if "release_notes" in line], [], baseline)

        def new_lines(**changes: object) -> list[str]:
            data = json.loads(json.dumps(original))
            for key, value in changes.items():
                if key == "release_verify":
                    data.setdefault("commands", {})[key] = value
                else:
                    data[key] = value
            put(path, json.dumps(data, indent=2) + "\n")
            try:
                return [line for line in self.checker(repo) if line not in baseline]
            finally:
                put(path, json.dumps(original, indent=2) + "\n")

        lines = new_lines(release_notes="ai")
        self.assertTrue([line for line in lines if all(w in line for w in ("release_notes", "cliff", "written"))],
                        lines)
        lines = new_lines(version_files=["VERSION"])
        self.assertTrue([line for line in lines if "VERSION" in line], lines)
        for bad in (["/etc/passwd"], ["../x"]):
            with self.subTest(bad):
                self.assertTrue(new_lines(version_files=bad))
        lines = new_lines(release_verify="")
        self.assertTrue(lines, "an empty commands.release_verify gives no failure line")


# ---------------------------------------------------------------------------------------------------------------
# F6: audit once, tag the release commit (oracle 24c, 24d)
# ---------------------------------------------------------------------------------------------------------------

NOTES_NAME = "Acme Widgets"


class ReleaseCheckFlow(ReleaseRepoCase):
    """The `ReleaseRepoCase` base is commit A, with an audit record at A in the project note."""

    def setUp(self) -> None:
        super().setUp()
        notes = self.root / "notes" / NOTES_NAME
        notes.mkdir(parents=True)
        self.env["DEV_LOOP_NOTES"] = str(notes)
        put(notes / "Security Audit" / "Run 1" / "run-metadata.json",
            json.dumps({"source_ref": {"commit": self.base}, "profile": "quick", "scope_paths": [],
                        "run_status": "complete"}) + "\n")
        put(notes / "Project Note.md", note_text([record_line(1, self.base)], []))

    def release_check(self, rev: str) -> tuple[int, dict]:
        return self.devloop(self.repo, "release-check", "--sha", rev)

    def test_f6_audit_once_tag_the_release_commit(self) -> None:
        """F6 (oracle 24c as amended by kit-copy-reference, 24d): in this repo with no kit source, the release
        commit B after the record at A (new `## [3.0.0]` section, the `package.json` version, kit copies that match
        the B MANIFEST, a new MANIFEST `version`, `dev-loop.json` `version` only): `stale-audit` with the kit copies
        in `paths`; the same commit plus `src/app.py`: `stale-audit` whose `paths` hold `src/app.py`, the version
        file, the kit copies, the MANIFEST and `dev-loop.json`."""
        release = {"CHANGELOG.md": new_changelog("3.0.0"), "package.json": PACKAGE % ("3.0.0", "4.17.20"),
                   **KIT_NEW, MANIFEST: manifest("3.0.0", KIT_NEW),
                   "dev-loop.json": config("3.0.0", version_files=["package.json"])}
        code, payload = self.release_check(self.head("release", release))
        self.assertEqual(code, 1, payload)
        stale = [r for r in payload.get("reasons") or [] if isinstance(r, dict) and r.get("reason") == "stale-audit"]
        self.assertEqual(len(stale), 1, payload)
        for path in (K1, K2):
            self.assertIn(path, stale[0].get("paths") or [], payload)
        code, payload = self.release_check(self.head("release-and-code",
                                                     {**release, "src/app.py": "def colour():\n    return 'blue'\n"}))
        self.assertEqual(code, 1, payload)
        stale = [r for r in payload.get("reasons") or [] if isinstance(r, dict) and r.get("reason") == "stale-audit"]
        self.assertEqual(len(stale), 1, payload)
        paths = set(stale[0].get("paths") or [])
        for path in ("src/app.py", "package.json", K1, K2, MANIFEST, "dev-loop.json"):
            self.assertIn(path, paths, payload)


# ---------------------------------------------------------------------------------------------------------------
# F7: promoted before the tag (oracle 29, 30)
# ---------------------------------------------------------------------------------------------------------------

class PromotedFlow(FlowCase):

    def promoted(self, repo: Path) -> tuple[int, dict]:
        return self.run_json([str(REPO_RULES), "promoted", "--tag", "v3.0.0", "--sha", "HEAD", "--repo", str(repo)],
                             repo)

    def test_f7_promoted_before_the_tag(self) -> None:
        """F7 (oracle 29, 30): highest rc v3.0.0-rc.2, no tag v3.0.0, HEAD changes only `package.json`,
        `CHANGELOG.md` and `.agents/skills/` files after the rc: exit 0, `ok` true; plus a change to
        `skill/SKILL.md`: exit 1, `violations` ["skill/SKILL.md"]."""
        repo = self.new_repo("dev-loop-copy")
        self.commit(repo, {"skill/SKILL.md": "# skill one\n", "lib/kit.mjs": "export const one = 1\n",
                           "package.json": '{"version": "2.1.1"}\n', "CHANGELOG.md": "# Changelog\n",
                           ".agents/skills/dev-loop/SKILL.md": "# copy 1\n"}, "base")
        self.tag(repo, "v2.1.1")
        self.commit(repo, {"lib/kit.mjs": "export const one = 2\n"}, "rc.1 work")
        self.tag(repo, "v3.0.0-rc.1")
        self.commit(repo, {"skill/SKILL.md": "# skill two\n"}, "rc.2 work")
        self.tag(repo, "v3.0.0-rc.2")
        self.commit(repo, {"package.json": '{"version": "3.0.0"}\n', "CHANGELOG.md": "# Changelog\n\n## [3.0.0]\n",
                           ".agents/skills/dev-loop/SKILL.md": "# copy 3\n"}, "release 3.0.0")
        code, payload = self.promoted(repo)
        self.assertEqual(code, 0, payload)
        self.assertIs(payload.get("ok"), True, payload)
        self.commit(repo, {"skill/SKILL.md": "# skill three\n"}, "late kit change")
        code, payload = self.promoted(repo)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("violations"), ["skill/SKILL.md"], payload)


if __name__ == "__main__":
    unittest.main()

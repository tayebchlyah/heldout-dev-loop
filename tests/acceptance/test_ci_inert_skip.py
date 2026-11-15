#!/usr/bin/env python3
"""Held-out acceptance tests of spec CI Inert Skip, `ci-skip` and `release-check` part: one test per
`## Acceptance flows` item it holds (F<n>), plus one test per oracle line no flow holds whole (O<n>). The repo
files part (F3, oracle 14 to 19): `test_ci_inert_skip_repo.py`.

Seams: `devloop.py ci-skip` and `release-check` as processes on scratch git repos, run from the kit source (seam
1), and the declared functions `ci_skip.blocking(..., inert=True)`, `ci_skip.inert_globs` and
`ci_skip.ConfigError`, called in-process on the same scratch repos for the rows that need no CLI. Scratch repos,
fixtures and helpers: `release_candidates_support.py`. Every name in the scratch repos is invented.

Fixture choices the spec leaves open:
- The base on `main` is the Written Release Notes fixture repo (`package.json` 2.1.1 in `version_files`,
  `CHANGELOG.md` at 2.1.1, two kit copies and the MANIFEST, `.claude/settings.json`, `.codex/hooks.json`,
  `src/app.py`), plus `assets/logo.svg`; `inert_paths` as the line says. Each head is a branch from the base, so
  the base is the merge base.
- The `paths` given to `blocking` are `git diff --name-only --no-renames <base> <head>`.
- Oracle 11: the kit-source repo and the upgrade are built as in the Kit Copy Reference tests (`init` of a 2.1.1
  package whose `skill/SKILL.md` has one more line, then `upgrade` from a 3.0.0 package of this repo).
- Oracle 13: the two repos (with and without `inert_paths`) are compared after their own shas and paths are
  replaced by placeholders; in the repo with `inert_paths`, `blocking(..., inert=True)` first shows rule (f)
  allows the asset, so the line proves `release-check` ignores a list `ci-skip` applies.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from release_candidates_support import (DEVLOOP, K1, K2, MANIFEST, REPO_ROOT, InstallerCase, Scratch, put, sha)
from release_security_audit_support import note_text, record_line

sys.path.insert(0, str(REPO_ROOT / "skill" / "scripts"))
import ci_skip  # noqa: E402

ABSENT = object()
LOGO = "assets/logo.svg"
LOGO_OLD = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>\n'
LOGO_NEW = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="6"/></svg>\n'
APP_NEW = "def colour():\n    return 'blue'\n"

PACKAGE = """{
  "name": "acme-widgets",
  "version": "%s",
  "dependencies": {
    "lodash": "4.17.20"
  }
}
"""

OLD_CHANGELOG = """# Changelog

## [2.1.1] - 2026-10-16

### Fixes

- Widget colour no longer resets.
"""

NEW_CHANGELOG = OLD_CHANGELOG.replace("# Changelog\n\n", """# Changelog

## [2.2.0] - 2026-11-15

### Highlights

**Gadget dials.** Gadget dials turn by themselves, so a user sets them once.

""", 1)

KIT_OLD = {K1: "# dev-loop skill one\n", K2: "STAGE = 1\n"}


def config(inert: object = ABSENT, **extra: object) -> str:
    data: dict = {"version": "2.1.1", "harnesses": ["claude", "codex"], "version_files": ["package.json"], **extra}
    if inert is not ABSENT:
        data["inert_paths"] = inert
    return json.dumps(data, indent=2) + "\n"


def manifest(version: str, files: dict[str, str]) -> str:
    return json.dumps({"version": version, "files": {p: sha(t) for p, t in sorted(files.items())}}, indent=2) + "\n"


def run_json(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd, env=env, capture_output=True, text=True,
                            timeout=300)
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        payload = {"unparsed_stdout": result.stdout}
    payload.setdefault("_stderr", result.stderr)
    return result.returncode, payload


class InertRepoCase(Scratch):
    """`make` writes the base on `main`; `head` makes a branch from it."""

    def make(self, dev_loop: str | None, name: str = "acme-widgets") -> None:
        """`dev_loop`: the base `dev-loop.json` text, None for no file."""
        self.repo = self.new_repo(name)
        files: dict[str, str | None] = {
            "package.json": PACKAGE % "2.1.1", "CHANGELOG.md": OLD_CHANGELOG, "src/app.py": "def colour():\n"
            "    return 'red'\n", LOGO: LOGO_OLD, ".claude/settings.json": '{"hooks": {}}\n',
            ".codex/hooks.json": '{"hooks": {}}\n', **KIT_OLD, MANIFEST: manifest("2.1.1", KIT_OLD)}
        if dev_loop is not None:
            files["dev-loop.json"] = dev_loop
        self.base = self.commit(self.repo, files, "base")

    def head(self, name: str, files: dict[str, str | None], stay: bool = False) -> str:
        self.git(self.repo, "checkout", "-q", "-b", name, self.base)
        rev = self.commit(self.repo, files, name)
        if not stay:
            self.git(self.repo, "checkout", "-q", "main")
        return rev

    def blocking(self, head: str) -> list[str]:
        paths = self.git(self.repo, "diff", "--name-only", "--no-renames", self.base, head).splitlines()
        self.assertTrue(paths)
        return ci_skip.blocking(self.repo, self.base, head, paths, inert=True)

    def cli(self, *args: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
        return run_json(list(args), self.repo, env or self.env)

    def ci_skip(self, head: str) -> dict:
        code, payload = self.cli("ci-skip", "--base", self.base, "--head", head, "--repo", str(self.repo))
        self.assertEqual(code, 0, payload)
        return payload


# ---------------------------------------------------------------------------------------------------------------
# F1, F2: inert PR, widened list (oracle 1, 12, 5)
# ---------------------------------------------------------------------------------------------------------------

class InertFlows(InertRepoCase):

    def test_f1_inert_pr_skips_ci(self) -> None:
        """F1 (oracle 1, 12): merge base `inert_paths` ["assets/*"], a branch changes only `assets/logo.svg`;
        `ci-skip --base main --github-output` on the branch: exit 0, `skip` true, `kind` inert, `blocking` [];
        the output file ends with `skip=true` and a newline, and no line starts with `kind=`."""
        self.make(config(["assets/*"]))
        self.head("asset", {LOGO: LOGO_NEW}, stay=True)
        out = self.root / "github-output"
        out.write_text("")
        code, payload = self.cli("ci-skip", "--base", "main", "--repo", str(self.repo), "--github-output",
                                 env={**self.env, "GITHUB_OUTPUT": str(out)})
        self.assertEqual(code, 0, payload)
        self.assertEqual((payload.get("skip"), payload.get("kind"), payload.get("blocking")), (True, "inert", []),
                         payload)
        text = out.read_text()
        self.assertTrue(text.endswith("skip=true\n"), repr(text))
        self.assertEqual([line for line in text.splitlines() if line.startswith("kind=")], [], repr(text))

    def test_f2_widened_list_blocks_itself_and_the_source(self) -> None:
        """F2 (oracle 5), abuse: the repo of F1, where rule (f) allows an asset-only branch; a branch widens the
        list to ["*"] and changes `src/app.py`: `skip` false, `blocking` holds `dev-loop.json` and `src/app.py`."""
        self.make(config(["assets/*"]))
        self.assertEqual(self.blocking(self.head("asset", {LOGO: LOGO_NEW})), [], "rule (f) does not allow the asset")
        payload = self.ci_skip(self.head("widen", {"dev-loop.json": config(["*"]), "src/app.py": APP_NEW}))
        self.assertIs(payload.get("skip"), False, payload)
        for path in ("dev-loop.json", "src/app.py"):
            self.assertIn(path, payload.get("blocking") or [], payload)


# ---------------------------------------------------------------------------------------------------------------
# Rule (f) rows, in-process (oracle 2, 3, 4, 6, 7, 8, 9)
# ---------------------------------------------------------------------------------------------------------------

class RuleRows(InertRepoCase):

    def test_o2_source_next_to_an_asset_blocks(self) -> None:
        """O2: as oracle 1, plus `src/app.py`: `blocking` ["src/app.py"]."""
        self.make(config(["assets/*"]))
        self.assertEqual(self.blocking(self.head("mixed", {LOGO: LOGO_NEW, "src/app.py": APP_NEW})), ["src/app.py"])

    def test_o3_star_matches_a_slash(self) -> None:
        """O3: merge base ["*"], the diff changes only `docs/guide/x.md`: no blocking path (`*` matches `/`)."""
        self.make(config(["*"]))
        self.assertEqual(self.blocking(self.head("doc", {"docs/guide/x.md": "# Guide\n"})), [])

    def test_o4_a_list_the_pr_adds_is_not_read(self) -> None:
        """O4, abuse: no `inert_paths` at the merge base; the PR adds ["assets/*"] and changes the asset:
        `blocking` holds `assets/logo.svg` and `dev-loop.json`."""
        self.make(config())
        found = self.blocking(self.head("add-list", {"dev-loop.json": config(["assets/*"]), LOGO: LOGO_NEW}))
        for path in (LOGO, "dev-loop.json"):
            self.assertIn(path, found)

    def test_o6_merged_config_kit_copy_and_manifest_are_never_inert(self) -> None:
        """O6, abuse: merge base ["*"]; the diff changes `.claude/settings.json`, `.codex/hooks.json`,
        `.agents/skills/dev-loop/SKILL.md` and `.dev-loop/MANIFEST.json`: each of the four blocks."""
        self.make(config(["*"]))
        new_kit = {**KIT_OLD, K1: "# dev-loop skill two\n"}
        found = self.blocking(self.head("kit-and-hooks", {
            ".claude/settings.json": '{"hooks": {"Stop": []}}\n', ".codex/hooks.json": '{"hooks": {"Stop": []}}\n',
            K1: new_kit[K1], MANIFEST: manifest("2.1.1", new_kit)}))
        for path in (".claude/settings.json", ".codex/hooks.json", K1, MANIFEST):
            self.assertIn(path, found)

    def test_o7_dev_loop_json_is_never_inert(self) -> None:
        """O7, abuse: merge base ["*"]; the diff changes `dev-loop.json` `commands.gate`: `blocking`
        ["dev-loop.json"]."""
        self.make(config(["*"], commands={"gate": "make test"}))
        head = self.head("gate", {"dev-loop.json": config(["*"], commands={"gate": "true"})})
        self.assertEqual(self.blocking(head), ["dev-loop.json"])

    def test_o8_no_list_means_no_inert_path(self) -> None:
        """O8: merge base with no `dev-loop.json`, with `{}`, and with `{"inert_paths": []}`: a diff of only
        `assets/logo.svg` gives `blocking` ["assets/logo.svg"]; `inert_globs` of the base is []."""
        for label, text in (("no file", None), ("empty object", "{}\n"), ("empty list", config([]))):
            with self.subTest(label):
                self.make(text, name="acme-" + label.replace(" ", "-"))
                self.assertEqual(ci_skip.inert_globs(self.repo, self.base), [])
                self.assertEqual(self.blocking(self.head("asset", {LOGO: LOGO_NEW})), [LOGO])

    def test_o9_a_bad_list_shape_fails(self) -> None:
        """O9: merge base `inert_paths` "assets/*", ["assets/"], [""], ["  "], [1]: `inert_globs` of the base
        raises `ConfigError`; for the string, `ci-skip` exits 1 with `error`."""
        for n, bad in enumerate(("assets/*", ["assets/"], [""], ["  "], [1])):
            with self.subTest(bad=bad):
                self.make(config(bad), name=f"acme-bad-{n}")
                with self.assertRaises(ci_skip.ConfigError):
                    ci_skip.inert_globs(self.repo, self.base)
                if n == 0:
                    head = self.head("asset", {LOGO: LOGO_NEW})
                    code, payload = self.cli("ci-skip", "--base", self.base, "--head", head, "--repo",
                                             str(self.repo))
                    self.assertEqual(code, 1, payload)
                    self.assertIn("error", payload)


# ---------------------------------------------------------------------------------------------------------------
# Kind order with earlier skip kinds (oracle 10, 11)
# ---------------------------------------------------------------------------------------------------------------

class KindRows(InertRepoCase):

    def test_o10_release_with_an_asset_stays_release(self) -> None:
        """O10 (with Written Release Notes oracle 8): the release diff (new `## [2.2.0]` section, `package.json`
        version only) plus `assets/logo.svg`, merge base ["assets/*"]: `skip` true, `kind` release."""
        self.make(config(["assets/*"]))
        payload = self.ci_skip(self.head("release", {"CHANGELOG.md": NEW_CHANGELOG, "package.json": PACKAGE % "2.2.0",
                                                     LOGO: LOGO_NEW}))
        self.assertEqual((payload.get("skip"), payload.get("kind"), payload.get("blocking")), (True, "release", []),
                         payload)


SOURCE_FILES = ("lib/kit.mjs", "lib/manifest.mjs")
SOURCE_FOLDERS = ("skill", "skills", "claude", "codex")


class KitUpgradeRow(InstallerCase):

    def test_o11_kit_upgrade_with_an_asset_stays_kit_upgrade(self) -> None:
        """O11 (with Kit Copy Reference oracle 1), kit-source repo: the kit upgrade diff (`kind` kit-upgrade) plus
        `assets/logo.svg`, merge base ["assets/*"]: `skip` true, `kind` kit-upgrade."""
        repo = self.new_repo("dev-loop")
        for rel in SOURCE_FILES:
            put(repo / rel, (REPO_ROOT / rel).read_text())
        for folder in SOURCE_FOLDERS:
            shutil.copytree(REPO_ROOT / folder, repo / folder, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        put(repo / "package.json", '{\n  "name": "dev-loop",\n  "version": "2.1.1"\n}\n')
        put(repo / "CHANGELOG.md", OLD_CHANGELOG)
        put(repo / "src" / "app.py", "def colour():\n    return 'red'\n")
        put(repo / LOGO, LOGO_OLD)
        old = self.package("2.1.1")
        with (old / "skill" / "SKILL.md").open("a") as handle:
            handle.write("\nAn older line of the installed kit.\n")
        init = self.install(old, "init", repo, "--yes", "--harness=claude")
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        data = json.loads((repo / "dev-loop.json").read_text())
        data.update({"version_files": ["package.json"], "inert_paths": ["assets/*"]})
        put(repo / "dev-loop.json", json.dumps(data, indent=2) + "\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "A: kit 2.1.1")
        base = self.git(repo, "rev-parse", "HEAD")

        self.git(repo, "checkout", "-q", "-b", "upgrade")
        upgrade = self.install(self.package("3.0.0"), "upgrade", repo, "--yes")
        self.assertEqual(upgrade.returncode, 0, upgrade.stdout + upgrade.stderr)
        upgraded = json.loads((repo / "dev-loop.json").read_text())
        self.assertEqual(upgraded.get("inert_paths"), ["assets/*"], "fixture: upgrade dropped inert_paths")
        put(repo / LOGO, LOGO_NEW)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "U: kit 3.0.0 and a new logo")
        head = self.git(repo, "rev-parse", "HEAD")
        self.git(repo, "checkout", "-q", "main")

        code, payload = run_json(["ci-skip", "--base", base, "--head", head, "--repo", str(repo)], repo, self.env)
        self.assertEqual(code, 0, payload)
        self.assertEqual((payload.get("skip"), payload.get("kind"), payload.get("blocking")),
                         (True, "kit-upgrade", []), payload)


# ---------------------------------------------------------------------------------------------------------------
# release-check ignores the list (oracle 13)
# ---------------------------------------------------------------------------------------------------------------

class ReleaseCheckRow(InertRepoCase):

    def release_check_after_asset(self, inert: object, name: str) -> tuple[int, str]:
        """Record at the base, HEAD changes only the asset; the `release-check --sha HEAD` exit and JSON with this
        repo's shas and paths as placeholders."""
        self.make(config(inert), name=name)
        put(self.notes / "Security Audit" / "Run 1" / "run-metadata.json",
            json.dumps({"source_ref": {"commit": self.base}, "profile": "quick", "scope_paths": [],
                        "run_status": "complete"}) + "\n")
        put(self.notes / "Project Note.md", note_text([record_line(1, self.base)], []))
        head = self.commit(self.repo, {LOGO: LOGO_NEW}, "new logo")
        if inert is not ABSENT:
            self.assertEqual(self.blocking(head), [], "rule (f) does not allow the asset")
        code, payload = self.cli("release-check", "--sha", "HEAD")
        payload.pop("_stderr", None)
        text = json.dumps(payload, sort_keys=True)
        for rev, mark in ((self.base, "<record>"), (head, "<head>")):
            text = re.sub(rev[:7] + "[0-9a-f]*", mark, text)
        for path in (str(self.repo), str(self.notes)):
            text = text.replace(path, "<path>")
        return code, text.replace(name, "<name>")

    def test_o13_release_check_ignores_inert_paths(self) -> None:
        """O13: record at the base, a later commit changes only `assets/logo.svg`: `release-check --sha HEAD`
        gives the same exit and JSON with `inert_paths` ["assets/*"] at the record commit and at HEAD as with no
        `inert_paths`."""
        self.notes = self.root / "notes" / "Acme Widgets"
        self.notes.mkdir(parents=True)
        self.env["DEV_LOOP_NOTES"] = str(self.notes)
        plain = self.release_check_after_asset(ABSENT, "acme-plain")
        listed = self.release_check_after_asset(["assets/*"], "acme-listed")
        self.assertEqual(listed, plain)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Held-out oracle tests for the stack model (spec: 20261112 Stack Model), the `verify-init` and `drive` seam.

F3 and the oracle lines of the Playwright folder lookup; each docstring names the `## Acceptance oracle` lines
(O<n>) it checks. Seams and fixture: `stack_model_support.py`. The drive run of F3 needs Playwright and Chromium
for this repo (`npm ci && npx playwright install chromium`): the scratch repo links its `web/node_modules` to this
repo's `node_modules`, and the processes keep the user's `HOME`, where Playwright keeps its browsers.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from stack_model_support import (NODE_MODULES, PageServer, ScratchCase, commit_all, fill_verify_skill, init_repo,
                                 listed_paths, page, put)

NO_KEY = object()


class PlaywrightFolderCase(ScratchCase):
    def setUp(self) -> None:
        super().setUp()
        if "HOME" in os.environ:
            self.env["HOME"] = os.environ["HOME"]

    def make_repo(self, name: str, source_dirs: object, packages: tuple[str, ...] = ()) -> Path:
        """A repo on `main` whose `dev-loop.json` has `"capabilities": ["ui"]` and that `source_dirs` value
        (NO_KEY: no key); each of `packages` is a fake Playwright package folder (`<folder>/node_modules/<pkg>`)."""
        repo = self.root / name
        init_repo(repo, self.env)
        config: dict[str, object] = {"version": "3.0.0", "harnesses": ["claude", "codex"], "capabilities": ["ui"]}
        if source_dirs is not NO_KEY:
            config["source_dirs"] = source_dirs
        put(repo / "dev-loop.json", json.dumps(config, indent=2) + "\n")
        put(repo / ".gitignore", "node_modules\n")
        put(repo / "README.md", "# Kettle site\n")
        for package in packages:
            put(repo / package / "package.json", json.dumps({"name": Path(package).name, "version": "1.0.0"}) + "\n")
        commit_all(repo, self.env, "Base")
        return repo

    def verify_key(self, repo: Path) -> dict:
        return json.loads((repo / "dev-loop.json").read_text()).get("verify")


class VerifyInitTests(PlaywrightFolderCase):
    def test_o10_o11_o12_o19_verify_init_records_the_playwright_folder(self) -> None:
        """O10: Playwright only in `web/`, a `source_dirs` entry: `"playwright": "web"` after `base_url`. O11: in the
        root and in `web/`: `.`. O12: in no root or `source_dirs` folder: no field, the rest unchanged. O19: no
        `source_dirs` key, Playwright in the default UI folder: that folder."""
        url = "http://127.0.0.1:4173"
        ui_folder = "front" + "end"
        cases = (
            (10, "web-only", ["web/"], ("web/node_modules/playwright",), "web"),
            (11, "root-and-web", ["web/"],
             ("node_modules/@playwright/test", "web/node_modules/@playwright/test"), "."),
            (12, "unlisted-folder", ["web/"], ("tools/node_modules/playwright",), None),
            (19, "default-folders", NO_KEY, (f"{ui_folder}/node_modules/playwright",), ui_folder),
        )
        for oracle, name, source_dirs, packages, expected in cases:
            repo = self.make_repo(name, source_dirs, packages)
            code, payload = self.verify_init(repo, "--base-url", url)
            with self.subTest(oracle=oracle, packages=packages):
                self.assertEqual(code, 0, payload)
                skill = payload.get("skill")
                self.assertTrue(skill, payload)
                want = {"skill": skill, "base_url": url}
                if expected is not None:
                    want["playwright"] = expected
                self.assertEqual(payload.get("config"), want, payload)
                verify = self.verify_key(repo)
                self.assertEqual(verify, want, verify)
                self.assertEqual(list(verify), list(want), verify)
                self.assertTrue((repo / ".agents" / "skills" / skill / "SKILL.md").is_file(), skill)


class WebFolderFlowTests(PlaywrightFolderCase):
    def test_f3_ui_in_a_folder_that_is_not_the_default(self) -> None:
        """F3, O10, O13; with the Verify Skill (`verify-init`, `drive --all`): Playwright in `web/` only,
        `verify-init` writes `"playwright": "web"` and `drive` runs the map from it; with Playwright moved to the
        root `node_modules`, `drive` fails and names only the two `web/` paths."""
        self.assertTrue((NODE_MODULES / "playwright").is_dir() or (NODE_MODULES / "@playwright" / "test").is_dir(),
                        "Playwright missing from this repo: run npm ci && npx playwright install chromium")
        server = PageServer({"/": page("<h1>Kettle</h1>")})
        self.addCleanup(server.close)
        repo = self.make_repo("kettle-site", ["web/"])
        put(repo / "web" / "src" / "home.ts", "export const title = 'Kettle';\n")
        os.symlink(NODE_MODULES, repo / "web" / "node_modules", target_is_directory=True)
        commit_all(repo, self.env, "Add the web app")

        code, payload = self.verify_init(repo, "--base-url", server.url)
        with self.subTest(oracle=10):
            self.assertEqual(code, 0, payload)
            name = payload.get("skill")
            want = {"skill": name, "base_url": server.url, "playwright": "web"}
            self.assertEqual(payload.get("config"), want, payload)
            self.assertEqual(self.verify_key(repo), want)
        name = payload.get("skill")
        self.assertTrue(name, payload)
        fill_verify_skill(repo, name, "/", "Kettle", ["web/src/*"])
        commit_all(repo, self.env, "Add the verify skill")

        code, payload = self.drive(repo, "--all")
        with self.subTest(flow=3, step="Playwright in web/"):
            self.assertEqual(code, 0, payload)
            self.assertIs(payload.get("ok"), True, payload)
            routes = [r for r in payload.get("routes", []) if r.get("path") == "/"]
            self.assertEqual(len(routes), 1, payload)
            self.assertIs(routes[0].get("ok"), True, routes)

        (repo / "web" / "node_modules").unlink()
        os.symlink(NODE_MODULES, repo / "node_modules", target_is_directory=True)
        code, payload = self.drive(repo, "--all")
        with self.subTest(oracle=13):
            self.assertEqual(code, 1, payload)
            paths = listed_paths(payload.get("error") or "", repo)
            self.assertEqual(sorted(paths), ["web/node_modules/@playwright/test", "web/node_modules/playwright"],
                             payload)


if __name__ == "__main__":
    unittest.main()

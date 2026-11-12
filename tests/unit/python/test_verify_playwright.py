"""verify.playwright: its validation, the verify-init lookup, the drive lookup and the drive.mjs input."""

from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from test_verify import CHECK_SETUP, RUNNER, VALID, Quiet, devloop, entry_text, git, load_verify, make_repo, make_skill, put

LINE = "verify.playwright in dev-loop.json must be a folder path in the repo"
URL = "http://127.0.0.1:4173"
STUB = """const { writeFileSync } = require('node:fs')
writeFileSync(require('node:path').join(process.cwd(), 'loaded.txt'), __dirname)
process.exit(3)
"""


class VerifyPlaywrightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.verify = load_verify()

    def test_config_errors_table(self) -> None:
        repo = make_repo(self.tmp / "app")
        (self.tmp / "outside").mkdir()
        (repo / "link").symlink_to(self.tmp / "outside")
        (repo / "web").mkdir()
        for value in ("/tmp", "../web", "", " ", 5, "link"):
            self.assertEqual(self.verify.config_errors({**VALID, "playwright": value}, repo), [LINE], value)
        for value in ("web", ".", "web/", "nope"):
            self.assertEqual(self.verify.config_errors({**VALID, "playwright": value}, repo), [], value)

    def test_check_setup_line(self) -> None:
        def lines(value: str) -> list[str]:
            repo = make_repo(self.tmp / value.replace("/", "_").replace(".", "dot"), verify={**VALID, "playwright": value})
            (repo / "web").mkdir()
            out = subprocess.run([sys.executable, str(CHECK_SETUP), "--harness", "claude", "--repo", str(repo)],
                                 capture_output=True, text=True).stdout
            return out.splitlines()
        self.assertIn(f"- {LINE}", lines("../web"))
        self.assertNotIn(f"- {LINE}", lines("web"))

    def init(self, name: str, source_dirs: object = "absent", found: tuple[str, ...] = ()) -> tuple[dict, dict]:
        repo = make_repo(self.tmp / name)
        config = json.loads((repo / "dev-loop.json").read_text())
        if source_dirs != "absent":
            config["source_dirs"] = source_dirs
            put(repo / "dev-loop.json", json.dumps(config, indent=2) + "\n")
        for path in found:
            (repo / path).mkdir(parents=True)
        code, out = devloop(repo, "verify-init", "--base-url", URL)
        self.assertEqual(code, 0, out)
        written = json.loads((repo / "dev-loop.json").read_text())["verify"]
        self.assertEqual(out["config"], written)
        return written, out

    def test_init_finds_a_source_dir(self) -> None:
        written, _ = self.init("a", ["web/"], ("web/node_modules/playwright",))
        self.assertEqual(written, {"skill": "verify-a", "base_url": URL, "playwright": "web"})
        self.assertEqual(list(written), ["skill", "base_url", "playwright"])

    def test_init_prefers_the_root(self) -> None:
        written, _ = self.init("a", ["web/"], ("node_modules/@playwright/test", "web/node_modules/@playwright/test"))
        self.assertEqual(written["playwright"], ".")

    def test_init_without_playwright(self) -> None:
        written, out = self.init("a", ["web/"])
        self.assertEqual(written, {"skill": "verify-a", "base_url": URL})
        self.assertEqual(out["config"], {"skill": "verify-a", "base_url": URL})

    def test_init_default_folders(self) -> None:
        written, _ = self.init("a", found=("frontend/node_modules/playwright",))
        self.assertEqual(written["playwright"], "frontend")

    def test_init_source_dirs_order_nested_and_invalid(self) -> None:
        written, _ = self.init("a", ["a/", "packages/web/"], ("packages/web/node_modules/playwright",))
        self.assertEqual(written["playwright"], "packages/web")
        written, _ = self.init("b", ["a/", "packages/web/"],
                               ("a/node_modules/playwright", "packages/web/node_modules/playwright"))
        self.assertEqual(written["playwright"], "a")
        written, _ = self.init("c", ["web"], ("web/node_modules/playwright",))
        self.assertNotIn("playwright", written)


class DriveLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.verify = load_verify()

    def repo(self, playwright: str, script: bool = False, dirs: tuple[str, ...] = ()) -> Path:
        root = make_repo(self.tmp / "app", verify={**VALID, "base_url": self.base_url, "playwright": playwright})
        put(root / "tools/runner.mjs", RUNNER)
        make_skill(root, entries={"a": entry_text(paths=["web/a*"], script="tools/runner.mjs" if script else None)})
        put(root / ".gitignore", "runner-*.json\nnode_modules/\nloaded.txt\n")
        for path in dirs:
            (root / path).mkdir(parents=True, exist_ok=True)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "base")
        return root

    def test_playwright_paths(self) -> None:
        self.assertEqual(self.verify.playwright_paths({}), list(self.verify.PLAYWRIGHT_DIRS))
        self.assertEqual(self.verify.playwright_paths({"playwright": "."}),
                         ["node_modules/playwright", "node_modules/@playwright/test"])
        for value in ("web", "web/"):
            self.assertEqual(self.verify.playwright_paths({"playwright": value}),
                             ["web/node_modules/playwright", "web/node_modules/@playwright/test"])

    def test_drive_looks_only_in_the_folder(self) -> None:
        root = self.repo("web", dirs=("node_modules/playwright", "web"))
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "playwright not found in web/node_modules/playwright, "
                         "web/node_modules/@playwright/test: run npm i -D playwright && npx playwright install "
                         "chromium in the repo root")

    def test_kit_runner_loads_the_configured_folder(self) -> None:
        root = self.repo("web")
        package = root / "web/node_modules/playwright"
        put(package / "package.json", json.dumps({"name": "playwright", "main": "index.js"}))
        put(package / "index.js", STUB)
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 1, out)
        self.assertTrue(out["error"].startswith("runner failed for"), out)
        self.assertEqual(Path((root / "loaded.txt").read_text()).resolve(), package.resolve())

    def test_script_runner_gets_no_playwright_key(self) -> None:
        root = self.repo("web", script=True, dirs=("web/node_modules/playwright",))
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 0, out)
        self.assertNotIn("playwright", json.loads((root / "runner-a.json").read_text()))

    def test_drive_refuses_outside_playwright(self) -> None:
        (self.tmp / "outside/node_modules/playwright").mkdir(parents=True)
        root = self.repo("link")
        (root / "link").symlink_to(self.tmp / "outside")
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 1)
        self.assertIn(LINE, out["error"])


if __name__ == "__main__":
    unittest.main()

"""verify.playwright: its validation, the verify-init lookup, the drive lookup and the drive.mjs input."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_verify import CHECK_SETUP, VALID, devloop, load_verify, make_repo, put

LINE = "verify.playwright in dev-loop.json must be a folder path in the repo"
URL = "http://127.0.0.1:4173"


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


if __name__ == "__main__":
    unittest.main()

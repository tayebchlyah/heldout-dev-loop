#!/usr/bin/env python3
"""Tests for the verify skill: verify.py rules, check_setup.py lines, devloop.py verify-init and drive."""

from __future__ import annotations

import importlib.util
import http.server
import threading
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "skill" / "scripts"
CHECK_SETUP = SCRIPTS / "check_setup.py"
DEVLOOP = SCRIPTS / "devloop.py"

GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main",
       "-c", "commit.gpgsign=false"]


def load_verify():
    spec = importlib.util.spec_from_file_location("verify_for_tests", SCRIPTS / "verify.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load verify module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


SKILL_TEXT = """---
name: verify-shop
description: Launch, check, drive and clean up the shop.
---

# verify-shop

## Launch
uv run shop up

## Doctor
uv run shop doctor

## Drive
devloop.py drive

## Evidence
The evidence folder.

## Cleanup
uv run shop down
"""


def entry_text(routes: object = None, paths: object = None, script: str | None = None) -> str:
    routes = [{"path": "/clusters", "names": [{"role": "heading", "name": "Clusters"}]}] if routes is None else routes
    lines = ["---", f"routes: {json.dumps(routes)}", f"paths: {json.dumps(paths or ['web/*'])}"]
    if script:
        lines.append(f"script: {json.dumps(script)}")
    return "\n".join(lines + ["---", "", "# Clusters", "", "The cluster list.", ""])


def make_repo(root: Path, verify: object = "absent", stacks: str | None = "python, ui",
              harnesses: list[str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    config: dict = {"version": "2.2.0", "harnesses": harnesses or ["claude", "codex"]}
    if verify != "absent":
        config["verify"] = verify
    put(root / "dev-loop.json", json.dumps(config, indent=2) + "\n")
    put(root / "AGENTS.md", f"# AGENTS.md\n\nstacks: {stacks}\n" if stacks else "# AGENTS.md\n")
    return root


def make_skill(root: Path, entries: dict[str, str] | None = None, link: bool = True) -> None:
    skill = root / ".agents" / "skills" / "verify-shop"
    put(skill / "SKILL.md", SKILL_TEXT)
    put(skill / "features" / "README.md", "# Feature map\n\n- clusters.md\n")
    for name, text in (entries if entries is not None else {"clusters": entry_text()}).items():
        put(skill / "features" / f"{name}.md", text)
    if link:
        (root / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (root / ".claude" / "skills" / "verify-shop").symlink_to("../../.agents/skills/verify-shop")


VALID = {"skill": "verify-shop", "base_url": "http://127.0.0.1:4173"}


def verify_lines(root: Path, words: tuple[str, ...] = ("verify", "base_url")) -> list[str]:
    out = subprocess.run([sys.executable, str(CHECK_SETUP), "--harness", "claude", "--repo", str(root)],
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if any(w in line for w in words)]


class VerifyCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.verify = load_verify()

    def test_ui_stack_without_verify_names_verify_init(self) -> None:
        root = make_repo(self.tmp / "app")
        self.assertIn(f"- {self.verify.MISSING}", verify_lines(root))
        self.assertIn("verify-init", self.verify.MISSING)

    def test_verify_false_and_python_stack_are_silent(self) -> None:
        self.assertEqual(verify_lines(make_repo(self.tmp / "a", verify=False)), [])
        self.assertEqual(verify_lines(make_repo(self.tmp / "b", stacks="python")), [])
        self.assertEqual(verify_lines(make_repo(self.tmp / "c", stacks=None)), [])

    def test_filled_skill_with_one_entry_is_silent(self) -> None:
        root = make_repo(self.tmp / "app", verify=VALID)
        make_skill(root)
        self.assertEqual(self.verify.check(root), [])
        self.assertEqual(verify_lines(root), [])

    def test_skeleton_fails_on_marker_and_empty_map(self) -> None:
        root = make_repo(self.tmp / "app", verify=VALID)
        make_skill(root, entries={})
        skill_md = root / ".agents/skills/verify-shop/SKILL.md"
        skill_md.write_text(SKILL_TEXT.replace("uv run shop up", self.verify.FILL))
        lines = self.verify.check(root)
        self.assertIn(f"fill marker {self.verify.FILL} in .agents/skills/verify-shop/SKILL.md", lines)
        self.assertIn("no feature map entry under .agents/skills/verify-shop/features/", lines)

    def test_skill_md_name_and_sections(self) -> None:
        root = make_repo(self.tmp / "app", verify=VALID)
        make_skill(root)
        skill_md = root / ".agents/skills/verify-shop/SKILL.md"
        skill_md.write_text(SKILL_TEXT.replace("name: verify-shop", "name: other").replace("## Doctor", "## Doc"))
        lines = self.verify.check(root)
        self.assertIn(".agents/skills/verify-shop/SKILL.md lacks frontmatter name: verify-shop", lines)
        self.assertIn(".agents/skills/verify-shop/SKILL.md lacks the H2 section Doctor", lines)

    def test_fill_marker_under_features_is_named(self) -> None:
        root = make_repo(self.tmp / "app", verify=VALID)
        make_skill(root)
        put(root / ".agents/skills/verify-shop/features/README.md", f"# Feature map\n{self.verify.FILL}\n")
        self.assertEqual(self.verify.check(root),
                         [f"fill marker {self.verify.FILL} in .agents/skills/verify-shop/features/README.md"])

    def test_bad_entries_name_the_file(self) -> None:
        cases = {
            "not_list": entry_text(routes={"path": "/x"}),
            "no_slash": entry_text(routes=[{"path": "clusters", "names": []}]),
            "no_role": entry_text(routes=[{"path": "/c", "names": [{"name": "Clusters"}]}]),
            "empty_paths": entry_text().replace('paths: ["web/*"]', "paths: []"),
            "dir_path": entry_text(paths=["web/"]),
            "not_json": entry_text().replace('paths: ["web/*"]', "paths: web/*"),
            "no_front": "# Clusters\n",
            "no_script": entry_text(script="tools/missing.mjs"),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                root = make_repo(self.tmp / name, verify=VALID)
                make_skill(root, entries={name: text})
                lines = self.verify.check(root)
                self.assertEqual(len(lines), 1, lines)
                self.assertTrue(lines[0].startswith(f"bad feature map entry .agents/skills/verify-shop/features/{name}.md: "),
                                lines)

    def test_entry_names_default_to_empty(self) -> None:
        root = make_repo(self.tmp / "app", verify=VALID)
        make_skill(root, entries={"c": entry_text(routes=[{"path": "/c"}])})
        entry, reason = self.verify.load_entry(root, root / ".agents/skills/verify-shop/features/c.md")
        self.assertIsNone(reason)
        self.assertEqual(entry["routes"], [{"path": "/c", "names": []}])

    def test_bad_config_names_the_field(self) -> None:
        cases = {
            "verify.base_url": {**VALID, "base_url": "localhost:5173"},
            "verify.setup": {**VALID, "setup": "make login"},
            "verify.storage_state": {**VALID, "storage_state": "/abs/state.json"},
            "verify.skill": {**VALID, "skill": "Bad_Name"},
            "verify in dev-loop.json": ["x"],
        }
        for field, verify in cases.items():
            with self.subTest(field=field):
                root = make_repo(self.tmp / field, verify=verify)
                lines = self.verify.check(root)
                self.assertEqual(len(lines), 1, lines)
                self.assertIn(field, lines[0])

    def test_skill_without_skill_md_names_the_path(self) -> None:
        root = make_repo(self.tmp / "app", verify=VALID)
        (root / ".agents/skills/verify-shop").mkdir(parents=True)
        self.assertIn("missing .agents/skills/verify-shop/SKILL.md (verify.skill)", self.verify.check(root))

    def test_missing_claude_link_is_named(self) -> None:
        root = make_repo(self.tmp / "app", verify=VALID)
        make_skill(root, link=False)
        self.assertEqual(self.verify.check(root),
                         [".claude/skills/verify-shop must link to ../../.agents/skills/verify-shop"])
        codex = make_repo(self.tmp / "codex", verify=VALID, harnesses=["codex"])
        make_skill(codex, link=False)
        self.assertEqual(self.verify.check(codex), [])


def devloop(cwd: Path, *args: str, env: dict | None = None) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd, capture_output=True, text=True,
                            env={**os.environ, **(env or {})})
    return result.returncode, json.loads(result.stdout)


def snapshot(root: Path) -> dict[str, str]:
    """Every path under root except .git, with its content or link target."""
    out = {}
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        rel = path.relative_to(root).as_posix()
        out[rel] = f"link:{os.readlink(path)}" if path.is_symlink() else path.read_text() if path.is_file() else "dir"
    return out


class VerifyInitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.verify = load_verify()

    def test_writes_skeleton_link_and_config(self) -> None:
        root = make_repo(self.tmp / "Shop App")
        config = json.loads((root / "dev-loop.json").read_text())
        code, out = devloop(root, "verify-init", "--base-url", "http://127.0.0.1:4173")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["skill"], "verify-shop-app")
        self.assertEqual(out["config"], {"skill": "verify-shop-app", "base_url": "http://127.0.0.1:4173"})
        skill = root / ".agents/skills/verify-shop-app"
        text = (skill / "SKILL.md").read_text()
        self.assertEqual(self.verify.frontmatter(text)["name"], "verify-shop-app")
        for section in self.verify.SECTIONS:
            self.assertRegex(text, rf"(?m)^## {section}$")
        self.assertIn(self.verify.FILL, text)
        self.assertIn(self.verify.FILL, (skill / "features/README.md").read_text())
        self.assertEqual(os.readlink(root / ".claude/skills/verify-shop-app"), "../../.agents/skills/verify-shop-app")
        written = (root / "dev-loop.json").read_text()
        self.assertEqual(json.loads(written), {**config, "verify": out["config"]})
        self.assertEqual(list(json.loads(written)), [*config, "verify"])
        self.assertTrue(written.startswith('{\n  "version"'))
        self.assertEqual(sorted(out["files"]), sorted([
            ".agents/skills/verify-shop-app/SKILL.md", ".agents/skills/verify-shop-app/features/README.md",
            ".claude/skills/verify-shop-app", "dev-loop.json"]))

    def test_codex_only_writes_no_link(self) -> None:
        root = make_repo(self.tmp / "kiosk", harnesses=["codex"])
        code, out = devloop(root, "verify-init", "--base-url", "https://kiosk.test")
        self.assertEqual(code, 0, out)
        self.assertFalse((root / ".claude").exists())
        self.assertNotIn(".claude/skills/verify-kiosk", out["files"])

    def test_refuses_and_writes_nothing(self) -> None:
        cases = {
            "verify_present": (lambda r: put(r / "dev-loop.json", json.dumps({"harnesses": ["claude"], "verify": False}))),
            "folder_present": (lambda r: (r / ".agents/skills/verify-app").mkdir(parents=True)),
            "link_present": (lambda r: ((r / ".claude/skills").mkdir(parents=True),
                                         (r / ".claude/skills/verify-app").symlink_to("../../gone"))),
            "no_config": (lambda r: (r / "dev-loop.json").unlink()),
        }
        for name, prepare in cases.items():
            with self.subTest(name=name):
                root = make_repo(self.tmp / name / "app")
                prepare(root)
                before = snapshot(root)
                code, out = devloop(root, "verify-init", "--base-url", "http://127.0.0.1:4173")
                self.assertEqual(code, 1, out)
                self.assertIn("error", out)
                self.assertEqual(snapshot(root), before)
        for args in (["--name", "Bad_Name", "--base-url", "http://x"], ["--base-url", "ftp://x"]):
            with self.subTest(args=args):
                root = make_repo(self.tmp / args[-1].replace("/", "_") / "app")
                before = snapshot(root)
                code, out = devloop(root, "verify-init", *args)
                self.assertEqual(code, 1, out)
                self.assertIn("error", out)
                self.assertEqual(snapshot(root), before)

    def test_usage_error_exits_2(self) -> None:
        root = make_repo(self.tmp / "app")
        self.assertEqual(devloop(root, "verify-init")[0], 2)

    def test_skeleton_then_check_flags_marker_and_empty_map(self) -> None:
        root = make_repo(self.tmp / "app")
        devloop(root, "verify-init", "--base-url", "http://127.0.0.1:4173")
        lines = self.verify.check(root)
        self.assertIn(f"fill marker {self.verify.FILL} in .agents/skills/verify-app/SKILL.md", lines)
        self.assertIn("no feature map entry under .agents/skills/verify-app/features/", lines)
        self.assertNotIn(self.verify.MISSING, lines)


# A runner script: records its stdin, answers one ok route per input route.
RUNNER = """import { readFileSync, writeFileSync } from 'node:fs'
const input = JSON.parse(readFileSync(0, 'utf8'))
writeFileSync(`runner-${input.entry}.json`, JSON.stringify(input))
console.log(JSON.stringify({ routes: input.routes.map((r, i) => ({ entry: input.entry, path: r.path, ok: true,
  status: 200, missing: [], console_errors: [], error: null, screenshot: `${input.evidence}/${input.entry}-${i + 1}.jpg`,
  seconds: 0 })) }))
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass


class DriveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def repo(self, verify: dict | None = None, entries: dict[str, list[str]] | None = None,
             playwright: bool = True) -> Path:
        root = make_repo(self.tmp / "app", verify={**VALID, "base_url": self.base_url, **(verify or {})})
        put(root / "tools/runner.mjs", RUNNER)
        make_skill(root, entries={name: entry_text(paths=paths, script="tools/runner.mjs")
                                  for name, paths in (entries or {"a": ["web/a*"], "b": ["web/b/*"]}).items()})
        put(root / ".gitignore", "runner-*.json\nnode_modules/\n")
        if playwright:
            (root / "node_modules/playwright").mkdir(parents=True)
        git(root, "add", "-A")
        git(root, "commit", "-qm", "base")
        git(root, "switch", "-qc", "feat/x")
        return root

    def commit(self, root: Path, *paths: str) -> str:
        for path in paths:
            put(root / path, "change\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "change")
        return git(root, "rev-parse", "HEAD").strip()

    def test_no_verify_skips(self) -> None:
        for verify in ("absent", False):
            with self.subTest(verify=verify):
                root = make_repo(self.tmp / str(verify), verify=verify)
                self.assertEqual(devloop(root, "drive"), (0, {"ok": True, "skipped": "no-verify", "routes": []}))

    def test_selection_by_branch_diff_and_entry_file(self) -> None:
        root = self.repo()
        self.commit(root, "web/a.ts")
        code, out = devloop(root, "drive")
        self.assertEqual((code, out["selected"]), (0, ["a"]), out)
        self.assertEqual([r["entry"] for r in out["routes"]], ["a"])
        put(root / ".agents/skills/verify-shop/features/b.md",
            entry_text(paths=["web/b/*"], script="tools/runner.mjs") + "## Gotchas\n\nNone.\n")
        self.commit(root)
        self.assertEqual(devloop(root, "drive")[1]["selected"], ["a", "b"])
        self.assertEqual(devloop(root, "drive", "--all")[1]["selected"], ["a", "b"])

    def test_non_ascii_changed_path_selects_entry(self) -> None:
        root = self.repo(entries={"a": ["web/*"]})
        self.commit(root, "web/récap.ts")
        code, out = devloop(root, "drive")
        self.assertEqual((code, out["selected"]), (0, ["a"]), out)

    def test_since_selects_fix_only(self) -> None:
        root = self.repo()
        sha = self.commit(root, "web/a.ts")
        self.commit(root, "web/b/x.ts")
        self.assertEqual(devloop(root, "drive", "--since", sha)[1]["selected"], ["b"])
        code, out = devloop(root, "drive", "--since", "nope")
        self.assertEqual(code, 1)
        self.assertIn("--since", out["error"])

    def test_no_match_is_ok_empty(self) -> None:
        root = self.repo()
        self.commit(root, "docs/x.md")
        code, out = devloop(root, "drive")
        self.assertEqual(code, 0)
        self.assertEqual((out["ok"], out["selected"], out["routes"]), (True, [], []))

    def test_unreachable_base_url_runs_no_setup(self) -> None:
        root = self.repo(verify={"base_url": "http://127.0.0.1:9", "setup": ["touch setup-ran"]})
        code, out = devloop(root, "drive", "--all", env={"DEVLOOP_TEST_READY_WAIT": "1"})
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "base_url not reachable: http://127.0.0.1:9")
        self.assertNotIn("routes", out)
        self.assertFalse((root / "setup-ran").exists())

    def test_failing_setup_names_command(self) -> None:
        root = self.repo(verify={"setup": ["echo first-out", "echo oops; exit 1", "touch never"]})
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 1)
        self.assertIn("echo oops; exit 1", out["error"])
        self.assertIn("oops", out["tail"])
        self.assertNotIn("routes", out)
        self.assertFalse((root / "never").exists())

    def test_script_receives_runner_input(self) -> None:
        root = self.repo(verify={"setup": ["echo '{}' > state.json"], "storage_state": "state.json"})
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 0, out)
        received = json.loads((root / "runner-a.json").read_text())
        self.assertEqual(received, {"base_url": self.base_url, "storage_state": str((root / "state.json").resolve()),
                                    "routes": [{"path": "/clusters", "names": [{"role": "heading", "name": "Clusters"}]}],
                                    "evidence": out["evidence"], "entry": "a"})
        self.assertEqual([r["entry"] for r in out["routes"]], ["a", "b"])

    def test_evidence_drive_json_matches_stdout(self) -> None:
        root = self.repo()
        code, out = devloop(root, "drive", "--all")
        evidence = Path(out["evidence"])
        git_dir = Path(git(root, "rev-parse", "--path-format=absolute", "--git-dir").strip())
        self.assertEqual(evidence.parent, git_dir / "dev-loop" / "evidence")
        self.assertRegex(evidence.name, r"^\d{8}T\d{6}Z$")
        self.assertEqual(json.loads((evidence / "drive.json").read_text()), out)
        self.assertEqual(set(out), {"ok", "selected", "routes", "evidence"})

    def test_runner_crash_names_entry(self) -> None:
        root = self.repo()
        put(root / "tools/runner.mjs", "console.error('crashed here'); process.exit(3)\n")
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "runner failed for a")
        self.assertIn("crashed here", out["tail"])

    def test_missing_storage_state_named(self) -> None:
        root = self.repo(verify={"storage_state": "state.json"})
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 1)
        self.assertIn("state.json", out["error"])

    def test_no_playwright_folder_even_in_parent(self) -> None:
        (self.tmp / "node_modules/playwright").mkdir(parents=True)
        root = self.repo(playwright=False)
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 1)
        self.assertIn("playwright", out["error"])

    def test_all_and_since_is_usage_error(self) -> None:
        root = self.repo()
        self.assertEqual(devloop(root, "drive", "--all", "--since", "HEAD")[0], 2)

    def test_bad_entry_refuses(self) -> None:
        root = self.repo()
        put(root / ".agents/skills/verify-shop/features/c.md", entry_text(routes=[{"path": "nope"}]))
        code, out = devloop(root, "drive", "--all")
        self.assertEqual(code, 1)
        self.assertIn("features/c.md", out["error"])
        self.assertNotIn("routes", out)



def section(text: str, heading: str) -> str:
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


class VerifyTextTests(unittest.TestCase):
    def test_kit_text_names_verify_commands(self) -> None:
        skill = (SCRIPTS.parent / "SKILL.md").read_text()
        stage0 = section(skill, "0 Bootstrap")
        self.assertIn("devloop.py verify-init", stage0)
        self.assertIn("references/verify.md", stage0)
        stage6 = section(skill, "6 Feature gate")
        self.assertIn("devloop.py drive", stage6)
        self.assertIn("drive:", stage6)
        self.assertIn("Report five lines", stage6)
        refs = SCRIPTS.parent / "references"
        verify_md = (refs / "verify.md").read_text()
        for needle in ("## Licenses", "9580c23", "Lauren Tan", "devloop.py verify-init", "drive --all",
                       "Sub-features", "How to get to it (user POV)", "Driving it with", "Gotchas"):
            self.assertIn(needle, verify_md)
        for route in ("claude.md", "codex.md"):
            text = (refs / route).read_text()
            self.assertIn("devloop.py drive", text)
        self.assertIn("model: sonnet", (refs / "claude.md").read_text())
        self.assertNotIn("computer-use", (refs / "codex.md").read_text())


if __name__ == "__main__":
    unittest.main()

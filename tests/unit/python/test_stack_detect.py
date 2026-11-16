"""stack-detect: detection order, skip rules, workspace members, capabilities, source_dirs, unknown."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SKILL_ROOT, write

import stacks
from seal import ACCEPTANCE_DIR

DEVLOOP = SKILL_ROOT / "scripts" / "devloop.py"

FIXTURES: dict[str, dict[str, str]] = {
    "maven": {"pom.xml": ""},
    "gradle": {"build.gradle.kts": ""},
    "go": {"go.mod": ""},
    "uv": {"pyproject.toml": "", "uv.lock": ""},
    "poetry": {"pyproject.toml": "", "poetry.lock": ""},
    "pip": {"requirements.txt": ""},
    "pnpm": {"package.json": "{}", "pnpm-lock.yaml": ""},
    "yarn": {"package.json": "{}", "yarn.lock": ""},
    "npm": {"package.json": "{}"},
    "cargo": {"Cargo.toml": ""},
    "dotnet": {"App.sln": ""},
    "bundler": {"Gemfile": ""},
}


def ws(members: list[str], exclude: list[str] | None = None, project: bool = False) -> dict[str, str]:
    text = '[project]\nname = "x"\n' if project else ""
    text += f"[tool.uv.workspace]\nmembers = {json.dumps(members)}\n"
    if exclude is not None:
        text += f"exclude = {json.dumps(exclude)}\n"
    return {"pyproject.toml": text, "uv.lock": ""}


class StackDetectTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.count = 0

    def tree(self, files: dict[str, str]) -> Path:
        self.count += 1
        repo = self.root / f"repo{self.count}"
        repo.mkdir()
        for rel, text in files.items():
            write(repo / rel, text)
        return repo

    def detect(self, repo: Path) -> dict:
        return stacks.detect(repo, stacks.load_presets())

    def run_cli(self, *args: str) -> tuple[int, dict]:
        done = subprocess.run([sys.executable, str(DEVLOOP), "stack-detect", *args], capture_output=True, text=True)
        return done.returncode, json.loads(done.stdout)

    def units(self, out: dict) -> list[tuple[str, str | None, str]]:
        return [(s["path"], s["preset"], s["unit"]) for s in out["stacks"]]

    def test_every_preset_has_a_fixture(self) -> None:
        names = [p["name"] for p in stacks.load_presets()["presets"]]
        self.assertEqual(sorted(FIXTURES), sorted(names))
        for name, files in FIXTURES.items():
            with self.subTest(preset=name):
                self.assertEqual(self.units(self.detect(self.tree(files))), [(".", name, "root")])

    def test_root_uv(self) -> None:
        out = self.detect(self.tree(FIXTURES["uv"]))
        self.assertEqual(self.units(out), [(".", "uv", "root")])
        self.assertEqual(out["source_dirs"], [])

    def test_root_uv_existing_folders(self) -> None:
        out = self.detect(self.tree({**FIXTURES["uv"], "src/a.py": "", "tests/unit/test_a.py": ""}))
        self.assertEqual(out["source_dirs"], ["src/", "tests/unit/"])

    def test_python_and_js_picks(self) -> None:
        cases = [({"pyproject.toml": ""}, "pip"), ({"pyproject.toml": "", "requirements.txt": ""}, "pip"),
                 ({"package.json": "{}", "yarn.lock": "", "pnpm-lock.yaml": ""}, "pnpm"),
                 ({"settings.gradle": "", "build.gradle": ""}, "gradle"), ({"App.csproj": ""}, "dotnet")]
        for files, name in cases:
            with self.subTest(files=sorted(files)):
                self.assertEqual(self.units(self.detect(self.tree(files))), [(".", name, "root")])

    def test_root_go(self) -> None:
        out = self.detect(self.tree({"go.mod": "", "cmd/app/main.go": ""}))
        self.assertEqual(self.units(out), [(".", "go", "root")])
        self.assertEqual(out["source_dirs"], ["cmd/"])

    def test_two_units(self) -> None:
        out = self.detect(self.tree({"backend/pyproject.toml": "", "backend/uv.lock": "",
                                     "frontend/package.json": '{"dependencies": {"react": "1"}}', "compose.yaml": ""}))
        self.assertEqual(self.units(out), [("backend/", "uv", "backend"), ("frontend/", "npm", "frontend")])
        self.assertEqual((out["capabilities"], out["source_dirs"], out["unknown"]), (["ui", "backend"], ["backend/", "frontend/"], []))

    def test_dev_dependency_ui(self) -> None:
        out = self.detect(self.tree({"package.json": '{"devDependencies": {"@angular/core": "1"}}'}))
        self.assertEqual(out["capabilities"], ["ui"])

    def test_backend_services(self) -> None:
        out = self.detect(self.tree({"backend/api/go.mod": "", "backend/worker/Cargo.toml": ""}))
        self.assertEqual(self.units(out), [("backend/api/", "go", "backend-api"), ("backend/worker/", "cargo", "backend-worker")])
        self.assertEqual(out["source_dirs"], ["backend/api/", "backend/worker/"])

    def test_skipped_folders(self) -> None:
        out = self.detect(self.tree({"node_modules/x/package.json": "{}", "node_modules/package.json": "{}",
                                     ".venv/pyproject.toml": "", f"{ACCEPTANCE_DIR}/package.json": "{}", "a/b/go.mod": ""}))
        self.assertEqual(out["stacks"], [])
        self.assertEqual(out["unknown"], ["a/"])

    def test_empty_repo_via_process(self) -> None:
        self.assertEqual(self.run_cli("--repo", str(self.tree({}))), (0, {"stacks": [], "capabilities": [], "source_dirs": [], "unknown": []}))

    def test_unknown(self) -> None:
        out = self.detect(self.tree({"pyproject.toml": "", "uv.lock": "", "tests/unit/test_x.py": "", "docs/a.md": "",
                                     "scripts/b.sh": "", "cache/.tool/x": "", "deep/a/b/c.txt": ""}))
        self.assertEqual(out["unknown"], ["deep/", "docs/", "scripts/"])

    def test_first_match_wins(self) -> None:
        out = self.detect(self.tree({"pyproject.toml": "", "uv.lock": "", "package.json": "{}"}))
        self.assertEqual(self.units(out), [(".", "uv", "root")])

    def test_workspace_member(self) -> None:
        out = self.detect(self.tree({"pyproject.toml": "", "uv.lock": "", "backend/pyproject.toml": "", "frontend/package.json": "{}"}))
        self.assertEqual([(s["path"], s["preset"]) for s in out["stacks"]], [(".", "uv"), ("frontend/", "npm")])
        self.assertEqual(out["source_dirs"], ["backend/", "frontend/"])

    def test_member_under_a_folder_unit(self) -> None:
        out = self.detect(self.tree({"backend/go.mod": "", "backend/api/go.mod": "", "backend/web/package.json": "{}"}))
        self.assertEqual(self.units(out), [("backend/", "go", "backend"), ("backend/web/", "npm", "backend-web")])
        self.assertEqual(out["source_dirs"], ["backend/", "backend/api/", "backend/web/"])

    def test_root_npm_source_dirs(self) -> None:
        self.assertEqual(self.detect(self.tree(FIXTURES["npm"]))["source_dirs"], [])
        self.assertEqual(self.detect(self.tree({"package.json": "{}", "src/a.js": ""}))["source_dirs"], ["src/"])

    def test_root_dotnet_has_no_source_dirs(self) -> None:
        self.assertEqual(self.detect(self.tree(FIXTURES["dotnet"]))["source_dirs"], [])

    def test_commands_equal_the_preset(self) -> None:
        by = stacks.by_name(stacks.load_presets())
        out = self.detect(self.tree({"backend/pyproject.toml": "", "backend/uv.lock": "", "frontend/package.json": "{}"}))
        for entry in out["stacks"]:
            preset = by[entry["preset"]]
            with self.subTest(preset=entry["preset"]):
                self.assertEqual(entry["commands"], {"lint": preset["lint"], "typecheck": preset["typecheck"], "test": preset["test"],
                                                     "junit": {key: preset["junit"][key] for key in ("command", "files", "package", "setup")}})

    def test_partly_claimed_folder_is_unknown(self) -> None:
        cases = [({"pom.xml": "", "src/main/java/A.java": "", "src/it/java/FooIT.java": ""}, ["src/"]),
                 ({"pyproject.toml": "", "uv.lock": "", "src/pkg/a.py": "", "tests/unit/test_a.py": "",
                   "tests/integration/test_b.py": ""}, ["tests/"]),
                 ({"pom.xml": "", "src/main/java/A.java": "", "src/test/java/ATest.java": ""}, [])]
        for files, unknown in cases:
            with self.subTest(files=sorted(files)):
                self.assertEqual(self.detect(self.tree(files))["unknown"], unknown)

    def test_capabilities_start_from_the_setup_proposal(self) -> None:
        react = '{"dependencies": {"react": "1"}}'
        cases = [({"package.json": react}, ["ui"]),
                 ({"package.json": "{}", "AGENTS.md": "stacks: ui\n"}, ["ui"]),
                 ({"package.json": react, "AGENTS.md": "stacks: ui\n", "compose.yml": ""}, ["ui", "backend"]),
                 ({"package.json": react, "compose.yml": ""}, ["ui", "backend"]),
                 ({"go.mod": "", "AGENTS.md": "stacks: ui\n"}, ["ui"])]
        for files, caps in cases:
            with self.subTest(files=sorted(files)):
                self.assertEqual(self.detect(self.tree(files))["capabilities"], caps)

    def test_git_ignored_folders_are_skipped(self) -> None:
        repo = self.tree({".gitignore": "target/\ndist/\n", "Cargo.toml": "", "target/debug/x.o": "",
                          "dist/package.json": "{}", "docs/a.md": ""})
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        out = self.detect(repo)
        self.assertEqual(self.units(out), [(".", "cargo", "root")])
        self.assertEqual(out["unknown"], ["docs/"])

    def test_bad_package_json(self) -> None:
        for text in ("{not json", '{"dependencies": []}', "[]", '{"dependencies": {"react": "1"'):
            with self.subTest(text=text):
                out = self.detect(self.tree({"frontend/package.json": text}))
                self.assertEqual(self.units(out), [("frontend/", "npm", "frontend")])
                self.assertNotIn("ui", out["capabilities"])

    def test_symlinks(self) -> None:
        repo = self.tree({"backend/pyproject.toml": "", "backend/uv.lock": "", "docs/a.md": ""})
        os.symlink(repo / "docs", repo / "docs" / "loop")
        os.symlink(repo / "backend", repo / "link")
        out = self.detect(repo)
        self.assertEqual(self.units(out), [("backend/", "uv", "backend")])
        self.assertNotIn("link/", json.dumps(out))
        self.assertEqual(out["unknown"], ["docs/"])

    def paths(self, out: dict) -> list[tuple[str, str | None]]:
        return [(s["path"], s["preset"]) for s in out["stacks"]]

    def test_workspace_backend_member(self) -> None:
        out = self.detect(self.tree({**ws(["backend"]), "backend/pyproject.toml": "", "backend/tests/unit/test_a.py": "",
                                     "frontend/package.json": "{}"}))
        self.assertEqual(self.units(out), [("backend/", "uv", "backend"), ("frontend/", "npm", "frontend")])
        self.assertEqual((out["source_dirs"], out["unknown"]), (["backend/", "frontend/"], []))

    def test_workspace_glob_members_and_exclude(self) -> None:
        files = {"packages/a/pyproject.toml": "", "packages/b/pyproject.toml": "", "packages/c/README.md": ""}
        out = self.detect(self.tree({**ws(["packages/*"]), **files}))
        self.assertEqual(self.units(out), [("packages/a/", "uv", "packages-a"), ("packages/b/", "uv", "packages-b")])
        self.assertEqual((out["source_dirs"], out["unknown"]), (["packages/a/", "packages/b/"], ["packages/"]))
        out = self.detect(self.tree({**ws(["packages/*"], exclude=["packages/b"]), **files}))
        self.assertEqual(self.units(out), [("packages/a/", "uv", "packages-a")])

    def test_workspace_root_unit(self) -> None:
        cases = [({"src/x/__init__.py": ""}, [(".", "uv", "root"), ("backend/", "uv", "backend")], ["backend/", "src/"]),
                 ({}, [("backend/", "uv", "backend")], ["backend/"]),
                 ({"tests/unit/test_a.py": ""}, [(".", "uv", "root"), ("backend/", "uv", "backend")], ["backend/", "tests/unit/"])]
        for extra, units, dirs in cases:
            with self.subTest(extra=sorted(extra)):
                out = self.detect(self.tree({**ws(["backend"], project=True), "backend/pyproject.toml": "", **extra}))
                self.assertEqual((self.units(out), out["source_dirs"]), (units, dirs))

    def test_member_outside_rules(self) -> None:
        names = ["backend", "../outside", ".hidden", "node_modules/x", ACCEPTANCE_DIR]
        repo = self.tree({**ws(names), **{f"{n}/pyproject.toml": "" for n in names if n != "../outside"}})
        write(repo.parent / "outside" / "pyproject.toml", "")
        out = self.detect(repo)
        self.assertEqual(self.units(out), [("backend/", "uv", "backend")])
        for entry in out["source_dirs"]:
            self.assertFalse(any(n in entry for n in ("outside", ".hidden", "node_modules")) or entry.startswith(ACCEPTANCE_DIR), entry)

    def test_no_workspace_keeps_todays_rules(self) -> None:
        cases = [({**ws(["backend"]), "backend/a.py": ""}, None),
                 ({"pyproject.toml": "[[not toml", "uv.lock": "", "src/a.py": ""}, ["src/"]),
                 ({"pyproject.toml": "[tool.uv.workspace]\n", "uv.lock": "", "src/a.py": ""}, None),
                 ({"pyproject.toml": "tool = 1\n", "uv.lock": "", "src/a.py": "", "backend/pyproject.toml": ""}, None),
                 ({"pyproject.toml": '[tool]\nuv = "x"\n', "uv.lock": "", "src/a.py": "", "backend/pyproject.toml": ""}, None),
                 ({"pyproject.toml": "[tool.uv]\nworkspace = 1\n", "uv.lock": "", "src/a.py": "", "backend/pyproject.toml": ""}, None),
                 ({"pyproject.toml": '[tool.uv.workspace]\nmembers = ["backend", 1]\n', "uv.lock": "", "src/a.py": "",
                   "backend/pyproject.toml": ""}, None)]
        for files, dirs in cases:
            with self.subTest(root=files["pyproject.toml"]):
                out = self.detect(self.tree(files))
                self.assertNotIn(("backend/", "uv"), self.paths(out))
                if "src/a.py" in files:
                    self.assertEqual(self.units(out), [(".", "uv", "root")])
                if dirs is not None:
                    self.assertEqual(out["source_dirs"], dirs)

    def test_bad_exclude_is_ignored(self) -> None:
        out = self.detect(self.tree({"pyproject.toml": '[tool.uv.workspace]\nmembers = ["backend"]\nexclude = "backend"\n',
                                     "uv.lock": "", "backend/pyproject.toml": ""}))
        self.assertEqual(self.units(out), [("backend/", "uv", "backend")])

    def test_workspace_root_package_json_ui(self) -> None:
        out = self.detect(self.tree({**ws(["backend"]), "backend/pyproject.toml": "", "backend/uv.lock": "",
                                     "package.json": '{"dependencies": {"react": "1"}}'}))
        self.assertIn("ui", out["capabilities"])

    def test_non_member_python_folder_is_a_unit(self) -> None:
        out = self.detect(self.tree({**ws(["packages/a"]), "packages/a/pyproject.toml": "", "tools/pyproject.toml": ""}))
        self.assertEqual(self.paths(out), [("packages/a/", "uv"), ("tools/", "pip")])

    def test_go_root_with_workspace(self) -> None:
        out = self.detect(self.tree({"go.mod": "", "cmd/a.go": "", "pyproject.toml": '[tool.uv.workspace]\nmembers = ["tools"]\n',
                                     "tools/pyproject.toml": ""}))
        self.assertEqual(self.paths(out), [(".", "go"), ("tools/", "uv")])

    def test_bad_member_patterns_and_dedupe(self) -> None:
        out = self.detect(self.tree({**ws(["", ".", "/abs", "backend", "backend/", "*"]), "backend/pyproject.toml": "",
                                     "packages/x.txt": ""}))
        self.assertEqual(self.units(out), [("backend/", "uv", "backend")])
        out = self.detect(self.tree({**ws(["packages/*"]), "packages/x.txt": "", "packages/a/pyproject.toml": ""}))
        self.assertEqual(self.units(out), [("packages/a/", "uv", "packages-a")])

    def test_workspace_symlinks(self) -> None:
        repo = self.tree({**ws(["packages/*", "linked/pkg"]), "real/pkg/pyproject.toml": "", "packages/keep.txt": ""})
        os.symlink(repo / "real" / "pkg", repo / "packages" / "p")
        os.symlink(repo / "real", repo / "linked")
        out = self.detect(repo)
        self.assertFalse([s for s in out["stacks"] if s["path"].startswith(("packages/", "linked/"))], out["stacks"])
        repo = self.tree({**FIXTURES["uv"], "elsewhere/a.py": ""})
        os.symlink(repo / "elsewhere", repo / "src")
        self.assertNotIn("src/", self.detect(repo)["source_dirs"])

    def test_ignored_member(self) -> None:
        repo = self.tree({".gitignore": "build/\n", **ws(["build", "backend"]), "build/pyproject.toml": "",
                          "backend/pyproject.toml": ""})
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self.assertEqual(self.units(self.detect(repo)), [("backend/", "uv", "backend")])

    def test_unit_name(self) -> None:
        self.assertEqual([stacks.unit_name(p) for p in (".", "web/", "backend/api/")], ["root", "web", "backend-api"])

    def test_missing_repo(self) -> None:
        code, out = self.run_cli("--repo", str(self.root / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("is not a folder", out["error"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Held-out oracle tests for the project skills of the walled implementer (spec: Walled Project Skills).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>) it
checks. O16 holds in the denials of F2 and F3, O23 in F1 and F2. `test_o34_kit_text` checks the text line, which no
flow names. Every name in the scratch repos is invented.

Seam 1 (F1 to F4): the hook `deny-acceptance-to-subagents.sh` fed PreToolUse JSON on stdin (exit code and stderr) and
`devloop.py skill-wall` (stdout JSON), on the scratch repo of `tool_walls_support.py`. Seam 2 (F5):
`check_setup.py --harness claude` and `devloop.py merge-check` as processes on scratch projects, `bin/install.mjs
init` and `upgrade`.

Fixture choices the spec leaves open:
- One checkout: `refs/heads/main` gets a fresh tip per case, and the checkout sits on `feat/gizmo` made from it.
  Two checkouts: the main worktree on `main`, a linked worktree on its own branch. The hook process and the hook
  `cwd` are the same folder; `CLAUDE_PROJECT_DIR` stays the main worktree, as in an `EnterWorktree` session.
- `check_setup.py` passes for Claude after `init` once the home holds a `security-audit` skill whose tree id
  `DEVLOOP_TEST_SECURITY_AUDIT_TREE` names and a stub `claude` is on `PATH`, as in `test_ci_follow_up.py`. So O25 can
  read a real exit 0.
- `merge-check` runs with a green ledger at the branch head, so that no `no-review` reason gives the `human` of O26.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import merge_check_binding_support as merge
from test_walled_agents_nested_routes import ALLOWED_SKILLS
from test_walled_agents_no_web import CHECKER, CLOSED
from tool_walls_support import (CLAUDE_HOOKS, CLAUDE_ROUTE, CODEX_ROUTE, INSTALLER, KIT_REFERENCE, NET, REPO_ROOT,
                                ToolWallRepo, git, header, near)

NAME = "pg-guide"
CALL = {"skill": NAME}
SKILL_PATH = f".claude/skills/{NAME}/SKILL.md"
LISTED = (NAME,)
LINK = {f".claude/skills/{NAME}": f"../../.agents/skills/{NAME}"}
LINK_TARGET = f".agents/skills/{NAME}/SKILL.md"
OUTSIDE_FOLDER = "outside-guide"

NOT_LISTED = "not in walled_skills"
NO_COPY = "no copy at the main ref"
DIFFERS = "differs from the main ref"
FORK = "context: fork"
SHELL = "shell line"
HOOKS = "hooks key"


def skill_text(frontmatter: str = "", body: str = "Use the read replica.\n") -> bytes:
    return (f"---\nname: {NAME}\ndescription: How this project uses Postgres.\n{frontmatter}---\n\n"
            f"# Postgres guide\n\n{body}").encode()


PLAIN = skill_text()
ONE_MORE_BYTE = PLAIN + b"x"

# O9 to O12, O32.
UNSAFE_SKILLS = ((9, skill_text("context: fork\n"), FORK),
                 (10, skill_text(body="Today is !`date`.\n"), SHELL),
                 (11, skill_text(body="```!\ndate\n```\n"), SHELL),
                 (12, skill_text("hooks:\n  Stop: []\n"), HOOKS),
                 (32, skill_text('context: "fork"\n'), FORK),
                 (32, skill_text("context: fork # note\n"), FORK),
                 (32, skill_text('"context": fork\n'), FORK),
                 (32, skill_text("context:\n  fork\n"), FORK),
                 (32, skill_text('"hooks": x\n'), HOOKS),
                 (32, b"---\n{hooks: x}\n---\n\n# Postgres guide\n", HOOKS),
                 (32, skill_text(body="Run it with ```! date\n"), SHELL))

# O24.
BAD_LISTS = ("pg-guide", [""], ["a:b"], ["A"], [1])


def user_actions(output: str) -> list[str]:
    lines = output.splitlines()
    heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
    return lines[heads[0] + 1:] if heads else []


class SkillWallRepo(ToolWallRepo):
    def setUp(self) -> None:
        super().setUp()
        self.base = git(self.repo, "rev-parse", "refs/heads/main")
        self.config = json.loads((self.repo / "dev-loop.json").read_text())
        self.worktrees = 0

    def config_bytes(self, listed: tuple[str, ...] | None) -> bytes:
        config = dict(self.config)
        if listed is not None:
            config["walled_skills"] = list(listed)
        return (json.dumps(config, indent=2) + "\n").encode()

    @staticmethod
    def write(folder: Path, files: dict[str, bytes], links: dict[str, str] | None = None) -> None:
        for path, data in files.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_bytes(data)
        for path, target in (links or {}).items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).symlink_to(target)

    def land(self, files: dict[str, bytes] | None = None, listed: tuple[str, ...] | None = LISTED,
             links: dict[str, str] | None = None) -> None:
        """A fresh `refs/heads/main`: the base, `listed` (None: no key), `files` (None: the plain skill), `links`.
        The checkout then sits on `feat/gizmo` at that tip."""
        git(self.repo, "checkout", "-q", "-f", "-B", "main", self.base)
        git(self.repo, "clean", "-fdqx")
        self.write(self.repo, {"dev-loop.json": self.config_bytes(listed),
                               **({SKILL_PATH: PLAIN} if files is None else files)}, links)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "Main tip")
        git(self.repo, "checkout", "-q", "-B", "feat/gizmo", "main")

    def linked(self) -> Path:
        """The main worktree on `main`, and a new linked worktree."""
        git(self.repo, "checkout", "-q", "main")
        self.worktrees += 1
        folder = self.root / "worktrees" / f"gizmo-{self.worktrees}"
        git(self.repo, "worktree", "add", "-q", "-b", f"feat/gizmo-{self.worktrees}", str(folder), "main")
        return folder

    def skill(self, tool_input: dict | None = None, agent: str | None = "implementer",
              cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self.hook("claude", NET, self.pre_tool("Skill", CALL if tool_input is None else tool_input, agent, cwd),
                         cwd=cwd)

    def wall(self, tool_input: dict | None = None) -> tuple[int, dict]:
        payload = self.pre_tool("Skill", CALL if tool_input is None else tool_input, "implementer")
        return self.cli("skill-wall", stdin=json.dumps(payload))

    def assert_open(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_closed(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assert_denied(result)
        self.assertIn(CLOSED, result.stderr)

    def assert_reason(self, result: subprocess.CompletedProcess[str], reason: str) -> None:
        """A D6 denial with its reason, and O16."""
        self.assert_closed(result)
        self.assertIn(reason, result.stderr)
        self.assertIn("mattpocock-skills:tdd", result.stderr)
        self.assertIn("SKILL.md", result.stderr)


class ProjectSkillWallTests(SkillWallRepo):
    def test_f1_a_listed_plain_skill_opens_to_the_implementer(self) -> None:
        """O1, O2, O13, O28, O23 for O1: the hook lets the listed skill through, from the origin ref when there is
        one, through a link, and in a linked worktree."""
        # O1, O23.
        self.land()
        with self.subTest(oracle="O1"):
            self.assert_open(self.skill())
        with self.subTest(oracle="O23", line="O1"):
            self.assertEqual(self.wall(), (0, {"deny": False, "reason": None}))

        # O2: the list and the blob of the origin ref win, each way.
        self.land()
        git(self.repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
        self.land(files={}, listed=None)
        self.write(self.repo, {SKILL_PATH: PLAIN})
        with self.subTest(oracle="O2", main="no list, no skill", origin="both"):
            self.assert_open(self.skill())
        self.land(files={SKILL_PATH: ONE_MORE_BYTE})
        with self.subTest(oracle="O2", main="another blob", origin="the plain blob"):
            self.assert_reason(self.skill(), DIFFERS)
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.base)
        self.land()
        with self.subTest(oracle="O2", main="both", origin="no list, no skill"):
            self.assert_reason(self.skill(), NOT_LISTED)
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")

        # O13.
        self.land(files={LINK_TARGET: PLAIN}, links=LINK)
        with self.subTest(oracle="O13"):
            self.assertTrue((self.repo / ".claude" / "skills" / NAME).is_symlink())
            self.assert_open(self.skill())

        # O28.
        self.land()
        with self.subTest(oracle="O28"):
            self.assert_open(self.skill(cwd=self.linked()))

    def test_f2_each_change_of_what_loads_is_denied(self) -> None:
        """O3 to O8, O14, O15, O29 to O31, O16 on each denial, O23 for O3 and for stdin `[]`: a branch list, a
        branch edit, a nested copy, a link, and each edit of one of the two checkouts."""
        # O3, O23.
        self.land(listed=("other-guide",))
        self.write(self.repo, {"dev-loop.json": self.config_bytes(LISTED)})
        git(self.repo, "commit", "-q", "-am", "List the skill on the branch")
        with self.subTest(oracle="O3"):
            self.assert_reason(self.skill(), NOT_LISTED)
        with self.subTest(oracle="O23", line="O3"):
            self.assertEqual(self.wall(), (0, {"deny": True, "reason": NOT_LISTED}))
        with self.subTest(oracle="O23", line="stdin []"):
            code, out = self.cli("skill-wall", stdin="[]")
            self.assertEqual((code, out.get("deny")), (0, True), out)

        # O4.
        self.land(listed=None)
        with self.subTest(oracle="O4"):
            self.assert_reason(self.skill(), NOT_LISTED)

        # O5.
        self.land(files={})
        self.write(self.repo, {SKILL_PATH: PLAIN})
        with self.subTest(oracle="O5"):
            self.assert_reason(self.skill(), NO_COPY)

        # O6.
        self.land(files={f"sub/{SKILL_PATH}": PLAIN})
        with self.subTest(oracle="O6"):
            self.assert_reason(self.skill(), NO_COPY)

        # O7.
        self.land()
        self.write(self.repo, {SKILL_PATH: ONE_MORE_BYTE})
        with self.subTest(oracle="O7", edit="not committed"):
            self.assert_reason(self.skill(), DIFFERS)
        git(self.repo, "commit", "-q", "-am", "Edit the skill on the branch")
        with self.subTest(oracle="O7", edit="committed"):
            self.assert_reason(self.skill(), DIFFERS)

        # O8.
        self.land()
        (self.repo / SKILL_PATH).unlink()
        with self.subTest(oracle="O8"):
            self.assert_reason(self.skill(), DIFFERS)

        # O14.
        other = ".agents/skills/other-guide"
        self.land(files={SKILL_PATH: PLAIN, f"{other}/SKILL.md": PLAIN})
        shutil.rmtree(self.repo / ".claude" / "skills" / NAME)
        self.write(self.repo, {}, {f".claude/skills/{NAME}": f"../../{other}"})
        with self.subTest(oracle="O14"):
            self.assert_reason(self.skill(), DIFFERS)

        # O15.
        self.write(self.root, {f"{OUTSIDE_FOLDER}/SKILL.md": PLAIN})
        self.land(files={}, links={f".claude/skills/{NAME}": f"../../../{OUTSIDE_FOLDER}"})
        with self.subTest(oracle="O15"):
            self.assertEqual((self.repo / SKILL_PATH).read_bytes(), PLAIN)
            self.assert_denied(self.skill())

        # O29.
        self.land()
        worktree = self.linked()
        self.write(self.repo, {SKILL_PATH: ONE_MORE_BYTE})
        with self.subTest(oracle="O29"):
            self.assert_reason(self.skill(cwd=worktree), DIFFERS)

        # O30.
        self.land()
        worktree = self.linked()
        self.write(worktree, {SKILL_PATH: ONE_MORE_BYTE})
        with self.subTest(oracle="O30"):
            self.assert_reason(self.skill(cwd=worktree), DIFFERS)

        # O31.
        self.land()
        worktree = self.linked()
        (self.repo / SKILL_PATH).unlink()
        with self.subTest(oracle="O31"):
            self.assert_reason(self.skill(cwd=worktree), DIFFERS)

    def test_f3_a_skill_that_can_leave_the_wall_is_denied(self) -> None:
        """O9 to O12, O32, O16 on each denial: a fork, a shell line and a hook, with every copy equal."""
        for oracle, text, reason in UNSAFE_SKILLS:
            self.land(files={SKILL_PATH: text})
            with self.subTest(oracle=f"O{oracle}", skill=text.decode()):
                self.assert_reason(self.skill(), reason)

    def test_f4_all_other_callers_keep_their_rules(self) -> None:
        """O17 to O22: the 3 fixed names, a plugin name, the callers with no wall, an empty call, the `spec-tester`
        and a repository with no kit CLI."""
        # O17.
        self.land(listed=None)
        for name in ALLOWED_SKILLS:
            with self.subTest(oracle="O17", repository="no key", skill=name):
                self.assert_open(self.skill({"skill": name}))
        git(self.repo, "update-ref", "-d", "refs/heads/main")
        for name in ALLOWED_SKILLS:
            with self.subTest(oracle="O17", repository="no main ref", skill=name):
                self.assert_open(self.skill({"skill": name}))

        # O18.
        plugin = f"plugin:{NAME}"
        self.land(files={f".claude/skills/{plugin}/SKILL.md": PLAIN}, listed=(plugin,))
        with self.subTest(oracle="O18"):
            self.assert_denied(self.skill({"skill": plugin}))

        # O21.
        self.land(listed=("other-guide",))
        for agent in (None, "general-purpose"):
            with self.subTest(oracle="O21", caller=agent or "main session"):
                self.assert_open(self.skill(agent=agent))

        # O19, O20, O22, with O1 on the same repository: the rule of each line gives its denial, not the skill.
        self.land()
        with self.subTest(oracle="O1"):
            self.assert_open(self.skill())
        with self.subTest(oracle="O19"):
            self.assert_closed(self.skill({}))
        with self.subTest(oracle="O20"):
            self.assert_denied(self.skill(agent="spec-tester"))
        shutil.rmtree(self.repo / ".agents" / "skills" / "dev-loop")
        with self.subTest(oracle="O22"):
            self.assert_closed(self.skill())


class SetupAndMergeTests(unittest.TestCase):
    def scratch(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name).resolve()

    def install(self, *args: str) -> None:
        done = subprocess.run(["node", str(INSTALLER), *args], cwd=self.project, env=self.env, capture_output=True,
                              text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout[-1500:] + done.stderr[-1500:])

    def plant_security_audit(self) -> None:
        """A test `security-audit` skill at the Claude path, its tree id in the variable that names it."""
        folder = self.home / ".claude" / "skills" / "security-audit"
        merge.put(folder / "SKILL.md", "---\nname: security-audit\ndescription: Audit a codebase.\n---\n\n# Audit\n")
        tree_repo = self.root / "tree-scratch"
        merge.git(self.root, self.env, "init", "-q", str(tree_repo))
        tree_env = {**self.env, "GIT_DIR": str(tree_repo / ".git"), "GIT_WORK_TREE": str(folder)}
        merge.git(folder, tree_env, "add", "-A", ".")
        self.env["DEVLOOP_TEST_SECURITY_AUDIT_TREE"] = merge.git(folder, tree_env, "write-tree")

    def installed_project(self) -> None:
        """An installed project with no commit yet, so no main ref, and a home where `check_setup.py` passes."""
        self.root = self.scratch()
        self.env = merge.clean_env(self.root)
        self.home = self.root / "home"
        stubs = self.root / "stubs"
        merge.put(stubs / "claude", "#!/bin/sh\n[ \"$1\" = --version ] || exit 2\necho '2.1.310 (Claude Code)'\n")
        (stubs / "claude").chmod(0o755)
        self.env["PATH"] = f"{stubs}{os.pathsep}{self.env.get('PATH', '')}"
        (self.root / "notes" / "Features").mkdir(parents=True)
        self.project = self.root / "acme-gizmos"
        merge.init_repo(self.project, self.env)
        self.install("init", "--yes", "--harness=both", f"--notes={self.root / 'notes'}")
        self.plant_security_audit()
        self.config = json.loads((self.project / "dev-loop.json").read_text())

    def checker(self) -> tuple[int, str]:
        done = subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--home", str(self.home),
                               "--repo", str(self.project)], env=self.env, capture_output=True, text=True, timeout=180)
        return done.returncode, done.stdout + done.stderr

    def assert_no_action(self, label: str) -> None:
        code, output = self.checker()
        with self.subTest(oracle="O25", case=label):
            self.assertEqual(code, 0, output)
            self.assertEqual([line for line in user_actions(output) if NAME in line], [], output)

    def write_config(self, listed: object = None) -> None:
        config = {**self.config, **({} if listed is None else {"walled_skills": listed})}
        (self.project / "dev-loop.json").write_text(json.dumps(config, indent=2) + "\n")

    def project_key(self) -> object:
        return json.loads((self.project / "dev-loop.json").read_text()).get("walled_skills", "no key")

    def commit_main(self, message: str) -> None:
        merge.git(self.project, self.env, "add", "-A")
        merge.git(self.project, self.env, "commit", "-q", "--allow-empty", "-m", message)

    def assert_setup_lines(self) -> None:
        """O24, O25, O27, in the order the project grows: no commit, a main with no key, a main with the key."""
        self.installed_project()

        # O27, no project value.
        with self.subTest(oracle="O27", step="init"):
            self.assertEqual(self.project_key(), "no key")
        self.install("upgrade", "--yes")
        with self.subTest(oracle="O27", step="upgrade"):
            self.assertEqual(self.project_key(), "no key")

        # O25, no main ref.
        self.write_config(list(LISTED))
        self.assert_no_action("no main ref")

        # O24, the shape, on the disk copy.
        for value in BAD_LISTS:
            self.write_config(value)
            code, output = self.checker()
            with self.subTest(oracle="O24", value=value):
                self.assertEqual(code, 1, output)
                self.assertTrue([line for line in output.splitlines() if "walled_skills" in line], output)

        # O25, the list only on disk.
        self.write_config()
        self.commit_main("Base")
        self.write_config(list(LISTED))
        self.assert_no_action("list only on disk")

        # O24, a good list and a plain skill.
        merge.put(self.project / SKILL_PATH, PLAIN)
        self.commit_main("List a plain skill")
        code, output = self.checker()
        with self.subTest(oracle="O24", value="a good list"):
            self.assertEqual(code, 0, output)
            self.assertEqual([line for line in output.splitlines() if NAME in line], [], output)

        # O25, an unusable listed skill at the main ref.
        merge.put(self.project / SKILL_PATH, skill_text(body="Today is !`date`.\n"))
        self.commit_main("Add a shell line")
        unsafe = self.checker()
        shutil.rmtree(self.project / ".claude" / "skills" / NAME)
        self.commit_main("Remove the skill")
        for label, (code, output) in (("a shell line", unsafe), ("no skill", self.checker())):
            with self.subTest(oracle="O25", case=label):
                self.assertEqual(code, 0, output)
                self.assertTrue([line for line in user_actions(output) if NAME in line], output)

        # O27, a project value.
        self.install("upgrade", "--yes")
        with self.subTest(oracle="O27", step="upgrade keeps a value"):
            self.assertEqual(self.project_key(), list(LISTED))

    def landing(self, project: merge.Project, slug: str, files: dict[str, str]) -> dict:
        """`merge-check` on a new reviewed branch with `files` changed."""
        worktree = project.worktree(f"feat/{slug}")
        project.spec(slug, "M")
        sha = project.commit(worktree, files, f"Change for {slug}")
        project.review(worktree, sha, "M", slug=slug)
        code, out = project.merge_check(worktree)
        self.assertEqual(code, 0, out)
        return out

    def assert_merge_lines(self) -> None:
        """O26, O33 on one project with a plain listed skill, a linked listed skill and a skill not listed."""
        project = merge.Project(self.scratch(), merge.config_text(walled_skills=[NAME, "linked-guide"]))
        link = project.repo / ".claude" / "skills" / "linked-guide"
        link.parent.mkdir(parents=True)
        link.symlink_to("../../.agents/skills/linked-guide")
        project.git(project.repo, "add", "-A")
        target = ".agents/skills/linked-guide/SKILL.md"
        unlisted = ".claude/skills/other-guide/SKILL.md"
        project.advance_main({SKILL_PATH: PLAIN.decode(), target: PLAIN.decode(), unlisted: PLAIN.decode()}, "Skills")
        edited = PLAIN.decode() + "Use the primary for writes.\n"

        out = self.landing(project, "add-name", {"dev-loop.json": merge.config_text(
            walled_skills=[NAME, "linked-guide", "new-guide"])})
        with self.subTest(oracle="O26"):
            self.assertEqual(out["merge"], "human", out)
            self.assertTrue(merge.reasons(out, "config-changed"), out)

        for label, slug, path in (("plain", "edit-plain", SKILL_PATH), ("link", "edit-link", target)):
            out = self.landing(project, slug, {path: edited})
            with self.subTest(oracle="O33", skill=label):
                self.assertEqual(out["merge"], "human", out)
                self.assertEqual([item.get("path") for item in merge.reasons(out, "walled-skill")], [path], out)
                self.assertEqual(merge.reasons(out, "config-changed"), [], out)

        out = self.landing(project, "edit-other", {unlisted: edited})
        with self.subTest(oracle="O33", skill="not listed"):
            self.assertEqual(merge.reasons(out, "walled-skill"), [], out)

    def test_f5_setup_and_merge_report_a_problem(self) -> None:
        """O24 to O27, O33: `check_setup.py` on a bad shape and on an unusable listed skill, `init` and `upgrade` on
        the key, `merge-check` on a change of the list and of a listed skill."""
        self.assert_setup_lines()
        self.assert_merge_lines()


class KitTextTests(unittest.TestCase):
    def assert_names(self, label: str, text: str, *names: str | re.Pattern[str]) -> None:
        flat = " ".join(text.split())
        for name in names:
            with self.subTest(oracle="O34", file=label, name=str(name)):
                found = name in flat if isinstance(name, str) else name.search(flat)
                self.assertTrue(found, f"{label} does not name {name}")

    def test_o34_kit_text(self) -> None:
        """O34: the contract file, the index lines, the Claude route and hook header, the Codex route."""
        with self.subTest(oracle="O34", file="reference/cli/skill-wall.md"):
            self.assertTrue((REPO_ROOT / "reference" / "cli" / "skill-wall.md").is_file())
        key_or_command = re.compile(r"skill-wall|walled_skills")
        for path in (REPO_ROOT / "AGENTS.md", KIT_REFERENCE, REPO_ROOT / "docs" / "configuration.md"):
            self.assert_names(path.name, path.read_text(), key_or_command)
        personal_gap = near(r"\bpersonal\b", r"\bskills?\b|\bcopy\b", 80)
        names = ("walled_skills", FORK, SHELL, personal_gap)
        self.assert_names(CLAUDE_ROUTE.name, CLAUDE_ROUTE.read_text(), *names)
        self.assert_names(NET, header(CLAUDE_HOOKS / NET).replace("#", " "), *names)
        self.assert_names(CODEX_ROUTE.name, CODEX_ROUTE.read_text(), "walled_skills")


if __name__ == "__main__":
    unittest.main()

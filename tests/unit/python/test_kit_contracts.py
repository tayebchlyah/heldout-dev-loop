"""Kit text contracts: the skill, the feature worktree, the model aliases and the plan tool."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

import test_devloop
from support import CHECKER, CLAUDE_HOOKS, CODEX_ASSETS, SKILL, SKILL_ROOT, all_instructions
from test_seal_hooks import ROOT, SHELL, patch

KIT_LIST_FILES = ("lib/kit.mjs", "claude/hooks/deny-acceptance-to-subagents.sh", "codex/hooks/acceptance-wall.sh",
                  "skill/scripts/seal/check.py", "skill/references/codex.md")


def kit_list_drift(root: Path) -> list[str]:
    text = {name: (root / name).read_text() for name in KIT_LIST_FILES}
    kit_skills = re.search(r"^export const KIT_SKILLS = \[(.*)\]", text["lib/kit.mjs"], re.M).group(1)
    expected = {"dev-loop"} | set(re.findall(r"'([^']+)'", kit_skills))
    claude = re.search(r"^KIT='(.*)'$", text["claude/hooks/deny-acceptance-to-subagents.sh"], re.M).group(1)
    codex = re.search(r"^\s*kit='(.*)'$", text["codex/hooks/acceptance-wall.sh"], re.M).group(1)
    lists = {}
    for wall, regex in (("claude", claude), ("codex", codex)):
        for folder in (".agents", ".claude"):
            group = re.search(re.escape("\\" + folder + "/skills/(") + r"([^)]*)\)", regex)
            lists[f"{wall} {folder}"] = set(group.group(1).split("|")) if group else set()
    kit_dirs = re.search(r"^KIT_DIRS = \((.*?)\)$", text["skill/scripts/seal/check.py"], re.M | re.S).group(1)
    lists["KIT_DIRS"] = set(re.findall(r'"\.agents/skills/([^/"]+)/"', kit_dirs))
    walls = next(line for line in text["skill/references/codex.md"].splitlines() if line.startswith("- Walls:"))
    folders = re.search(r"kit skill folders .*? of (.*?)\)", walls)
    lists["codex.md"] = set(re.findall(r"`([a-z-]+)`", folders.group(1))) if folders else set()
    return [f"{name}: missing {sorted(expected - names)}, extra {sorted(names - expected)}"
            for name, names in lists.items() if names != expected]


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text()
        cls.instructions = all_instructions()

    def test_route_files_name_the_tool_walls(self) -> None:
        claude = (SKILL_ROOT / "references" / "claude.md").read_text()
        codex = (SKILL_ROOT / "references" / "codex.md").read_text()
        for value in ("`LSP` is denied to every subagent", "`Monitor` is walled as `Bash`",
                      "`disallowedTools: LSP, mcp__*, ListMcpResourcesTool, ReadMcpResourceTool, ReadMcpResourceDirTool, WebFetch, WebSearch, Agent`",
                      "an MCP search tool", "(#139, checked in Claude Code 2.1.310", "`Skipping frontmatter hooks`",
                      "`NEEDS_FACT: <question>`", "`NEEDS_FACT` comes before the seal commit", "`work from the spec`",
                      "decide --kind discovery",
                      "the shell route (`curl`, `gh api`): a walled agent can still reach the web and the network through the shell"):
            self.assertIn(value, claude)
        fragment = json.loads((ROOT / "codex/hooks.fragment.json").read_text())
        matcher = next(g["matcher"] for g in fragment["hooks"]["PreToolUse"]
                       if any("acceptance-wall.sh" in h["command"] for h in g["hooks"]))
        for value in (f"`{matcher}`", "`collaboration.*`", "`multi_agent`", "nested harness", "`view_image` is walled by `path`", "no hook fires on `exec`, one fires on each inner tool call",
                      "`write_stdin`", "#140", "`custom_tool_call_output`, output only",
                      "`webrun`", "hosted web search tool", "`*.githubusercontent.com`", "`NEEDS_FACT: <question>`",
                      "`NEEDS_FACT` comes before the seal commit", "`work from the spec`", "decide --kind discovery"):
            self.assertIn(value, codex)

    def test_frontmatter_and_trigger_survive(self) -> None:
        self.assertIn("name: dev-loop", self.skill)
        self.assertIn("at the start of any coding session", self.skill)
        self.assertIn("dev-loop.json", self.skill)
        self.assertIn("notes_path.py", self.skill)
        self.assertNotIn("$VAULT", self.skill)

    def test_all_eight_stages_remain_ordered(self) -> None:
        positions = [self.skill.index(f"## {number} ") for number in range(8)]
        self.assertEqual(positions, sorted(positions))

    def test_claude_bootstrap_contract_remains(self) -> None:
        required = [
            ".dev-loop/hooks/claude/gate.sh",
            ".claude/agents/implementer.md",
            ".claude/agents/spec-tester.md",
            ".claude/settings.json",
            "mattpocock-skills:domain-modeling",
            "mattpocock-skills:grilling",
            "mattpocock-skills:prototype",
        ]
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.instructions)

    def test_claude_build_and_gate_contract_remains(self) -> None:
        required = [
            "subagent_type: implementer",
            "tests/acceptance/",
            "pytest tests/acceptance -q --tb=no",
            "superpowers:subagent-driven-development",
            "mattpocock-skills:pr",
            "gh pr merge --rebase",
            "Never invoke `superpowers:verification-before-completion`",
        ]
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.instructions)

    def test_held_out_authorship_contract_remains(self) -> None:
        self.assertIn("spec-tester", self.instructions)
        self.assertIn("implementer", self.instructions)
        self.assertIn("Plan never references `tests/acceptance/`", self.instructions)
        self.assertNotIn("Tell the user to run `/clear`", self.instructions)

    def test_codex_branch_is_declared(self) -> None:
        self.assertIn("references/codex.md", self.skill)
        self.assertIn(".codex/agents/implementer.toml", self.instructions)
        self.assertIn(".codex/agents/spec-tester.toml", self.instructions)
        self.assertIn(".codex/agents/task-reviewer.toml", self.instructions)

    def test_codex_branch_uses_real_codex_operations(self) -> None:
        codex = (SKILL_ROOT / "references" / "codex.md").read_text()
        self.assertIn("Codex `/review`", codex)
        self.assertNotIn("browser or computer-use tools", codex)
        self.assertNotIn("`/code-review medium`", codex)
        self.assertNotIn("`/security-review`", codex)
        self.assertNotIn("`/verify`", codex)

    def test_claude_branch_never_calls_user_only_verify(self) -> None:
        claude = (SKILL_ROOT / "references" / "claude.md").read_text()
        self.assertNotIn("then `/verify`", claude)
        self.assertIn("user-invoked only", claude)

    def test_gate_runs_reviews_once_before_acceptance(self) -> None:
        for value in [
            "Review wave runs once per feature, before any acceptance run",
            "Never rerun it",
            "Progress guard",
            "Never ship red",
            "--last-failed",
            "GATE_SHA",
            "`commands.acceptance_failed`",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, self.instructions)

    def test_claude_worktree_reviewers_pin_the_sha(self) -> None:
        claude = (SKILL_ROOT / "references" / "claude.md").read_text()
        self.assertIn("`origin/main...GATE_SHA`", claude)
        self.assertNotIn("isolation: worktree", claude)
        self.assertNotIn("Rerun step 1.", claude)

    def test_the_glossary_is_glossary_md_else_context_md(self) -> None:
        text = SKILL.read_text()
        self.assertIn("Glossary = `GLOSSARY.md` at the repo root, else `CONTEXT.md`", text)
        self.assertIn("Both exist: `GLOSSARY.md` wins.", text)
        self.assertIn("first terms of `GLOSSARY.md`", text)
        self.assertIn("spec path and glossary path", text)
        self.assertIn("or a glossary invariant;", text)
        for agent in (CLAUDE_HOOKS.parent / "agents" / "spec-tester.md", CODEX_ASSETS / "agents" / "spec-tester.toml"):
            self.assertIn("the glossary (GLOSSARY.md, else CONTEXT.md)", agent.read_text(), agent.name)

    def test_setup_checker_exists(self) -> None:
        self.assertTrue(CHECKER.is_file())

    def test_codex_agent_assets_parse_and_preserve_roles(self) -> None:
        expected = {
            "implementer.toml": "implementer",
            "spec-tester.toml": "spec_tester",
            "task-reviewer.toml": "task_reviewer",
            "final-reviewer.toml": "final_reviewer",
            "security-reviewer.toml": "security_reviewer",
            "planner.toml": "planner",
        }
        for filename, name in expected.items():
            with self.subTest(filename=filename):
                parsed = tomllib.loads((CODEX_ASSETS / "agents" / filename).read_text())
                self.assertEqual(parsed["name"], name)
                self.assertNotIn("default_permissions", parsed)
                self.assertTrue(parsed["description"])
                self.assertTrue(parsed["developer_instructions"])


class ModelAliasContractTests(unittest.TestCase):
    def test_subagent_models_are_named_by_alias(self) -> None:
        claude = (SKILL_ROOT / "references" / "claude.md").read_text()
        self.assertIn("name them by alias only", claude)

    def test_no_payload_pins_a_full_claude_model_id(self) -> None:
        import re

        pinned = re.compile(r"^model:\s*claude-", re.MULTILINE)
        for path in [*(SKILL_ROOT.parent / "claude" / "agents").glob("*.md"), SKILL, *(SKILL_ROOT / "references").glob("*.md")]:
            with self.subTest(path=path.name):
                self.assertIsNone(pinned.search(path.read_text()))


class PlanToolContractTests(unittest.TestCase):
    def test_skill_keeps_the_checklist_in_the_native_plan_tool(self) -> None:
        section = test_devloop.section(SKILL, "## Detect the stage")
        for value in ["`TaskCreate` item per stage", "subject `Task N: <plan heading>`", "Codex: no plan tool", "`task-start` for each task"]:
            with self.subTest(value=value):
                self.assertIn(value, section)


class FeatureWorktreeContractTests(unittest.TestCase):
    def test_stages_three_to_seven_run_in_the_feature_worktree(self) -> None:
        skill = SKILL.read_text()
        self.assertIn("## Where each stage runs", skill)
        self.assertIn("Stages 3 to 7 run in the feature's own worktree", skill)
        self.assertIn("Never ask the user to change directory.", skill)
        self.assertNotIn(".worktrees/", skill)

    def test_routes_name_the_planner_the_wall_and_the_exit_step(self) -> None:
        routes = {
            "claude.md": (["worktree-wall.sh", "ExitWorktree", "`keep`", "/clear",
                           "- Task reviewers and re-reviewers: `general-purpose`, `effort: high`."],
                          ["general-purpose", "model: opus", "effort: high", "superpowers:writing-plans", "notes_path.py",
                           "size-check --all", "one module or one seam", "applies the SKILL.md Stage 4 checks"]),
            "codex.md": ([".codex/agents/planner.toml", "/reasoning", "No worktree wall"],
                         ["`planner`", "No worktree wall", "applies the SKILL.md Stage 4 checks"]),
        }
        for route, (whole, stage_4) in routes.items():
            text = (SKILL_ROOT / "references" / route).read_text()
            section = text.split("## Stage 4 plan\n", 1)[-1].split("\n## ", 1)[0] if "## Stage 4 plan\n" in text else ""
            for value in whole:
                with self.subTest(route=route, value=value):
                    self.assertIn(value, text)
            for value in stage_4:
                with self.subTest(route=route, stage_4=value):
                    self.assertIn(value, section)

    def test_rebase_points_exclude_the_build(self) -> None:
        skill = SKILL.read_text()
        self.assertIn("Never during Stages 4 and 5", skill)
        self.assertIn("git fetch origin`, `git switch main`, `git rebase origin/main`", skill)
        for route in ["claude.md", "codex.md"]:
            with self.subTest(route=route):
                text = (SKILL_ROOT / "references" / route).read_text()
                self.assertIn("First rebase onto `origin/main`", text)

    def test_superseded_oracle_lines_flow_from_spec_to_tests(self) -> None:
        skill = SKILL.read_text()
        self.assertIn("Supersedes: read the earlier specs", skill)
        self.assertIn("Amended by <feature> on <date>", skill)
        self.assertIn("a Supersedes line or an escaped bug", skill)
        self.assertIn("a regression", skill)
        claude_tester = (SKILL_ROOT.parent / "claude" / "agents" / "spec-tester.md").read_text()
        codex_tester = (SKILL_ROOT.parent / "codex" / "agents" / "spec-tester.toml").read_text()
        self.assertIn("Change no other existing test.", claude_tester)
        self.assertIn("Change no other existing test.", codex_tester)

    def test_the_spec_tester_proves_flows_not_combinations(self) -> None:
        claude_tester = (SKILL_ROOT.parent / "claude" / "agents" / "spec-tester.md").read_text()
        codex_tester = (SKILL_ROOT.parent / "codex" / "agents" / "spec-tester.toml").read_text()
        for text in [claude_tester, codex_tester]:
            self.assertIn("Prove each flow once.", text)
            self.assertNotIn("combinations", text)
        self.assertIn("Never dispatch a subagent", claude_tester)

    def test_a_stage_ends_with_a_full_gate(self) -> None:
        self.assertIn(
            "End every stage that changed the repo with a direct gate: `commands.gate` from `dev-loop.json` "
            "when mapped, else `scripts/gate.sh`",
            SKILL.read_text(),
        )

    def test_session_a_and_b_names_are_gone(self) -> None:
        from support import PACKAGE_ROOT

        paths = [PACKAGE_ROOT / "AGENTS.md", PACKAGE_ROOT / "README.md"]
        for folder in ("skill", "skills", "claude", "codex", "reference"):
            paths += [p for p in (PACKAGE_ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        for path in paths:
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            with self.subTest(path=str(path.relative_to(PACKAGE_ROOT))):
                self.assertNotRegex(text, r"Session [AB]\b")
        glossary = (PACKAGE_ROOT / "GLOSSARY.md").read_text()
        for line in glossary.splitlines():
            if "Session A" in line or "Session B" in line:
                self.assertTrue(line.startswith("_Avoid_"), line)
        self.assertIn("**Shaping**:", glossary)
        self.assertIn("**Delivery**:", glossary)



class NotesLayoutTextTests(unittest.TestCase):
    OLD_PATHS = (
        "$NOTES/specs/",
        "$NOTES/plans/",
        "$NOTES/framing/",
        "$NOTES/answers/",
        "$NOTES/maps/",
        "<notes>/plans/",
        "<notes>/maps/",
        "<notes folder>/plans/",
        "spec: <spec file name>",
        "security-audit/run-",
        ".superpowers/sdd/<plan basename>",
    )

    def test_no_old_notes_paths(self) -> None:
        from support import PACKAGE_ROOT

        skipped = (PACKAGE_ROOT / "skill/scripts/notes_migrate", PACKAGE_ROOT / "skill/scripts/devloop.py")
        for folder in ("skill", "skills", "claude", "codex"):
            for path in (PACKAGE_ROOT / folder).rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path in skipped or any(parent in skipped for parent in path.parents):
                    continue
                try:
                    text = path.read_text()
                except UnicodeDecodeError:
                    continue
                for old in self.OLD_PATHS:
                    with self.subTest(path=str(path.relative_to(PACKAGE_ROOT)), old=old):
                        self.assertNotIn(old, text)

    def test_skill_names_new_layout(self) -> None:
        text = SKILL.read_text()
        for name in (
            "Features/<YYYYMMDD> <Title>/",
            "Framing.md",
            "Spec.md",
            "Plan.md",
            "Answers/",
            "Maps/<Effort Title>/",
            "notes-migrate",
        ):
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_stage_0_migration_text(self) -> None:
        from support import PACKAGE_ROOT

        text = SKILL.read_text()
        stage_0 = text[text.index("## 0 Bootstrap"):text.index("## 1 ")]
        self.assertIn("dry run", stage_0)
        self.assertIn("confirm", stage_0)
        kit = (PACKAGE_ROOT / "reference/kit.md").read_text()
        self.assertIn("notes-migrate", kit[kit.index("## Upgrade"):])

    def test_planner_texts_name_plan_in_feature_folder(self) -> None:
        from support import PACKAGE_ROOT

        for rel in ("skill/references/claude.md", "codex/agents/planner.toml"):
            text = (PACKAGE_ROOT / rel).read_text()
            for needle in ("Plan.md", "spec: Spec.md", "type: plan", "feature: <slug>"):
                with self.subTest(file=rel, needle=needle):
                    self.assertIn(needle, text)
        self.assertIn("type: framing", SKILL.read_text())

    def test_skill_links_notes_migrate_reference(self) -> None:
        self.assertIn("(references/notes-migrate.md)", SKILL.read_text())
        self.assertTrue((SKILL_ROOT / "references/notes-migrate.md").is_file())
        self.assertNotIn("reference/cli/notes-migrate.md", SKILL.read_text())


class KitSkillListTests(unittest.TestCase):

    def run_wall(self, harness: str, script: str, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run([SHELL[harness], str(ROOT / harness / "hooks" / script)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60)

    def test_every_kit_skill_list_equals_kit_skills(self) -> None:
        self.assertEqual(kit_list_drift(ROOT), [])
        names = re.search(r"^export const KIT_SKILLS = \[(.*)\]", (ROOT / "lib/kit.mjs").read_text(), re.M).group(1)
        for name in ["dev-loop", *re.findall(r"'([^']+)'", names)]:
            for path in (f".agents/skills/{name}/SKILL.md", f".claude/skills/{name}/SKILL.md"):
                for caller in ({}, {"agent_id": "a", "agent_type": "implementer"}):
                    calls = [("claude", "deny-acceptance-to-subagents.sh", tool, {"file_path": path})
                             for tool in ("Edit", "Write")]
                    calls.append(("codex", "acceptance-wall.sh", "apply_patch",
                                  {"command": patch(f"*** Update File: {path}")}))
                    for harness, script, tool, tool_input in calls:
                        with self.subTest(path=path, caller=caller.get("agent_type", "main"), tool=tool):
                            result = self.run_wall(harness, script, {**caller, "cwd": "/repo", "tool_name": tool,
                                                                     "tool_input": tool_input})
                            self.assertEqual(result.returncode, 2, result.stderr)
                            self.assertIn("upgrade", result.stderr)

    def test_a_name_added_to_one_list_only_is_drift(self) -> None:
        edits = [("lib/kit.mjs", "export const KIT_SKILLS = [", "export const KIT_SKILLS = ['zz-new', "),
                 ("skill/scripts/seal/check.py", "KIT_DIRS = (", 'KIT_DIRS = (".agents/skills/zz-new/", ')]
        for name, old, new in edits:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                for rel in KIT_LIST_FILES:
                    (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / rel, Path(tmp) / rel)
                target = Path(tmp) / name
                target.write_text(target.read_text().replace(old, new, 1))
                self.assertNotEqual(kit_list_drift(Path(tmp)), [])

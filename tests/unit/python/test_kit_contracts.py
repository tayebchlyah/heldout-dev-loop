"""Kit text contracts: the skill, the feature worktree, the model aliases and the plan tool."""

from __future__ import annotations

import re
import tomllib
import unittest

import test_devloop
from support import CHECKER, CLAUDE_HOOKS, CODEX_ASSETS, SKILL, SKILL_ROOT, all_instructions


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text()
        cls.instructions = all_instructions()

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
        self.assertIn("`main...GATE_SHA`", claude)
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

"""Kit text contracts: the review wave, the review yield, the spec gap and the seal instructions."""

from __future__ import annotations

import tomllib
import unittest

from test_devloop import REPO_ROOT, SKILL_ROOT, section
from test_skill_text import IMPLEMENTERS, REFERENCES, SPEC_TESTERS, stage_text


class ReviewWaveContractTests(unittest.TestCase):
    """D12, D13, D14, D6 in Stage 6: one review wave of three reviewers."""

    def setUp(self) -> None:
        self.stage = stage_text(6)
        self.claude = section(REFERENCES / "claude.md", "## Feature gate")
        self.codex = section(REFERENCES / "codex.md", "## Feature gate")

    def test_review_wave_has_three_reviewers_per_route(self) -> None:
        for value in [
            "`general-purpose`", "`model: opus`", "one message, four Agent calls",
            "skill `code-review`, args `high <feature branch>`", "skill `security-review`",
            "`git diff main...GATE_SHA` from the feature worktree, not the working tree",
            "Pocock spec desk", "Pocock standards desk", "`mattpocock-skills:code-review` SKILL.md",
            "no nested agents", "reports after its parent hands back", "Pocock = two desks",
            "works in the feature worktree on `main...GATE_SHA`",
        ]:
            with self.subTest(route="claude", value=value):
                self.assertIn(value, self.claude)
        self.assertNotIn("three Agent calls", self.claude)
        for value in [
            "`final_reviewer`", "skill `code-review` with fixed point `main`", "`security_reviewer`",
            "works in the feature worktree on `main...GATE_SHA`",
        ]:
            with self.subTest(route="codex", value=value):
                self.assertIn(value, self.codex)

    def test_reviewers_run_at_high_effort(self) -> None:
        for name in ["final-reviewer.toml", "security-reviewer.toml"]:
            with self.subTest(path=name):
                parsed = tomllib.loads((REPO_ROOT / "codex" / "agents" / name).read_text())
                self.assertEqual(parsed["model_reasoning_effort"], "high")
        self.assertIn("four Agent calls, all `general-purpose` with `model: opus` and `effort: high`", self.claude)

    def test_no_simplify_or_mutation_in_gate(self) -> None:
        for name, text in [("SKILL.md", self.stage), ("claude.md", self.claude), ("codex.md", self.codex)]:
            for value in ["simplif", "mutation", "mutant"]:
                with self.subTest(path=name, value=value):
                    self.assertNotIn(value, text.lower())
        self.assertFalse((REPO_ROOT / "codex" / "agents" / "simplifier.toml").exists())
        self.assertNotIn("simplifier", (REFERENCES / "codex.md").read_text())
        self.assertNotIn("simplifier.toml", (SKILL_ROOT / "scripts" / "check_setup.py").read_text())

    def test_gate_uses_devloop_and_commands_map(self) -> None:
        for value in [
            "git fetch origin", "`commands.stack_reset`", "docker compose --profile test up -d --build --force-recreate --wait",
            "`GATE_SHA`", "scripts/devloop.py gate --summary", "`commands.acceptance`", "`commands.acceptance_failed`",
            "product, test or environment", "Never rerun to green", "Progress guard", "Stale image rule",
            "## Gardener", "at most 20 open items", "lint candidate", "at most 5",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, self.stage)

    def test_every_defect_is_fixed(self) -> None:
        self.assertNotIn("Defect past the cap", self.stage)
        self.assertIn("Every defect is fixed, no cap", self.stage)
        self.assertIn("More than 5 defects: fix all, say so in the final report", self.stage)
        self.assertIn("at most 5 optional non-defect items", self.stage)
        self.assertIn("passes when its defects are fixed", self.stage)
        for name in ["claude.md", "codex.md"]:
            with self.subTest(path=name):
                self.assertIn("Act On list: every defect", (REFERENCES / name).read_text())

    def test_track_s_marker_comes_after_the_fix_wave_and_the_drive(self) -> None:
        line = next(line for line in self.stage.splitlines() if line.startswith("Track S:"))
        self.assertIn("After the drive: `mkdir -p", line)
        self.assertLess(line.index("fix wave and drive only"), line.index("review-done"))

    def test_gate_writes_ledger_line(self) -> None:
        self.assertIn("`Feature gate: green at <sha>`", self.stage)
        self.assertIn("progress.md", self.stage)


class ReviewYieldContractTests(unittest.TestCase):
    """Review Wave Measurement D2 to D5, D8, D15, D16: yield log, triage filters, security verifier, fresh fixer."""

    def setUp(self) -> None:
        self.stage = stage_text(6)

    def test_yield_log_columns_and_classes(self) -> None:
        self.assertIn("`$NOTES/Review Yield.md`", self.stage)
        columns = ["`#`", "`file:line`", "`reviewers`", "`class`", "`triage verdict`", "`fix sha`", "`user verdict`"]
        self.assertIn(", ".join(columns), self.stage)
        for value in ["`defect-a`", "`defect-b`", "`defect-c`", "`optional`", "`gardener`", "`dropped:<filter>`",
                      "`rejected`", "`deferred`", "one cost line per reviewer", "never waited on"]:
            with self.subTest(value=value):
                self.assertIn(value, self.stage)

    def test_triage_filters_come_before_the_defect_rules(self) -> None:
        for value in ["nitpick", "hypothetical against actual", "premature abstraction", "taste", "missing context",
                      "no concrete failing input"]:
            with self.subTest(value=value):
                self.assertIn(value, self.stage)
        self.assertLess(self.stage.index("premature abstraction"), self.stage.index("Defect = CONFIRMED"))

    def test_security_findings_go_to_a_fresh_verifier(self) -> None:
        for value in ["candidate-verifier prompt", "NEEDS VALIDATION", "REJECTED", "hardening gap",
                      "(c) security finding CONFIRMED by the verifier"]:
            with self.subTest(value=value):
                self.assertIn(value, self.stage)
        claude = section(REFERENCES / "claude.md", "## Feature gate")
        codex = section(REFERENCES / "codex.md", "## Feature gate")
        for value in ["`general-purpose`, `model: opus`, `effort: high`",
                      "~/.claude/skills/security-audit/VALIDATION-AND-REPORTING.md"]:
            with self.subTest(route="claude", value=value):
                self.assertIn(value, claude)
        for value in ["fresh `security_reviewer`", "~/.agents/skills/security-audit/VALIDATION-AND-REPORTING.md"]:
            with self.subTest(route="codex", value=value):
                self.assertIn(value, codex)

    def test_open_needs_validation_item_blocks_a_release(self) -> None:
        self.assertIn("NEEDS VALIDATION", stage_text(7))
        self.assertIn("a ruling under it says", stage_text(7))
        self.assertIn("`accept` or `close`", stage_text(7))

    def test_every_fix_round_starts_a_new_implementer(self) -> None:
        for value in ["new `implementer`", "Never resume a finished agent"]:
            with self.subTest(value=value):
                self.assertIn(value, self.stage)

    def test_agents_md_documents_merge_check_and_the_data_rule(self) -> None:
        agents = (REPO_ROOT / "AGENTS.md").read_text()
        merge_check = (REPO_ROOT / "reference" / "cli" / "merge-check.md").read_text()
        self.assertIn("merge-check [--repo <path>] [--feature <slug>]", agents)
        for value in [
            "merge-check [--repo <path>] [--feature <slug>]", "merge_human_paths", "`config-unreadable`",
            "`merge-human`", "`track-l`", "`human-path`", "`config-changed`", "`no-review`", "`unreviewed`",
            "`unreviewed_minor`", "--verbatim",
            "`head`", "`branch`", "`.github`", "`.yml`", "`.conf`", "more than one branch",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, merge_check)
        self.assertIn("no smaller wave, no removal of the Track L threat model and no second review pass until 5 "
                      "features have a complete section in Review Yield.md", agents)
        kit = (REPO_ROOT / "reference" / "kit.md").read_text()
        r10 = kit[kit.index("Ruling R10"):kit.index("These are expected manual steps")]
        self.assertIn("3. ", r10)
        self.assertIn("security-audit", r10)

    def test_acceptance_and_notes_docs(self) -> None:
        acceptance = (REPO_ROOT / "reference" / "cli" / "acceptance.md").read_text()
        notes = next(line for line in (REPO_ROOT / "reference" / "kit.md").read_text().splitlines()
                     if line.startswith("Notes path:"))
        for value in ("`cases`", "<main-ref>"):
            self.assertIn(value, acceptance)
        for value in ("DEV_LOOP_NOTES", "main worktree", "tracked"):
            self.assertIn(value, notes)
        self.assertNotIn("`dev-loop.local.json` in the worktree", notes)


class SpecGapContractTests(unittest.TestCase):
    """D6, D9, the pipeline table, the devloop CLI contract in AGENTS.md."""

    def test_mutation_md_describes_the_nightly_ci_workflow(self) -> None:
        nightly = section(REFERENCES / "mutation.md", "## Nightly CI")
        for value in ["schedule", "cron", "once a day", "`main`", "--head main --run", "artifact",
                      "functions that need one unit test", "Stage 0", "new repo"]:
            with self.subTest(value=value):
                self.assertIn(value, nightly)

    def test_stage_0_writes_the_app_cli_skeleton(self) -> None:
        self.assertIn("app CLI skeleton (stack up, stack reset, acceptance run), mapped in `commands`", stage_text(0))

    def test_stack_up_and_stack_reset_are_read(self) -> None:
        self.assertIn("`commands.stack_up`, else `docker compose --profile test up -d --wait`", stage_text(5))
        self.assertIn("Fresh stack: `commands.stack_reset`", stage_text(6))

    def test_agents_md_documents_the_cli_as_built(self) -> None:
        agents = (REPO_ROOT / "AGENTS.md").read_text()
        cli = REPO_ROOT / "reference" / "cli"
        for value in ["`status [--repo <path>] [--feature <slug>]`", "`logged [--name <name>] -- <cmd>`"]:
            with self.subTest(value=value):
                self.assertIn(value, agents)
        for name, value in [("status", "`status [--repo <path>] [--feature <slug>]`"),
                            ("logged", "`logged [--name <name>] -- <cmd>`"), ("board", "`error` with `message`"),
                            ("status", "`features`: each spec not shipped"),
                            ("status", "An approved spec with neither is left out"),
                            ("status", "`shipped: <YYYY-MM-DD>`"), ("status", "`pr: <url>`")]:
            with self.subTest(value=value):
                self.assertIn(value, (cli / f"{name}.md").read_text())


D16_SENTENCE = ("When `## Supersedes` is not none, or a flow in `## Acceptance flows` names an earlier feature, "
                "write at least one test that combines the new behaviour with that earlier feature.")


class SealInstructionContractTests(unittest.TestCase):
    def test_spec_testers_compose_with_the_earlier_feature(self) -> None:
        for path in SPEC_TESTERS:
            with self.subTest(path=path.name):
                self.assertIn(D16_SENTENCE, path.read_text())

    def test_spec_testers_seal_and_run_without_tracebacks(self) -> None:
        for path in SPEC_TESTERS:
            text = path.read_text()
            for value in ["devloop.py seal --feature <slug> --message <subject>", "--tb=no", "seal --rebase-continue"]:
                with self.subTest(path=path.name, value=value):
                    self.assertIn(value, text)

    def test_taint_remedy_commands_are_complete(self) -> None:
        import re
        paths = SPEC_TESTERS + [SKILL_ROOT / "SKILL.md"]
        for path in paths:
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertIn("seal --feature <slug> --reverts <sha> --message <subject>", text)
                self.assertIsNone(re.search(r"devloop\.py seal(?=[.`,;]|\s*$)", text, re.M))
                self.assertIsNone(re.search(r"devloop\.py seal (?!--)", text))

    def test_implementers_stop_on_a_block_or_a_taint(self) -> None:
        for path in [*IMPLEMENTERS, SKILL_ROOT / "SKILL.md"]:
            with self.subTest(path=path.name):
                text = path.read_text()
                self.assertIn("guard block or a taint report", text)
                self.assertIn("never rephrase the call", text)
                self.assertIn("after a search-scope block, search again on named source folders or with the suite excluded",
                              text)

    def test_stage_texts_hold_the_seal_rules(self) -> None:
        checks = {
            1: ["never change `tests/acceptance/`", "`Supersedes` line"],
            3: ["devloop.py seal --feature <slug> --message <subject>", "seal --rebase-continue"],
            5: ["`taint`", "git revert", "fresh implementer", "git revert --no-commit <sha>",
                "seal --feature <slug> --reverts <sha>", "fresh spec-tester"],
            6: ["`seal`", "`weakened`", "quoted reason"],
        }
        for number, values in checks.items():
            for value in values:
                with self.subTest(stage=number, value=value):
                    self.assertIn(value, stage_text(number))

    def test_rebase_conflict_rule_replaced(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        self.assertNotIn("keeps the upstream side", skill)
        self.assertNotIn("checkout --ours", skill)
        self.assertIn("Every conflict under `tests/acceptance/` goes to `spec-tester`", skill)

    def test_codex_route_lists_the_gaps_and_the_wall(self) -> None:
        text = (REFERENCES / "codex.md").read_text()
        for value in ["acceptance-wall.sh", "seal.sh", "copies the seal value method", "prints only a summary",
                      "hook timeout", "Legacy mode", "acceptance failure text", "squashed", "`env`",
                      "`uv run`", "`timeout 5 git commit`", "patch-id", "`jq` is missing",
                      "keys on the custom agent name"]:
            with self.subTest(value=value):
                self.assertIn(value, text)
        for value in ["filesystem policy", "dev-implementer", "dev-spec-tester", "dev-read-only"]:
            with self.subTest(absent=value):
                self.assertNotIn(value, text)

    def test_claude_route_lists_the_hooks_and_gaps(self) -> None:
        text = (REFERENCES / "claude.md").read_text()
        for value in ["seal.sh stop", "seal.sh turn", "dev-loop-subagents", "`onFailure: \"block\"`",
                      "`stop_hook_active`", "patch-id", "`jq` is missing"]:
            with self.subTest(value=value):
                self.assertIn(value, text)

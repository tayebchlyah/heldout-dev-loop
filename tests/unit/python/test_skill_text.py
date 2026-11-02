"""Kit text contracts: the instructions, the skill stack and the tracks."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from test_devloop import REPO_ROOT, SKILL_ROOT, load_check_setup


TEN_RULES = [
    "one home file per module",
    "public behaviour only",
    "one table per function",
    "no change-detector tests",
    "one golden per output",
    "cost markers",
    "a mutation fix test proves the kill",
    "a test budget per plan task",
    "synthetic fixtures for moved acceptance details",
    "a growth report on each pr",
    "tests only at the seams the spec names",
]


PSTACK_PRINCIPLES = [
    "prove it works",
    "fix root causes",
    "attack the premise",
    "make operations idempotent",
    "migrate callers, then delete the legacy api",
    "boundary discipline",
    "separate before serializing shared state",
    "explain the number",
]


CODEBASE_DESIGN_RULES = [
    "the interface is the test surface",
    "replace old tests, do not layer new ones",
    "one adapter is a hypothetical seam, two adapters are a real one",
]


IMPLEMENTERS = [REPO_ROOT / "claude" / "agents" / "implementer.md", REPO_ROOT / "codex" / "agents" / "implementer.toml"]


SPEC_TESTERS = [REPO_ROOT / "claude" / "agents" / "spec-tester.md", REPO_ROOT / "codex" / "agents" / "spec-tester.toml"]


class InstructionContractTests(unittest.TestCase):
    def test_search_scope_texts(self) -> None:
        held = "tests/" + "acceptance"
        rows = [(SKILL_ROOT / "references" / "building.md", [f"never the repo root or `tests/` without excluding `{held}`"]),
                (SKILL_ROOT / "references" / "claude.md", ["search-wall", "recursive search", "xargs"]),
                (SKILL_ROOT / "references" / "codex.md", ["search-wall", "recursive search", "xargs"]),
                (REPO_ROOT / "reference" / "cli" / "audit.md", ["path rule", "`paths`"])]
        for path, phrases in rows:
            for phrase in phrases:
                with self.subTest(path=path.name, phrase=phrase):
                    self.assertIn(phrase, path.read_text())

    def test_building_md_holds_ponytail_and_its_notice(self) -> None:
        text = (SKILL_ROOT / "references" / "building.md").read_text()
        for value in ["Does it need to exist?", "MIT License", "DietrichGebert"]:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_tests_md_holds_ten_rules(self) -> None:
        text = (SKILL_ROOT / "references" / "tests.md").read_text().lower()
        for rule in TEN_RULES:
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_building_md_holds_pstack_principles(self) -> None:
        text = (SKILL_ROOT / "references" / "building.md").read_text().lower()
        for value in [*PSTACK_PRINCIPLES, "b1a1298", "lauren tan", "benchmark.md"]:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_kit_prompts_name_no_internal_project(self) -> None:
        internal = re.compile(r"Dev Process Review|\b[FS]\d+[a-z]?\b")
        for root in [SKILL_ROOT, REPO_ROOT / "skills", REPO_ROOT / "claude", REPO_ROOT / "codex"]:
            for path in root.rglob("*"):
                if path.suffix in {".md", ".toml", ".rules", ".yaml"}:
                    with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                        self.assertIsNone(internal.search(path.read_text()))

    def test_tests_md_holds_codebase_design_rules(self) -> None:
        text = (SKILL_ROOT / "references" / "tests.md").read_text().lower()
        for rule in CODEBASE_DESIGN_RULES:
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_benchmark_md_holds_the_checklist_and_its_notice(self) -> None:
        text = (SKILL_ROOT / "references" / "benchmark.md").read_text()
        for value in ["Why not double?", "Did it even happen?", "inconclusive", "b1a1298", "Lauren Tan"]:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_stage_6_tests_the_premise_before_a_third_fix(self) -> None:
        self.assertIn("test the premise before a third fix", stage_text(6))

    def test_stage_7_proves_the_safety_fact_of_a_risky_merge(self) -> None:
        text = stage_text(7)
        for value in ["Merge danger", "the one fact", "`human-path`", "You ran it", "gh pr edit"]:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_implementers_read_building_and_tests(self) -> None:
        for path in IMPLEMENTERS:
            text = path.read_text()
            for value in ["references/building.md", "references/tests.md"]:
                with self.subTest(path=path.name, value=value):
                    self.assertIn(value, text)

    def test_implementers_have_contradictory_test_exit(self) -> None:
        for path in IMPLEMENTERS:
            with self.subTest(path=path.name):
                self.assertIn("CONTRADICTORY_TEST: <test> <spec line>", path.read_text())

    def test_spec_testers_write_one_test_per_flow(self) -> None:
        for path in SPEC_TESTERS:
            with self.subTest(path=path.name):
                self.assertIn("one test per acceptance flow", path.read_text())

    def test_spec_testers_name_files_after_the_slug(self) -> None:
        for path in SPEC_TESTERS:
            with self.subTest(path=path.name):
                text = path.read_text()
                self.assertIn("tests/acceptance/api/test_<slug>.py", text)
                self.assertIn("tests/acceptance/e2e/<slug>.spec.ts", text)
        self.assertIn("feature slug", stage_text(3))

    def test_no_double_check_in_kit_prompts(self) -> None:
        banned = ["double-check", "double check", "use a subagent to verify", "narration"]
        for root in [SKILL_ROOT, REPO_ROOT / "claude", REPO_ROOT / "codex"]:
            for path in root.rglob("*"):
                if path.suffix not in {".md", ".toml", ".json"} or "__pycache__" in path.parts:
                    continue
                text = path.read_text().lower()
                for value in banned:
                    with self.subTest(path=str(path.relative_to(REPO_ROOT)), value=value):
                        self.assertNotIn(value, text)

    def test_mutation_runs_nightly(self) -> None:
        text = (SKILL_ROOT / "references" / "mutation.md").read_text()
        self.assertIn("--head main --run", text)
        self.assertNotIn("GATE_SHA", text)


REFERENCES = SKILL_ROOT / "references"


REPLACED_SKILLS = [
    "superpowers:brainstorming",
    "superpowers:test-driven-development",
    "superpowers:systematic-debugging",
    "superpowers:finishing-a-development-branch",
]


PROJECT_SKILL_DENIES = [
    "Skill(superpowers:verification-before-completion)",
    "Skill(superpowers:test-driven-development)",
    "Skill(superpowers:systematic-debugging)",
    "Skill(superpowers:brainstorming)",
    "Skill(superpowers:finishing-a-development-branch)",
]


def stage_text(number: int) -> str:
    skill = (SKILL_ROOT / "SKILL.md").read_text()
    start = skill.index(f"\n## {number} ")
    end = skill.find("\n## ", start + 1)
    return skill[start:] if end == -1 else skill[start:end]


class SkillStackContractTests(unittest.TestCase):
    """D16, D18, D22 and the 2.0.0 pipeline: the skill each stage names."""

    def setUp(self) -> None:
        self.claude = (REFERENCES / "claude.md").read_text()
        self.codex = (REFERENCES / "codex.md").read_text()

    def assert_only_denied(self, name: str) -> None:
        for path in [SKILL_ROOT / "SKILL.md", *sorted(REFERENCES.glob("*.md"))]:
            for line in path.read_text().splitlines():
                if name in line:
                    with self.subTest(path=path.name, line=line):
                        if not line.lstrip("- ").startswith("Never invoke"):
                            self.assertIn(f"Skill({name})", line)

    def test_codex_skill_list(self) -> None:
        self.assertEqual(
            load_check_setup().CODEX_SKILLS,
            ["grilling", "domain-modeling", "codebase-design", "code-review", "tdd", "diagnosing-bugs", "pr", "prototype", "wayfinder", "retro"],
        )

    def test_claude_project_check_requires_the_skill_denies(self) -> None:
        check_setup = load_check_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"permissions": {"deny": ["Read(./.env)", "Read(./.env.*)"]}}))
            failures: list[str] = []
            check_setup.check_claude_settings(path, failures)
            for rule in PROJECT_SKILL_DENIES:
                with self.subTest(rule=rule):
                    self.assertIn(f"missing Claude deny: {rule}", failures)

    def test_stage_1_uses_grilling_only(self) -> None:
        self.assert_only_denied("superpowers:brainstorming")
        stage = stage_text(1)
        for value in ["grilling", "prototype", "`wayfinder`", "Track S", "one-line reason"]:
            with self.subTest(value=value):
                self.assertIn(value, stage)
        for value in ["mattpocock-skills:grilling", "mattpocock-skills:prototype"]:
            self.assertIn(value, self.claude)
        for value in ["`grilling`", "`prototype`"]:
            self.assertIn(value, self.codex)

    def test_framing_and_spec_text(self) -> None:
        cases = [
            (stage_text(1), [
                "Change or answer", "`$NOTES/Answers/<YYYYMMDD> <title>.md`", "`kind: investigation|performance|question`",
                "two of these four signals", "`$NOTES/Maps/<Effort Title>/handoff.md`", "chart the map from",
                "references/maps.md", "`$NOTES/Maps/*/map.md`",
                "Change or answer, first", "no cap check, no grilling", "Done when the answer note is written",
                "docs/agents/issue-tracker.md",
                "`$NOTES/Features/<YYYYMMDD> <Title>/Framing.md`",
            ]),
            (stage_text(2), ["`wc -w`", "1,500 words", "map decisions"]),
            (stage_text(6), [
                "`## Non-goals`", "`dropped:non-goal`",
                "A non-defect that asks for what a spec `## Non-goals` line rules out", "A defect stays a defect",
            ]),
            ((REFERENCES / "spec.md").read_text(), [
                "## Non-goals", "Catches:", "Misses:", "benchmark.md", "Delivery runs no doubt review",
                "the framing note (SKILL.md Stage 1)",
            ]),
            ((REFERENCES / "maps.md").read_text(), [
                "Wayfinding operations", "Destination (the map's first section)", "is dropped", "Maps/<Effort Title>/map.md", "Blocked by:", "docs/agents/issue-tracker.md", "notes_path.py",
            ]),
            ((REFERENCES / "benchmark.md").read_text(), ["sysctl -n hw.ncpu", "performance check", "the Stage 1 answer line added"]),
            (self.claude, ["`mattpocock-skills:wayfinder` SKILL.md", "plugins/cache/"]),
            (self.codex, ["`wayfinder`", "~/.agents/skills/wayfinder/SKILL.md"]),
        ]
        for text, values in cases:
            for value in values:
                with self.subTest(value=value):
                    self.assertIn(value, text)
        stage = stage_text(1)
        self.assertLess(stage.index("Change or answer"), stage.index("cap check"))
        self.assertLess(stage.index("cap check"), stage.index("`grilling` skill"))

    def test_spec_reference_holds_builder_check_and_notices(self) -> None:
        spec = (REFERENCES / "spec.md").read_text()
        for value in [
            "feature:", "track:", "review_status:", "\nshipped:\npr:\n", "## Acceptance oracle", "Supersedes", "## Decision log",
            "the ideal number is one",
            "You've been handed a design document",
            "nothing important left",
            "Copyright (c) 2025 Jesse Vincent",
            "Run STRIDE over each boundary",
            "Adversarial review. Find what is wrong with this artifact.",
            "Copyright (c) 2025 Addy Osmani",
            "MIT License",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, spec)
        self.assertIn("references/spec.md", stage_text(2))

    def test_stage_4_writes_plans(self) -> None:
        self.assertIn("superpowers:writing-plans", stage_text(4))

    def test_stage_5_executor_by_track(self) -> None:
        stage = stage_text(5)
        for value in [
            "Track M: `superpowers:executing-plans`",
            "Track L: `superpowers:subagent-driven-development`",
            "`tdd`", "`diagnosing-bugs`", "references/building.md", "references/tests.md",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, stage)
        for name in ["superpowers:test-driven-development", "superpowers:systematic-debugging"]:
            self.assert_only_denied(name)
        for value in ["superpowers:executing-plans", "mattpocock-skills:tdd", "mattpocock-skills:diagnosing-bugs"]:
            self.assertIn(value, self.claude)
        for value in ["superpowers:executing-plans", "`tdd`", "`diagnosing-bugs`"]:
            self.assertIn(value, self.codex)

    def test_stage_7_is_dev_loop_own_with_pr(self) -> None:
        stage = stage_text(7)
        self.assert_only_denied("superpowers:finishing-a-development-branch")
        for value in ["`pr`", "git rebase origin/main", "gh pr merge --rebase"]:
            with self.subTest(value=value):
                self.assertIn(value, stage)
        self.assertIn("mattpocock-skills:pr", self.claude)
        self.assertIn("`pr`", self.codex)

    def test_log_audit_names_the_transcript(self) -> None:
        for text, value in [
            (self.claude, "`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/*/$CLAUDE_CODE_SESSION_ID.jsonl`"),
            (self.codex, "`find \"${CODEX_HOME:-$HOME/.codex}/sessions\" -name \"rollout-*-$CODEX_THREAD_ID.jsonl\"`"),
        ]:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_codex_profile_names_the_managed_proxy_and_dev_docker(self) -> None:
        for value in ["network_proxy", "dev-docker", "codex -c default_permissions=dev-docker",
                      'Network access to "<host>" was blocked', "stop item", "[permissions.dev-workspace.network.domains]",
                      "loopback TCP", "Minimum codex-cli 0.165.0"]:
            with self.subTest(value=value):
                self.assertIn(value, self.codex)
        self.assertNotIn("keeps both limits only when no container engine is reachable", self.codex)
        kit = (REPO_ROOT / "reference" / "kit.md").read_text()
        for value in ["managed proxy", "dev-docker", "loopback TCP", "user layer", "[permissions.dev-workspace.network.unix_sockets]",
                      "macOS", "codex-cli 0.165.0", "key by key"]:
            with self.subTest(value=value):
                self.assertIn(value, kit)
        self.assertNotIn("codex-cli 0.162.1", kit)
        note = next(line for line in kit.splitlines() if line.startswith("Codex network scope upgrade:"))
        for value in ["[features]", "[permissions.dev-workspace.network]", "[permissions.dev-workspace.network.domains]",
                      "Update Codex to 0.165.0 first"]:
            with self.subTest(note=value):
                self.assertIn(value, note)


class TracksContractTests(unittest.TestCase):
    """D7, D10, D11, D14, D15: tracks, chained sessions, status detection, the merge rule."""

    def setUp(self) -> None:
        self.skill = (SKILL_ROOT / "SKILL.md").read_text()
        self.header = self.skill[: self.skill.index("\n## 0 ")]

    def test_skill_chains_stages_under_the_grant(self) -> None:
        self.assertNotIn("Run one stage per invocation", self.skill)
        self.assertNotIn("Never chain stages", self.skill)
        description = next(line for line in self.skill.splitlines() if line.startswith("description:"))
        self.assertIn("dev-loop.json", description)
        self.assertNotIn("vault_notes", self.skill)
        for value in [
            "Track S", "Track M", "Track L", "Shaping", "Delivery", "Stages 3 to 7",
            "devloop.py decide", "three fix rounds", "one-way door", "deletes data", "reaches production",
            "usage limit", "Supersedes", "Monitor", "two minutes",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, self.header)
        stage = stage_text(3)
        self.assertNotIn("Tell the user to review", stage)
        self.assertNotIn("/clear", stage)

    def test_kit_drift_session_rule(self) -> None:
        start = self.skill.index("\n## Where each stage runs")
        where = self.skill[start:self.skill.index("\n## 0 ")]
        for value in ["`worktree-newer`: `git fetch origin`; `git show origin/main:.dev-loop/MANIFEST.json`",
                      "main checkout is behind, not an unmerged upgrade: rebase it as the Stage 2 start does, then stop",
                      "Else stop; tell the user to merge the kit upgrade to `main`"]:
            with self.subTest(value=value):
                self.assertIn(value, self.header)
        for value in ["`main-newer`", "Stage 3, 6 or 7 start", "`scripts/devloop.py pause --intent",
                      "--next <stage start>", "`/continue` (Codex `$continue`)", "new session in the worktree",
                      "Main worktree, Stage 3 start (after `git fetch origin`) or Stage 2 start",
                      "start a new session", "Stages 4 and 5 ignore `main-newer`",
                      "rebase the main checkout onto `origin/main` as the Stage 2 start does, then `scripts/devloop.py pause",
                      "Main checkout rebase: in the main worktree, first in `git worktree list` (`git -C <main worktree>`)"]:
            with self.subTest(value=value):
                self.assertIn(value, where)

    def test_feature_branch_has_no_upstream(self) -> None:
        where = self.skill[self.skill.index("\n## Where each stage runs"):self.skill.index("\n## 0 ")]
        for text, value in [
            (where, "branch from `origin/main` with `--no-track`"),
            (stage_text(7), "`git push -u origin HEAD`"),
            ((REPO_ROOT / "skills" / "garden" / "SKILL.md").read_text(), "`origin/main` with `--no-track`"),
            ((REFERENCES / "claude.md").read_text(), "`git worktree add --no-track -b <branch> .claude/worktrees/<slug> origin/main` when absent, run from the main checkout"),
        ]:
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertNotIn("`git -C <main worktree> worktree add", (REFERENCES / "claude.md").read_text())

    def test_claude_worktree_session_and_waits(self) -> None:
        claude = (REFERENCES / "claude.md").read_text()
        codex = (REFERENCES / "codex.md").read_text()
        for value in ["- Worktree session: in an `EnterWorktree` session", "One git command per Bash call",
                      "`git commit -F <file>`", "`grep -r --include=<glob> <pattern> <dir>`",
                      "`ExitWorktree` with `keep` first"]:
            with self.subTest(value=value):
                self.assertIn(value, claude)
        self.assertIn("writes the table to a file with `Write` outside the repo (under `$TMPDIR`), runs `review-save --reviewer <reviewer> < <file>`, then removes the file", claude)
        self.assertNotIn("quoted heredoc", claude)
        self.assertIn("Exception to the SKILL.md Monitor rule", claude)
        self.assertIn("Exception to the SKILL.md rule to watch a long command", codex)
        for text in (claude, codex):
            for value in ["devloop.py gate --summary` directly", "no `logged` wrapper",
                          "counts as watched",
                          "`ci wait --timeout <m>` (default 30)", "at least `<m>` plus 5 minutes"]:
                with self.subTest(value=value):
                    self.assertIn(value, text)

    def test_review_wave_runs_against_origin_main(self) -> None:
        self.assertIn("whole branch against `origin/main`", (SKILL_ROOT / "SKILL.md").read_text())

    def test_deferred_triage_rule(self) -> None:
        deferred = (REFERENCES / "deferred.md").read_text()
        for value in ["Security item", "live check is cheap", "quote its output under the item", "`--help`"]:
            with self.subTest(value=value):
                self.assertIn(value, deferred)
        self.assertIn("triage it (`references/deferred.md`)", stage_text(1))

    def test_verification_budget_moved(self) -> None:
        tests_md = (SKILL_ROOT / "references" / "tests.md").read_text()
        for value in ["## Verification budget", "| T0 |", "| T4 |", "Each project caps T0"]:
            with self.subTest(value=value):
                self.assertIn(value, tests_md)
        self.assertIn("references/tests.md", self.header)

    def test_one_session_hand_off_and_effort(self) -> None:
        for value in ["one session by default", "optional", "high effort", "medium effort"]:
            with self.subTest(value=value):
                self.assertIn(value, self.header)
        for value in ["`/effort medium`", "`/compact`", "`Start Delivery of <slug>`", "own code block", "after the compaction"]:
            with self.subTest(value=value):
                self.assertIn(value, stage_text(2))
        for value in ["`/effort medium`", "`Accept Track S`"]:
            with self.subTest(value=value):
                self.assertIn(value, stage_text(1))
        start = self.skill.index("\n## Detect the stage")
        detect = self.skill[start:self.skill.index("\n## ", start + 1)]
        for value in ["`effort`", "`feature` set, `level` and `want` not null, and they differ", "`/effort <want>`"]:
            with self.subTest(value=value):
                self.assertIn(value, detect)
        for value in ["planner", "high effort"]:
            with self.subTest(value=value):
                self.assertIn(value, stage_text(4))
        self.assertGreater(stage_text(4).index("then Stage 5"), stage_text(4).index("size-check --all"))
        self.assertIn("number outside the block", stage_text(2))
        self.assertNotIn("Session A", self.skill)
        self.assertNotIn("Session B", self.skill)

    def test_skill_detects_by_devloop_status(self) -> None:
        self.assertIn("scripts/devloop.py status", self.header)
        self.assertNotIn("| Check | Stage |", self.skill)

    def test_stage_4_smart_zone_rule(self) -> None:
        stage = stage_text(4)
        for value in ["smart zone", "names each file", "no ruling from another task", "split", "`spec: Spec.md`"]:
            with self.subTest(value=value):
                self.assertIn(value, stage)

    def test_stage_7_merge_rule(self) -> None:
        stage = stage_text(7)
        for value in [
            "git fetch origin", "git rebase origin/main", "scripts/devloop.py gate --summary",
            "scripts/devloop.py ci wait", "`merge: auto`", "`merge: human`", "two-way door", "blast radius",
            "Track L", "gh pr merge --rebase --match-head-commit <sha>", "git rev-parse HEAD", "security item in `## Deferred`",
            "Right after the merge: write `shipped: <merge date>`", "`pr: <PR url>`",
            "into the spec frontmatter in the notes folder", "Track S: no spec, skip",
            "scripts/devloop.py merge-check", "`merge` `auto`", "merge_human_paths",
            "no `--feature`", "When `head` is not `<sha>`, the PR waits for the user",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, stage)
        self.assertNotIn("it checks that HEAD", stage)

    def test_track_s_bug_fix_shows_red_then_green(self) -> None:
        line = next(line for line in stage_text(5).splitlines() if line.startswith("Track S bug fix:"))
        for value in ["failing test", "fail output", "pass output", "PR body", "not committed alone"]:
            with self.subTest(value=value):
                self.assertIn(value, line)

    def test_new_repo_files_and_the_deferred_cap(self) -> None:
        for value in ["`CODING_STANDARDS.md`", "nightly"]:
            with self.subTest(value=value):
                self.assertIn(value, stage_text(0))
        self.assertIn("`## Deferred` holds 20 open items", stage_text(1))

    def test_track_s_has_no_spec_and_no_plan(self) -> None:
        self.assertIn("Track S: no spec, no plan", stage_text(1))
        self.assertIn("Track S: no plan, no executor", stage_text(5))
        self.assertIn("then the Stage 6 review wave, triage and fix wave, then Stage 7", stage_text(5))
        self.assertIn("Stage 1, Stage 5, Stage 6 review wave, triage and fix wave only, Stage 7", self.header)
        self.assertIn("after green CI and the Stage 6 review wave", stage_text(7))
        self.assertNotIn("Tracks S and M", self.skill)
        self.assertNotIn("Tracks S, M", (REFERENCES / "claude.md").read_text())
        self.assertNotIn("Tracks S, M", (REFERENCES / "codex.md").read_text())

    def test_codex_never_invoke_list_names_plain_skills(self) -> None:
        codex = (REFERENCES / "codex.md").read_text()
        self.assertNotIn("Skill(superpowers:", codex)
        line = next(line for line in codex.splitlines() if line.lstrip("- ").startswith("Never invoke"))
        for name in REPLACED_SKILLS:
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", line)


class UsageTextTests(unittest.TestCase):
    def test_usage_docstring_names_the_repo_flag_of_each_command_that_takes_it(self) -> None:
        doc = (SKILL_ROOT / "scripts" / "devloop.py").read_text().split('"""')[1]
        for command in ("decide", "pause"):
            line = next(line for line in doc.splitlines() if line.strip().startswith(f"devloop.py {command} "))
            self.assertIn("--repo", line, command)

    def test_skill_md_has_no_double_blank_line(self) -> None:
        self.assertNotIn("\n\n\n", (SKILL_ROOT / "SKILL.md").read_text())

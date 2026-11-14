"""The workflows as text: CI jobs and shards, the release workflow, its config and scripts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402


class ReleaseWorkflowTests(unittest.TestCase):
    """Oracle 31 to 34: the workflows as text, the release config and scripts."""
    RC = "contains(github.ref_name, '-rc.')"

    def workflow(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text()

    def step(self, text: str, name: str) -> str:
        start = text.index(f"      - name: {name}\n")
        end = text.find("\n      - ", start + 1)
        return text[start:end if end != -1 else len(text)]

    def test_checkout_fetches_all_tags(self) -> None:
        checkout = self.workflow("release.yml").split("actions/checkout@", 1)[1].split("\n\n", 1)[0]
        self.assertIn("fetch-depth: 0", checkout)
        self.assertIn("fetch-tags: true", checkout)

    def test_promoted_step_runs_for_a_final_tag_before_the_changelog(self) -> None:
        text = self.workflow("release.yml")
        step = self.step(text, "Check the kit source of the release candidate")
        self.assertIn(f"if: ${{{{ !{self.RC} }}}}\n", step)
        self.assertIn('run: python3 scripts/repo_rules.py promoted --tag "$GITHUB_REF_NAME"', step)
        self.assertLess(text.index(step), text.index("release-notes --tag"))

    def test_release_step_marks_a_candidate_as_pre_release(self) -> None:
        step = self.step(self.workflow("release.yml"), "Create or update release")
        self.assertIn(f"prerelease: ${{{{ {self.RC} }}}}\n", step)
        self.assertIn(f"makeLatest: ${{{{ !{self.RC} }}}}\n", step)

    def test_release_notes_step(self) -> None:
        text = self.workflow("release.yml")
        self.assertNotIn("git-cliff-action", text)
        step = self.step(text, "Write the release notes")
        self.assertIn('run: python3 .agents/skills/dev-loop/scripts/devloop.py release-notes --tag "$GITHUB_REF_NAME" --output CHANGES.md', step)
        self.assertLess(text.index("Check the kit source of the release candidate"), text.index(step))
        self.assertLess(text.index(step), text.index("bodyFile: CHANGES.md"))

    def test_changes_job(self) -> None:
        ci = self.workflow("ci.yml")
        changes = ci.split("  changes:\n", 1)[1].split("\n  test-shards:\n", 1)[0]
        for needle in ("fetch-depth: 0", "path: .devloop-base", "skip=false",
                       ".devloop-base/.agents/skills/dev-loop/scripts/devloop.py ci-skip", "--github-output"):
            with self.subTest(needle=needle):
                self.assertIn(needle, changes)
        jobs = self.jobs(ci)
        self.assertEqual(list(jobs), ["changes", "test-shards", "test", "acceptance"])
        for name in ("test-shards", "acceptance"):
            with self.subTest(job=name):
                self.assertIn("    needs: changes\n", jobs[name])
                self.assertIn("    if: ${{ !cancelled() && needs.changes.outputs.skip != 'true' }}\n", jobs[name])
        self.assertIn("      fail-fast: false\n", jobs["test-shards"])

    @staticmethod
    def jobs(ci: str) -> dict[str, str]:
        parts = re.split(r"^  ([\w-]+):\n", ci.split("\njobs:\n", 1)[1], flags=re.M)
        return dict(zip(parts[1::2], parts[2::2]))

    def test_the_test_job_passes_only_on_a_skip_or_every_shard_green(self) -> None:
        job = self.jobs(self.workflow("ci.yml"))["test"]
        self.assertIn("    needs: [changes, test-shards]\n    if: ${{ always() }}\n", job)
        self.assertIn("SHARDS: ${{ needs.test-shards.result }}\n", job)
        self.assertIn("SKIP: ${{ needs.changes.outputs.skip }}\n", job)
        run = job.split("        run: ", 1)[1].strip().strip("'")
        for skip, shards, code in (("true", "skipped", 0), ("false", "success", 0), ("", "skipped", 1),
                                   ("false", "failure", 1), ("false", "cancelled", 1), ("false", "skipped", 1)):
            with self.subTest(skip=skip, shards=shards):
                result = subprocess.run(["bash", "-c", run], env={"SKIP": skip, "SHARDS": shards})
                self.assertEqual(result.returncode, code)

    def test_the_shard_jobs_pass_their_part_to_the_runner(self) -> None:
        jobs = self.jobs(self.workflow("ci.yml"))
        self.assertIn("        part: [installer, 1/4, 2/4, 3/4, 4/4]\n", jobs["test-shards"])
        self.assertIn("DEVLOOP_TEST_SHARD: ${{ matrix.part }}\n", self.step(jobs["test-shards"], "Skill suite"))
        self.assertNotIn("DEVLOOP_TEST_SHARD", jobs["acceptance"])
        self.assertIn('      DEVLOOP_TEST_JOBS: "8"\n', jobs["acceptance"])
        self.assertNotIn("DEVLOOP_TEST_JOBS", jobs["test-shards"])
        self.assertIn("if: ${{ matrix.part == 'installer' }}\n", self.step(jobs["test-shards"], "Installer suite"))

    def test_a_retarget_runs_ci_again_on_the_new_base(self) -> None:
        ci = self.workflow("ci.yml")
        self.assertIn("  pull_request:\n    types: [opened, synchronize, reopened, edited]\n", ci)
        cancel = "cancel-in-progress: ${{ github.event_name == 'pull_request' && github.event.action != 'edited' }}\n"
        self.assertIn(cancel, ci)
        self.assertNotIn("github.event.action", ci.replace(cancel, ""))
        for path in ("reference/kit.md", "skills/release/SKILL.md"):
            with self.subTest(path=path):
                self.assertIn("List `edited` in the `pull_request` types", (ROOT / path).read_text())

    def test_changes_job_skips_only_on_a_default_branch_base(self) -> None:
        sources = {"ci.yml": self.workflow("ci.yml"), "reference/kit.md": (ROOT / "reference/kit.md").read_text(),
                   "skills/release/SKILL.md": (ROOT / "skills/release/SKILL.md").read_text()}
        for source, text in sources.items():
            step = text.split("      - id: skip\n", 1)[1].split("\n```", 1)[0].split("\n\n", 1)[0]
            env_block, run = step.split("        run: |\n", 1)
            for event, target, skip in (("pull_request", "trunk", "true"), ("push", "trunk", "true"),
                                        ("push", "feature", "false"), ("pull_request", "other", "false")):
                with self.subTest(source=source, event=event, target=target), tempfile.TemporaryDirectory() as tmp:
                    scripts = Path(tmp, ".devloop-base/.agents/skills/dev-loop/scripts")
                    scripts.mkdir(parents=True)
                    (scripts / "ci_skip.py").write_text("")
                    (scripts / "devloop.py").write_text(
                        "import os\nopen(os.environ['GITHUB_OUTPUT'], 'a').write('skip=true\\n')\n")
                    env = {**os.environ, "GITHUB_OUTPUT": str(Path(tmp, "out"))}
                    values = {"${{ github.base_ref || github.ref_name }}": target, "${{ github.event.repository.default_branch }}": "trunk"}
                    for line in env_block.splitlines()[1:]:
                        key, value = line.strip().split(": ", 1)
                        env[key] = values[value]
                    script = textwrap.dedent(run).replace(
                        "${{ github.event.pull_request.base.sha || github.event.before }}", "BASE")
                    subprocess.run(["bash", "-euc", script], cwd=tmp, env=env, check=True)
                    self.assertEqual(Path(tmp, "out").read_text(), f"skip={skip}\n")

    def test_dev_loop_json(self) -> None:
        config = json.loads((ROOT / "dev-loop.json").read_text())
        self.assertEqual(config["release_notes"], "written")
        self.assertEqual(config["version_files"], ["package.json", "package-lock.json"])
        for key, value in (("release_prepare", "node bin/install.mjs upgrade"),
                           ("release_check", "scripts/release-check.sh"),
                           ("release_verify", "scripts/release-verify.sh")):
            with self.subTest(key=key):
                self.assertEqual(config["commands"][key], value)

    def test_release_check_script(self) -> None:
        text = (ROOT / "scripts" / "release-check.sh").read_text()
        self.assertIn("set -euo pipefail", text)
        self.assertIn("scripts/canary.sh", text)
        final = text.split("-rc.", 1)[1]
        self.assertIn('scripts/repo_rules.py promoted --tag "$RELEASE_TAG" --sha HEAD', final)

    def test_release_verify_cleans_up(self) -> None:
        script = ROOT / "scripts" / "release-verify.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, stubs = root / "repo", root / "bin"
            repo.mkdir()
            stubs.mkdir()
            for args in (("init", "-q", "-b", "main"), ("config", "user.email", "a@b"), ("config", "user.name", "a"),
                         ("commit", "-q", "--allow-empty", "-m", "a")):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            for code, ok, extra in ((0, True, ""), (1, False, ""), (0, False, "touch stray.txt\n")):
                with self.subTest(npx_exit=code, untracked=bool(extra)):
                    (stubs / "npx").write_text(f"#!/bin/sh\n{extra}exit {code}\n")
                    (stubs / "npx").chmod(0o755)
                    scratch = root / f"tmp{code}{len(extra)}"
                    scratch.mkdir()
                    env = {**os.environ, "RELEASE_TAG": "v9.9.9", "RELEASE_VERSION": "9.9.9", "TMPDIR": str(scratch),
                           "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}
                    result = subprocess.run([str(script)], cwd=repo, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
                    self.assertEqual(list(scratch.iterdir()), [])

    def test_release_verify_never_waits_on_a_prompt(self) -> None:
        script = ROOT / "scripts" / "release-verify.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, stubs, seen = root / "repo", root / "bin", root / "seen.txt"
            repo.mkdir()
            stubs.mkdir()
            for args in (("init", "-q", "-b", "main"), ("config", "user.email", "a@b"), ("config", "user.name", "a"),
                         ("commit", "-q", "--allow-empty", "-m", "a")):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            (stubs / "npx").write_text(
                "#!/bin/sh\n"
                '{ echo "prompt=$GIT_TERMINAL_PROMPT"\n'
                '  echo "url=$(git config --get url.git@github.com:.insteadof)"\n'
                '  echo "helper=$(git config --get credential.helper)"\n'
                f'  echo "fsmonitor=$(git config --get core.fsmonitor)"; }} > "{seen}"\n')
            (stubs / "npx").chmod(0o755)
            env = {**os.environ, "RELEASE_TAG": "v9.9.9", "TMPDIR": tmp, "GIT_CONFIG_COUNT": "2",
                   "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false",
                   "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": "osxkeychain",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                   "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}
            result = subprocess.run([str(script)], cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(seen.read_text().splitlines(), [
                "prompt=0", "url=https://github.com/", "helper=", "fsmonitor=false"])

    def test_test_and_acceptance_jobs_install_git_cliff_before_the_suite(self) -> None:
        ci = self.workflow("ci.yml")
        jobs = self.jobs(ci)
        test_job, acceptance_job = jobs["test-shards"], jobs["acceptance"]
        for job, suite in ((test_job, "run: npm test"), (acceptance_job, "run: scripts/acceptance.sh")):
            self.assertIn("git-cliff-2.14.2-x86_64-unknown-linux-gnu.tar.gz", job)
            self.assertIn("24f397c733add5390fdceee3a2088588ab0d5f944ce00d34cb7029b888cf2db4  git-cliff.tar.gz", job)
            self.assertIn('echo "$HOME/.local/bin" >> "$GITHUB_PATH"', job)
            self.assertLess(job.index("git-cliff-2.14.2"), job.index(suite))


class ImpactMapWorkflowTests(unittest.TestCase):
    def files(self) -> tuple[str, str]:
        folder = ROOT / ".github" / "workflows"
        return (folder / "impact-map.yml").read_text(), (folder / "ci.yml").read_text()

    def test_triggers_permissions_and_limits(self) -> None:
        text, _ = self.files()
        for needle in ('cron: "0 5 * * 1"', "workflow_dispatch:", "contents: write", "issues: write",
                       "timeout-minutes: 120", "group: impact-map", 'DEVLOOP_REQUIRE_DOCKER: "1"'):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_installs_what_the_shards_install(self) -> None:
        text, ci = self.files()
        shards = ReleaseWorkflowTests.jobs(ci)["test-shards"]
        for action in ("actions/checkout", "actions/setup-node", "actions/setup-python"):
            with self.subTest(action=action):
                uses = re.compile(rf"uses: {action}@\S+ # \S+")
                self.assertEqual(uses.findall(text), uses.findall(shards))
        self.assertIn("node-version: 24", text)
        self.assertIn('python-version: "3.14"', text)
        for name in ("Install Playwright and Chromium", "Install git-cliff"):
            with self.subTest(step=name):
                self.assertEqual(ReleaseWorkflowTests.step(self, text, name).strip(),
                                 ReleaseWorkflowTests.step(self, shards, name).strip())
        self.assertIn("sudo apt-get install -y strace", text)

    def test_the_checkout_is_main(self) -> None:
        text, _ = self.files()
        self.assertRegex(text, r"actions/checkout@\S+ # \S+\n\s+with:\n\s+ref: main\n")

    def test_trace_and_push_steps(self) -> None:
        text, _ = self.files()
        trace = ReleaseWorkflowTests.step(self, text, "Trace the suites")
        self.assertIn("python3 scripts/impact_map.py trace --jobs 4", trace)
        push = ReleaseWorkflowTests.step(self, text, "Push the new map")
        for needle in ("git status --porcelain -- tests/impact-map.json", "git switch -c chore/impact-map",
                       "🔧(tests) #327: Refresh the impact map", "git push --force origin chore/impact-map",
                       "Impact map refresh", "compare/main...chore/impact-map", "GH_TOKEN: ${{ github.token }}"):
            with self.subTest(needle=needle):
                self.assertIn(needle, push)
        self.assertLess(text.index(trace), text.index(push))

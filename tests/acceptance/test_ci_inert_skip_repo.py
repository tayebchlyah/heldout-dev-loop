#!/usr/bin/env python3
"""Held-out acceptance tests of spec CI Inert Skip, this repo's files part: flow F3 and one test per oracle line
no flow holds whole (O<n>). The `ci-skip` and `release-check` part: `test_ci_inert_skip.py`.

Seam 2: `dev-loop.json`, `.github/workflows/ci.yml`, `.github/rulesets/main.json` and the docs read as text, JSON
or YAML; the run step of job `test` run with `bash -c` and env values.

Fixture choices the spec leaves open:
- `ci.yml` is read with Ruby's YAML loader (no YAML module ships with Python), as the release workflow tests do.
- A step env value counts when it is set on the step or on its job.
- Oracle 18 (no ruleset change) is checked in F3, beside the job graph whose job names it lists.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"

D9 = ["assets/*", ".github/ISSUE_TEMPLATE/*", ".github/PULL_REQUEST_TEMPLATE.md", ".github/rulesets/*",
      "CODE_OF_CONDUCT.md", "scripts/audit-image/*"]


def load_ci() -> dict:
    ruby = shutil.which("ruby")
    if not ruby:
        raise AssertionError("ruby not on PATH: the ci.yml tests read the workflow with Ruby's YAML loader")
    result = subprocess.run([ruby, "-ryaml", "-rjson", "-e", "print JSON.generate(YAML.safe_load(STDIN.read))"],
                            input=CI_YML.read_text(), capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"ci.yml does not parse: {result.stderr}")
    return json.loads(result.stdout)


def needs(job: dict) -> list[str]:
    value = job.get("needs")
    return [value] if isinstance(value, str) else list(value or [])


def condition(job: dict) -> str:
    return re.sub(r"\s+", " ", str(job.get("if", "")))


def step_env(job: dict, step: dict) -> dict[str, str]:
    return {**{k: str(v) for k, v in (job.get("env") or {}).items()},
            **{k: str(v) for k, v in (step.get("env") or {}).items()}}


class CiGraphFlow(unittest.TestCase):

    def test_f3_ci_graph(self) -> None:
        """F3 (oracle 15, 16, 17, 18): job `hygiene` runs the two hygiene suites only on a skip, with no install;
        job `test` needs `changes`, `test-shards` and `hygiene`, `if` `${{ always() }}`, and its run step gives
        the aggregate verdict for each env case; `test-shards` and `acceptance` run only when not skipped; the
        ruleset still requires exactly `changes`, `test`, `acceptance`."""
        jobs = load_ci().get("jobs") or {}

        hygiene = jobs.get("hygiene")
        self.assertIsInstance(hygiene, dict, "no job hygiene in ci.yml")
        self.assertEqual(needs(hygiene), ["changes"])
        cond = condition(hygiene)
        self.assertIn("!cancelled()", cond)
        self.assertRegex(cond, r"needs\.changes\.outputs\.skip\s*==\s*'true'|'true'\s*==\s*needs\.changes\.outputs"
                               r"\.skip")
        steps = hygiene.get("steps") or []
        runs = [str(step.get("run", "")) for step in steps]
        self.assertTrue([r for r in runs if "node --test tests/unit/node/payload.test.mjs" in r], runs)
        python = [step for step in steps if "python3 tests/unit/python/test_dev_loop.py" in str(step.get("run", ""))]
        self.assertEqual(len(python), 1, runs)
        self.assertEqual(step_env(hygiene, python[0]).get("DEVLOOP_TEST_MODULES"), "test_repo_rules,test_stacks_setup")
        for step in steps:
            text = json.dumps(step).lower()
            for word in ("npm ci", "playwright", "git-cliff"):
                self.assertNotIn(word, text, step)

        test = jobs.get("test")
        self.assertIsInstance(test, dict, "no job test in ci.yml")
        for name in ("changes", "test-shards", "hygiene"):
            self.assertIn(name, needs(test))
        self.assertEqual(str(test.get("if", "")).strip(), "${{ always() }}")
        run_steps = [step for step in test.get("steps") or [] if step.get("run")]
        self.assertEqual(len(run_steps), 1, test)
        step = run_steps[0]
        env = step_env(test, step)
        for key, source in (("SKIP", "needs.changes.outputs.skip"), ("SHARDS", "needs.test-shards.result"),
                            ("HYGIENE", "needs.hygiene.result")):
            self.assertIn(source, env.get(key, ""), (key, env))
        for skip, hygiene_result, shards, passes in (("true", "success", "skipped", True),
                                                     ("false", "skipped", "success", True),
                                                     ("true", "failure", "skipped", False),
                                                     ("true", "skipped", "skipped", False),
                                                     ("false", "skipped", "failure", False),
                                                     ("", "skipped", "skipped", False)):
            with self.subTest(SKIP=skip, HYGIENE=hygiene_result, SHARDS=shards):
                result = subprocess.run(["bash", "-c", str(step["run"])], cwd=REPO_ROOT, capture_output=True,
                                        text=True, timeout=60,
                                        env={"PATH": os.environ.get("PATH", ""), "SKIP": skip,
                                             "HYGIENE": hygiene_result, "SHARDS": shards})
                self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)

        for name in ("test-shards", "acceptance"):
            with self.subTest(job=name):
                job = jobs.get(name)
                self.assertIsInstance(job, dict, f"no job {name} in ci.yml")
                self.assertIn("changes", needs(job))
                cond = condition(job)
                self.assertIn("!cancelled()", cond)
                self.assertIn("needs.changes.outputs.skip != 'true'", cond)

        rules = json.loads(RULESET.read_text()).get("rules") or []
        contexts = [check.get("context") for rule in rules if rule.get("type") == "required_status_checks"
                    for check in (rule.get("parameters") or {}).get("required_status_checks") or []]
        self.assertEqual(sorted(contexts), ["acceptance", "changes", "test"])


class RepoFiles(unittest.TestCase):

    def test_o14_this_repo_lists_its_inert_paths(self) -> None:
        """O14: `dev-loop.json` `inert_paths` is exactly the D9 list, in that order."""
        config = json.loads((REPO_ROOT / "dev-loop.json").read_text())
        self.assertEqual(config.get("inert_paths"), D9)

    def test_o19_docs_name_the_key_the_rule_and_the_term(self) -> None:
        """O19: `reference/cli/ci-skip.md` names `inert_paths`, rule `(f)` and `kind` `inert`; `reference/kit.md`
        names `inert_paths` and a `hygiene` job; `docs/configuration.md` names `inert_paths`; `GLOSSARY.md` has a
        term line for "inert path"."""
        cli = (REPO_ROOT / "reference" / "cli" / "ci-skip.md").read_text()
        self.assertIn("inert_paths", cli)
        self.assertIn("(f)", cli)
        self.assertRegex(cli, r"[`\"']inert[`\"']")
        kit = (REPO_ROOT / "reference" / "kit.md").read_text()
        self.assertIn("inert_paths", kit)
        self.assertIn("hygiene", kit)
        self.assertIn("inert_paths", (REPO_ROOT / "docs" / "configuration.md").read_text())
        glossary = (REPO_ROOT / "GLOSSARY.md").read_text()
        self.assertRegex(glossary, re.compile(r"^\*\*Inert path\*\*:", re.M | re.I))


if __name__ == "__main__":
    unittest.main()

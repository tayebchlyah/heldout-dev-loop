"""The release guards, the release audit setup, the kit files and the release text."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

from support import GuardRepo

import check_setup  # noqa: E402


class ReleaseGuardTests(GuardRepo, unittest.TestCase):
    """Both git-guard.sh copies, fed hook JSON on stdin, with a stub release-check CLI in the main worktree."""

    def test_a_release_tag_push_carries_the_release_check_verdict(self) -> None:
        # Oracle 46, 47, 49, 50, 51, 52.
        for harness, mode, command, extra, decision, parts, absent in [
            ("claude", "fail", "git push origin v1.0.0", {}, "ask", ["a tag push", "release-check: no-audit, open-security"], None),
            ("claude", "ok", "git push origin v1.0.0", {}, "ask",
             ["Global rule: a tag push needs explicit permission each time; release-check: ok."], None),
            ("claude", "ok", "git push origin feat/x v1.0.0", {}, "ask", ["release-check: ok"], None),
            ("claude", "ok", "git push origin HEAD:refs/tags/v2.0.0", {}, "ask", ["release-check: ok"], None),
            ("claude", "ok", "git push origin v1.0.0 release-1", {}, "ask", ["a tag push"], "release-check"),
            ("claude", "ok", "git push origin refs/tags/v1.0.0", {}, "ask", ["release-check: ok"], None),
            ("claude", "ok", "git push origin tag v1.0.0", {}, "ask", ["release-check: ok"], None),
            ("codex", "fail", "git push origin v1.0.0", {}, "deny", ["a tag push", "release-check: no-audit"], None),
            ("claude", "fail", "git push origin v1.0.0", {"agent_id": "a1"}, "deny", ["subagent"], None),
            ("claude", None, "git push origin v1.0.0", {}, "ask", ["release-check failed: no CLI"], None),
            ("codex", None, "git push origin v1.0.0", {}, "deny", ["release-check failed"], None),
            ("claude", "ok", "git push --tags", {}, "ask", ["tag push"], "release-check"),
            ("claude", "ok", "git push --follow-tags origin feat/x", {}, "ask", ["tag push"], "release-check"),
            ("claude", "ok", "git push origin release-1", {}, "ask", ["a tag push"], "release-check"),
            ("claude", "ok", "git push origin 'refs/tags/*'", {}, "ask", [], "release-check"),
        ]:
            with self.subTest(harness=harness, mode=mode, command=command, extra=extra):
                self.stub(mode)
                got, reason, _ = self.guard(harness, command, **extra)
                self.assertEqual(got, decision, reason)
                for part in parts:
                    self.assertIn(part, reason)
                if absent:
                    self.assertNotIn(absent, reason)
        self.assertIn(f"release-check --repo {self.repo} --tag v1.0.0", self.calls.read_text())
        self.assertIn(f"release-check --repo {self.repo} --sha {self.c}", self.calls.read_text())
        # A subagent is denied before the CLI runs.
        self.calls.unlink()
        self.stub("slow")
        got, reason, seconds = self.guard("claude", "git push origin v1.0.0", agent_id="a1")
        self.assertEqual((got, self.calls.exists()), ("deny", False), reason)
        self.assertLess(seconds, 3)

    def test_gh_release_create_checks_the_commit_it_tags(self) -> None:
        # Oracle 48: a local tag, else --target, else refs/remotes/origin/main; any tag name asks.
        self.stub("fail")
        for command, sha in [("gh release create v1.0.0 --notes x", self.a), (f"gh release create v2.0.0 --target {self.b}", self.b),
                             ("gh release create v2.0.0 --target=main --title 'v2 release'", self.c),
                             ("gh release create v2.0.0", self.c), ("gh release create release-1", self.a),
                             ("cd /tmp && command gh release create v2.0.0", None)]:
            with self.subTest(command=command):
                self.calls.unlink(missing_ok=True)
                got, reason, _ = self.guard("claude", command)
                self.assertEqual(got, "ask", reason)
                if sha:
                    self.assertIn(f"a release create at {sha} needs explicit permission each time; release-check: no-audit", reason)
                    self.assertEqual(self.calls.read_text(), f"release-check --repo {self.repo} --sha {sha}\n")
        got, reason, _ = self.guard("claude", "gh release create v2.0.0 --target nowhere")
        self.assertIn("release-check failed: target nowhere does not resolve", reason)
        got, reason, _ = self.guard("codex", "gh release create v1.0.0")
        self.assertEqual((got, "release-check: no-audit" in reason), ("deny", True), reason)
        got, reason, _ = self.guard("claude", "gh release create v1.0.0", agent_id="a1")
        self.assertEqual(got, "deny", reason)
        for command in ["gh pr create --title x", "gh release list", "gh -R o/r release view v1.0.0"]:
            self.assertEqual(self.guard("claude", command)[0], "allow", command)
        # gh flags around release, the new alias, and a word the parser cannot read.
        for harness, decision in [("claude", "ask"), ("codex", "deny")]:
            for command, part in [("gh release new v1.0.0", f"at {self.a}"), ("gh -R o/r release create v1.0.0", "a gh release on another repository"),
                                  ("gh release -R o/r create v1.0.0", "a gh release on another repository"),
                                  ("gh --repo=o/r release --repo o/r new v1.0.0", "a gh release on another repository"),
                                  ("gh release $SUB v1.0.0", "release-check failed"),
                                  ("gh release -R o/r 'cr'$X v1.0.0", "release-check failed")]:
                with self.subTest(harness=harness, command=command):
                    got, reason, _ = self.guard(harness, command)
                    self.assertEqual(got, decision, reason)
                    self.assertIn(part, reason)

    def test_a_slow_or_broken_release_check_still_asks(self) -> None:
        # Oracle 48a and Review Focus 3: a timeout within the hook's 5 s, a stderr line with a quote stays valid JSON.
        self.stub("slow")
        got, reason, seconds = self.guard("claude", "git push origin v1.0.0")
        self.assertEqual(got, "ask", reason)
        self.assertIn("release-check failed: timeout", reason)
        self.assertLess(seconds, 5)
        self.stub("quote")
        for harness, decision in [("claude", "ask"), ("codex", "deny")]:
            got, reason, _ = self.guard(harness, "gh release create v1.0.0")
            self.assertEqual(got, decision, reason)
            self.assertIn('release-check failed: bad "quote" and \\ here', reason)
            self.assertNotIn("second line", reason)

    def test_a_refspec_tag_push_checks_the_commit_of_its_source(self) -> None:
        # D14: <src>:refs/tags/v* checks the commit of <src>, not a stale local tag; a plain tag name keeps --tag.
        self.git("tag", "v2.0.0", self.b)
        self.stub("ok")
        for command, want in [("git push origin main:refs/tags/v2.0.0", f"--sha {self.c}"),
                              ("git push origin HEAD:refs/tags/v3.0.0", f"--sha {self.c}"),
                              (f"git push origin {self.b}:refs/tags/v2.0.0", f"--sha {self.b}"),
                              ("git push origin refs/tags/v2.0.0", "--tag v2.0.0"), ("git push origin v2.0.0", "--tag v2.0.0")]:
            with self.subTest(command=command):
                self.calls.unlink(missing_ok=True)
                got, reason, _ = self.guard("claude", command)
                self.assertEqual(got, "ask", reason)
                self.assertIn("release-check: ok", reason)
                self.assertEqual(self.calls.read_text(), f"release-check --repo {self.repo} {want}\n")
                if want.startswith("--sha"):
                    self.assertIn(want.removeprefix("--sha "), reason)
        self.calls.unlink()
        for harness, decision in [("claude", "ask"), ("codex", "deny")]:
            got, reason, _ = self.guard(harness, "git push origin nowhere:refs/tags/v3.0.0")
            self.assertEqual(got, decision, reason)
            self.assertIn("release-check failed: nowhere does not resolve", reason)
        self.assertFalse(self.calls.exists())

    def test_the_cli_comes_from_the_hook_project_only(self) -> None:
        # Security review: a planted devloop.py in another repository never runs inside the hook.
        evil = self.tmp / "evil"
        evil.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(evil)], check=True)
        subprocess.run(["git", "-C", str(evil), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                       check=True)
        subprocess.run(["git", "-C", str(evil), "tag", "v1.0.0"], check=True)
        marker = self.tmp / "marker"
        planted = evil / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"
        planted.parent.mkdir(parents=True)
        planted.write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
        self.stub("ok")
        for harness, decision in [("claude", "ask"), ("codex", "deny")]:
            for command in [f"git -C {evil} push o x:refs/tags/v1", f"cd {evil} && git push origin v1.0.0",
                            f"cd {evil} && gh release create v1.0.0"]:
                with self.subTest(harness=harness, command=command):
                    got, reason, _ = self.guard(harness, command)
                    self.assertEqual(got, decision, reason)
                    self.assertIn("release-check failed: another repository", reason)
        self.assertFalse(marker.exists())
        self.assertFalse(self.calls.exists())


class ReleaseSetupTests(unittest.TestCase):
    """Oracle 53 to 55 and D12: check_setup lines for security_audit, the Docker engine and image, the vault ignore."""

    SHAPE = 'security_audit in dev-loop.json must be {"images": {<name>: <image>}}, names ^[a-z0-9][a-z0-9_-]*$'
    ENGINE = "start a Docker engine for the release audit sandbox"

    def repo(self, tmp: str, audit: object = None) -> Path:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        config = {} if audit is None else {"security_audit": audit}
        (repo / "dev-loop.json").write_text(json.dumps(config))
        return repo

    def docker(self, tmp: str, info: int, present: tuple[str, ...] = ()) -> dict[str, str]:
        """A fake docker on PATH: `info` exits `info`, `image inspect X` exits 0 only for X in present."""
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        script = bin_dir / "docker"
        script.write_text(
            '#!/bin/sh\n[ "$1" = info ] && exit %d\nfor i in %s; do [ "$3" = "$i" ] && exit 0; done\nexit 1\n'
            % (info, " ".join(present) or "none")
        )
        script.chmod(0o755)
        return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    def vault(self, tmp: str, local: dict, ignore: str) -> tuple[Path, Path]:
        repo = self.repo(tmp)
        vault = Path(tmp) / "vault"
        notes = vault / "Project"
        notes.mkdir(parents=True)
        (vault / ".obsidian").mkdir()
        (repo / "dev-loop.local.json").write_text(json.dumps({"notes": str(notes), **local}))
        subprocess.run(["git", "init", "-q", str(vault)], check=True)
        (vault / ".gitignore").write_text(ignore)
        return Path(tmp) / "home", repo

    def test_vault_ignore_line(self) -> None:
        line = "add Security Audit/ to the vault .gitignore"
        rows = [({}, "Dev Loop Board/\n", [line]), ({"board_vault_git": "shared"}, "", []),
                ({}, "Dev Loop Board/\nSecurity Audit/\n", [])]
        for local, ignore, want in rows:
            with self.subTest(local=local, ignore=ignore), tempfile.TemporaryDirectory() as tmp:
                home, repo = self.vault(tmp, local, ignore)
                got = [x for x in check_setup.check_board_vault(home, repo, {}) if "Security Audit" in x]
                self.assertEqual(got, want)

    def test_vault_ignore_line_without_obsidian_folder(self) -> None:
        line = "add Security Audit/ to the vault .gitignore"
        rows = [({}, "", True, [line]), ({}, "Security Audit/\n", True, []),
                ({"board_vault_git": "shared"}, "", True, []), ({}, "", False, [])]
        for local, ignore, git, want in rows:
            with self.subTest(local=local, ignore=ignore, git=git), tempfile.TemporaryDirectory() as tmp:
                repo = self.repo(tmp)
                vault = Path(tmp) / "vault"
                notes = vault / "Projects" / "name"
                notes.mkdir(parents=True)
                (repo / "dev-loop.local.json").write_text(json.dumps({"notes": str(notes), **local}))
                if git:
                    subprocess.run(["git", "init", "-q", str(vault)], check=True)
                    (vault / ".gitignore").write_text(ignore)
                got = [x for x in check_setup.check_board_vault(Path(tmp) / "home", repo, {}) if "Security Audit" in x]
                self.assertEqual(got, want)

    def test_bad_shapes_fail_and_a_good_one_does_not(self) -> None:
        bad = [{"images": {"T": "x"}}, {"images": {"t": ""}}, {"images": []}, {"other": 1}, "x",
               {"images": {"t": "x"}, "other": 1}, {"images": {"t": 5}}]
        for audit in bad:
            with self.subTest(audit=audit), tempfile.TemporaryDirectory() as tmp:
                repo = self.repo(tmp, audit)
                self.assertIn(self.SHAPE, check_setup.check_project_config(repo))
                self.assertEqual(check_setup.check_release_sandbox(repo), [])
        for audit in [None, {"images": {}}, {"images": {"py-3_1": "img:1"}}]:
            with self.subTest(audit=audit), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(check_setup.check_project_config(self.repo(tmp, audit)), [])

    def test_docker_engine_and_image_actions(self) -> None:
        pull = "pull the release audit sandbox image img:2 (security_audit.images.b)"
        rows = [(None, 1, (), []), ({"images": {}}, 1, (), []), ({"images": {"a": "img:1"}}, 1, (), [self.ENGINE]),
                ({"images": {"a": "img:1", "b": "img:2"}}, 0, ("img:1",), [pull]),
                ({"images": {"a": "img:1"}}, 0, ("img:1",), [])]
        for audit, info, present, want in rows:
            with self.subTest(audit=audit, info=info), tempfile.TemporaryDirectory() as tmp:
                with unittest.mock.patch.dict(os.environ, self.docker(tmp, info, present)):
                    self.assertEqual(check_setup.check_release_sandbox(self.repo(tmp, audit)), want)

    def test_missing_docker_binary_is_an_engine_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {"PATH": str(Path(tmp) / "none")}):
            repo = self.repo(tmp, {"images": {"a": "img:1"}})
            self.assertEqual(check_setup.check_release_sandbox(repo), [self.ENGINE])

    def test_docker_calls_have_a_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {"images": {"a": "img:1"}})
            with unittest.mock.patch.object(check_setup.subprocess, "run",
                                            side_effect=subprocess.TimeoutExpired("docker", 10)) as run:
                self.assertEqual(check_setup.check_release_sandbox(repo), [self.ENGINE])
            self.assertEqual(run.call_args.kwargs["timeout"], 10)


class ReleaseKitTests(unittest.TestCase):
    SKILL = Path(__file__).resolve().parents[3] / "skills" / "release-audit"

    def test_no_engine_branch_names_the_codex_restart(self) -> None:
        text = (self.SKILL / "SKILL.md").read_text()
        branch = text.split("- else (", 1)[1].split("\n4. ", 1)[0]
        self.assertIn("codex -c default_permissions=dev-docker", branch)

    def test_skill_is_user_invoked_only(self) -> None:
        text = (self.SKILL / "SKILL.md").read_text()
        head = text.split("---")[1]
        self.assertIn("name: release-audit\n", head)
        self.assertIn("disable-model-invocation: true", head)
        self.assertIn("allow_implicit_invocation: false", (self.SKILL / "agents" / "openai.yaml").read_text())

    def test_text_names_the_commands_and_the_rules(self) -> None:
        text = (self.SKILL / "SKILL.md").read_text()
        for name in ["release-plan", "release-record", "release-check", "sandbox-run.py", "sandbox.runner",
                     "never push a tag", "never run the audit in a subagent", "run-incomplete", "incomplete_reason",
                     "No approved sandbox: ", "Approved OS-enforced sandbox for every target-controlled execution",
                     "30 min", "19 USD", "quick", "`tests/" "acceptance` as out of the audit scope",
                     "search named folders or exclude the suite"]:
            self.assertIn(name, text.replace("Never push a tag", "never push a tag")
                          .replace("Never run the audit in a subagent", "never run the audit in a subagent"), name)
        self.assertNotIn("\u2014", text)

    def test_threat_model_and_low_lines(self) -> None:
        text = (self.SKILL / "SKILL.md").read_text()
        step4 = text.split("\n4. ")[1].split("\n5. ")[0]
        step7 = text.split("\n7. ")[1].split("\n\n")[0]
        for name in ["stop agent mistakes", "not a wall", "hardening", "`low`"]:
            self.assertIn(name, step4, name)
        for name in ["`low`", "Gardener"]:
            self.assertIn(name, step7, name)


class ReleaseSkillTextTests(unittest.TestCase):
    """Oracle 35, 36 and 38: the /release and /release-notes skills and the Stage 7 line."""
    ROOT = Path(__file__).resolve().parents[3]

    def skill(self, name: str) -> str:
        text = (self.ROOT / "skills" / name / "SKILL.md").read_text()
        head = text.split("---")[1]
        self.assertIn(f"name: {name}\n", head)
        self.assertIn("disable-model-invocation: true", head)
        self.assertEqual((self.ROOT / "skills" / name / "agents" / "openai.yaml").read_text(),
                         "policy:\n  allow_implicit_invocation: false\n")
        return text

    def test_release_skill(self) -> None:
        text = self.skill("release")
        for name in ["gh pr merge --rebase", "git push origin", "gh release create", "skills/release-audit/SKILL.md",
                     "release-check --sha HEAD", "commands.release_prepare", "release_check", "release_verify",
                     "release/<tag>", "\U0001f516(release)", "RELEASE_VERSION", "RELEASE_TAG", "ci-skip", ".devloop-base"]:
            self.assertIn(name, text, name)
        self.assertLess(text.index("skills/release-audit/SKILL.md"), text.index("release/<tag>"))
        self.assertLess(text.index("commands.gate"), text.index("git push"))
        self.assertLess(text.index("Stop 1"), text.index("gh pr merge --rebase"))
        self.assertIn("gh pr merge --rebase --match-head-commit <sha>", text)
        self.assertIn("Then `ci wait`: a verdict other than `pass`: stop, report it.", text)
        self.assertLess(text.index("Stop 2"), text.index("git push origin <tag>"))
        self.assertLess(text.index("Stop 2"), text.index("gh release create"))

    def test_release_step_3_fix_wave(self) -> None:
        step3 = self.skill("release").split("\n3. ")[1].split("\n4. ")[0]
        for name in ["release-record --fix-wave", "fix #<n>", "accept", "close"]:
            self.assertIn(name, step3, name)
        self.assertLess(step3.index("release-record --fix-wave"), step3.index("skills/release-audit/SKILL.md"))
        self.assertIn("Any other `--fix-wave` error (`foreign-commit`, `wave-exists`, `not-descendant`, `no-audit`, `no-project-note`), or any other `stale-audit`: follow the steps of `skills/release-audit/SKILL.md`", step3)

    def test_release_step_1_fixed_item_close(self) -> None:
        step1 = self.skill("release").split("\n1. ")[1].split("\n2. ")[0]
        self.assertIn("ruled `fix #<n>`", step1)
        self.assertIn("close fixed by #<n>", step1)

    def test_release_notes_steps_use_output_file(self) -> None:
        text = self.skill("release")
        step6 = text.split("\n6. ")[1].split("\n7. ")[0]
        step9 = text.split("\n9. ")[1].split("\n10. ")[0]
        self.assertIn("release-notes --tag <tag> --output <file>", step6)
        self.assertIn("--output", step9)
        self.assertNotIn("`release-notes --tag <tag>`", step6 + step9)

    def test_release_worktree_setup_before_gate(self) -> None:
        text = self.skill("release")
        step4 = text.split("\n4. ")[1].split("\n5. ")[0]
        self.assertIn("dependency install", step4)
        self.assertIn("new worktree", step4)
        self.assertNotIn("npm ci", text)

    def test_release_notes_skill(self) -> None:
        text = self.skill("release-notes")
        for name in ["--all", "### Highlights", "### Fixes", "### Upgrade notes", "Full changes:",
                     "gh release edit", "confirm"]:
            self.assertIn(name, text, name)
        self.assertLess(text.index("confirm"), text.index("gh release edit"))
        self.assertIn("`cliff`: say the mode is `cliff`, change nothing, stop.", text)
        self.assertIn("Do not commit.", text)
        self.assertIn("`--all`: run steps 1 to 5", text)

    def test_skill_md_names_release(self) -> None:
        text = (self.ROOT / "skill" / "SKILL.md").read_text()
        self.assertRegex(text, r"/release\b(?!-)")
        self.assertRegex(text, r"\$release\b(?!-)")


class ReleaseTextTests(unittest.TestCase):
    """Oracle 58 and 59: the kit text and this repo's release steps."""
    ROOT = Path(__file__).resolve().parents[3]

    def read(self, rel: str) -> str:
        return (self.ROOT / rel).read_text()

    def test_skill_stages_name_the_audit(self) -> None:
        text = self.read("skill/SKILL.md")
        self.assertIn("(security, needs-validation, <slug>)", text)
        self.assertIn("/release-audit", text)
        self.assertIn("release-check", text)
        self.assertNotIn("Never merge a release PR or push a tag while one is open", text)
        grant = text.split("Stop and report only for:", 1)[1].split("\n", 1)[0]
        self.assertIn("a tag push", grant)
        self.assertIn("a release PR merge", grant)
        self.assertNotIn("Never push a tag", text)
        self.assertIn("Stage 7 release rule", text)

    def test_canary_sandbox_keeps_the_project_permissions_and_exact_uid(self) -> None:
        text = self.read("scripts/canary.sh")
        box = text.split("sandbox_canary() {", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn("--sandbox", box)
        self.assertIn("codex exec", box)
        exec_line = next(line for line in box.splitlines() if "codex exec" in line)
        self.assertIn("-c default_permissions=dev-docker", exec_line)
        self.assertNotIn("grep -Eqw 65534", box)
        self.assertIn("uid=${uid:-none}", box)

    def test_one_audit_one_fix_wave_section(self) -> None:
        text = self.read("reference/release.md")
        self.assertIn("\n## One audit, one fix wave\n", text)
        section = text.split("\n## One audit, one fix wave\n")[1].split("\n## ")[0]
        for name in ["one audit", "informational", "## Gardener", "release-record --fix-wave", "next release tag",
                     "release-plan", "stop agent mistakes", "not a wall"]:
            self.assertIn(name, section, name)

    def test_skill_release_rule(self) -> None:
        raw = (self.ROOT / "skill" / "SKILL.md").read_bytes()
        rule = next(line for line in raw.decode().splitlines() if line.startswith("Release rule."))
        self.assertIn("release-record --fix-wave", rule)
        self.assertIn("until a ruling under it says", rule)
        self.assertNotIn("until its line is removed", rule)
        self.assertLessEqual(len(raw), 40000)

    def test_repo_release_steps_are_plain_numbers(self) -> None:
        steps = self.read("reference/release.md")
        self.assertNotRegex(steps, r"\d[a-z]\.")
        self.assertRegex(steps, r"(?m)^6\. ")

    def test_routes_have_a_release_audit_section(self) -> None:
        for rel in ["skill/references/claude.md", "skill/references/codex.md"]:
            with self.subTest(rel=rel):
                text = self.read(rel)
                self.assertIn("## Release audit", text)
                section = text.split("## Release audit", 1)[1]
                self.assertIn("release-audit", section)
                self.assertIn("sandbox-run.py", section)

    def test_repo_release_steps_run_the_audit_before_the_tag(self) -> None:
        self.assertIn("`reference/release.md`", self.read("AGENTS.md"))
        steps = self.read("reference/release.md")
        self.assertIn("/release-audit", steps)
        self.assertLess(steps.index("release-check"), steps.index("git tag"))

    def test_kit_text_has_no_em_dash(self) -> None:
        for rel in ["skill/SKILL.md", "skill/references/claude.md", "skill/references/codex.md", "AGENTS.md",
                    "README.md", "scripts/canary.sh", "skills/release-audit/SKILL.md", "skills/release/SKILL.md",
                    "skills/release-notes/SKILL.md", "reference/kit.md",
                    "reference/release.md", "reference/upstream.md", *sorted(f"reference/cli/{p.name}" for p in (self.ROOT / "reference" / "cli").glob("*.md"))]:
            self.assertNotIn("\u2014", self.read(rel), rel)


class ReleaseWorkflowTests(unittest.TestCase):
    """Oracle 31 to 34: the workflows as text, the release config and scripts."""
    ROOT = Path(__file__).resolve().parents[3]
    RC = "contains(github.ref_name, '-rc.')"

    def workflow(self, name: str) -> str:
        return (self.ROOT / ".github" / "workflows" / name).read_text()

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
        changes = ci.split("  changes:\n", 1)[1].split("\n  test:\n", 1)[0]
        for needle in ("fetch-depth: 0", "path: .devloop-base", "skip=false",
                       ".devloop-base/.agents/skills/dev-loop/scripts/devloop.py ci-skip", "--github-output"):
            with self.subTest(needle=needle):
                self.assertIn(needle, changes)
        jobs = ci.split("\n  test:\n", 1)[1].split("\n  acceptance:\n")
        for job in jobs:
            self.assertIn("    needs: changes\n", job)
            self.assertIn("    if: ${{ !cancelled() && needs.changes.outputs.skip != 'true' }}\n", job)

    def test_a_retarget_runs_every_job_again(self) -> None:
        ci = self.workflow("ci.yml")
        self.assertIn("  pull_request:\n    types: [opened, synchronize, reopened, edited]\n", ci)
        self.assertNotIn("github.event.action", ci)
        for path in ("reference/kit.md", "skills/release/SKILL.md"):
            with self.subTest(path=path):
                self.assertIn("List `edited` in the `pull_request` types", (self.ROOT / path).read_text())

    def test_changes_job_skips_only_on_a_default_branch_base(self) -> None:
        sources = {"ci.yml": self.workflow("ci.yml"), "reference/kit.md": (self.ROOT / "reference/kit.md").read_text(),
                   "skills/release/SKILL.md": (self.ROOT / "skills/release/SKILL.md").read_text()}
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
        config = json.loads((self.ROOT / "dev-loop.json").read_text())
        self.assertEqual(config["release_notes"], "written")
        self.assertEqual(config["version_files"], ["package.json", "package-lock.json"])
        for key, value in (("release_prepare", "node bin/install.mjs upgrade"),
                           ("release_check", "scripts/release-check.sh"),
                           ("release_verify", "scripts/release-verify.sh")):
            with self.subTest(key=key):
                self.assertEqual(config["commands"][key], value)

    def test_release_check_script(self) -> None:
        text = (self.ROOT / "scripts" / "release-check.sh").read_text()
        self.assertIn("set -euo pipefail", text)
        self.assertIn("scripts/canary.sh", text)
        final = text.split("-rc.", 1)[1]
        self.assertIn('scripts/repo_rules.py promoted --tag "$RELEASE_TAG" --sha HEAD', final)

    def test_release_verify_cleans_up(self) -> None:
        script = self.ROOT / "scripts" / "release-verify.sh"
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
        script = self.ROOT / "scripts" / "release-verify.sh"
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
        test_job, acceptance_job = ci.split("  acceptance:\n", 1)
        for job, suite in ((test_job, "run: npm test"), (acceptance_job, "run: scripts/acceptance.sh")):
            self.assertIn("git-cliff-2.14.2-x86_64-unknown-linux-gnu.tar.gz", job)
            self.assertIn("24f397c733add5390fdceee3a2088588ab0d5f944ce00d34cb7029b888cf2db4  git-cliff.tar.gz", job)
            self.assertIn('echo "$HOME/.local/bin" >> "$GITHUB_PATH"', job)
            self.assertLess(job.index("git-cliff-2.14.2"), job.index(suite))

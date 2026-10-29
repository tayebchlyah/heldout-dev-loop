"""The git guard push rules of the Claude and Codex hooks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from support import CLAUDE_HOOKS, CODEX_ASSETS, GUARD_ASKS


class ClaudeGitGuardPushTests(unittest.TestCase):
    """A plain push of a feature branch runs; a push that rewrites or reaches main asks."""

    @classmethod
    def setUpClass(cls) -> None:
        # The three repos are made once per class; each test gets a fresh copy, so a test that edits one leaves no trace.
        cls.templates = tempfile.mkdtemp()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for name, steps in (("repo", (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"],
                                      ["tag", "v1.0"], ["branch", "topic/x"], ["checkout", "-q", "-b", "feat"])),
                            ("main", (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"])),
                            ("feature", (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]))):
            os.makedirs(os.path.join(cls.templates, name))
            for args in steps:
                subprocess.run(["git", "-C", os.path.join(cls.templates, name), *args], check=True, env=env)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.templates)

    def copy(self, name: str, target: str) -> None:
        # An fsmonitor daemon socket (core.fsmonitor in a user config) cannot be copied.
        shutil.copytree(os.path.join(self.templates, name), target, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("fsmonitor--daemon.ipc"))

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp()
        self.copy("repo", self.repo)
        self.branch = "feat"
        # A second repo on main. Its path sorts after self.repo, so a guard that checks
        # only the first possible repo checks the feature one.
        self.main_repo = self.repo + "-main"
        self.copy("main", self.main_repo)
        # A second feature repo, for a session on main whose line visits a feature repo.
        self.feature_repo = self.repo + "-feature"
        self.copy("feature", self.feature_repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.repo)
        shutil.rmtree(self.main_repo)
        shutil.rmtree(self.feature_repo)

    def on(self, branch: str) -> None:
        """Check out `branch` in self.repo, unless it is already the branch there."""
        if branch != self.branch:
            subprocess.run(["git", "-C", self.repo, "checkout", "-q", branch], check=True)
            self.branch = branch

    def decisions(self, commands: Sequence[str], branch: str = "feat", env: dict | None = None) -> list[tuple[str, str]]:
        """Each command with its decision. The hooks run at once: a guard only reads the repos."""
        self.on(branch)
        with ThreadPoolExecutor(8) as pool:
            return list(zip(commands, pool.map(lambda command: self.decision(command, branch, env), commands)))

    def decision(self, command: str, branch: str = "feat", env: dict | None = None) -> str:
        self.on(branch)
        result = subprocess.run(
            ["bash", str(CLAUDE_HOOKS / "git-guard.sh")],
            input=json.dumps({"cwd": self.repo, "tool_input": {"command": command}}),
            text=True, capture_output=True, check=False, env=env,
        )
        if result.returncode == 2:
            return "deny"
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout else "allow"

    def test_plain_feature_branch_push_runs(self) -> None:
        for command, got in self.decisions(("git push", "git push origin feat", "git push -u origin feat", "git push origin HEAD")):
            self.assertEqual(got, "allow", command)

    def test_lease_push_to_the_current_feature_branch_runs(self) -> None:
        rows = [(command, "allow") for command in (
            "git push --force-with-lease", "git push --force-with-lease --force-if-includes",
            "git push --force-with-lease --force-if-includes origin HEAD", "git push --force-with-lease origin topic/x",
            "git push --force-with-lease origin HEAD:topic/x", "git push --force-with-lease origin @",
            "git push origin topic/x --force-with-lease", "git push --force-with-lease origin HEAD")]
        rows += [(command, "ask") for command in (
            "git push --force-with-lease git@github.com:other/repo.git HEAD", "git push --force-with-lease upstream HEAD",
            "git push --force-with-lease origin HEAD topic/x", "git push --mirr origin", "git push --dele origin topic/x",
            "git push --force-w", "git push --force-with", "git push --tag", "git push --follow",
            "git push --force", "git push -f", "git push -uf origin topic/x", "git push --force-if-includes",
            "git push --force-with-lease=topic/x:abc origin topic/x", "git push --force-with-lease origin +topic/x",
            "git push --force-with-lease --tags", "git push --force-with-lease --delete origin topic/x",
            "git push --force-with-lease --all", "git push --force-with-lease origin main",
            "git push --force-with-lease origin HEAD:main", "git push --force-with-lease origin topic/other",
            "git push --force-with-lease origin HEAD:topic/other", "git rebase main && git push --force-with-lease")]
        commands = [command for command, _ in rows]
        for (command, got), (_, want) in zip(self.decisions(commands, "topic/x"), rows):
            self.assertEqual(got, want, command)
        for branch in ("feat", "main"):
            self.assertEqual(self.decision("git push --force-with-lease", branch), "ask", branch)

    def test_lease_push_config_that_maps_to_another_branch_asks(self) -> None:
        def config(*args: str) -> None:
            subprocess.run(["git", "-C", self.repo, "config", *args], check=True)

        lease = ("git push --force-with-lease", "git push --force-with-lease origin topic/x",
                 "git push --force-with-lease origin HEAD")
        for merge in ("refs/heads/main", "refs/heads/topic/y"):
            config("branch.topic/x.merge", merge)
            config("push.default", "upstream")
            for command, got in self.decisions(lease, "topic/x"):
                self.assertEqual(got, "ask", f"{merge} {command}")
        config("branch.topic/x.merge", "refs/heads/topic/x")
        self.assertEqual(self.decision("git push --force-with-lease", "topic/x"), "allow")
        config("--unset", "branch.topic/x.merge")
        config("remote.origin.push", "refs/heads/topic/x:refs/heads/main")
        self.assertEqual(self.decision("git push --force-with-lease origin topic/x", "topic/x"), "ask")
        config("--unset", "remote.origin.push")
        config("remote.upstream.url", "x")
        config("branch.topic/x.pushRemote", "upstream")
        self.assertEqual(self.decision("git push --force-with-lease origin HEAD", "topic/x"), "ask")
        self.assertEqual(self.decision("git push --force-with-lease upstream HEAD", "topic/x"), "allow")

    def test_rewriting_deleting_tag_or_main_push_asks(self) -> None:
        for command, got in self.decisions(("git push --force origin feat", "git push --force-with-lease origin feat",
                        "git push origin +feat", "git push origin :feat", "git push origin --delete feat",
                        "git push origin main", "git push origin feat:main", "git push origin v1.0",
                        "git push --tags", "git push --mirror", "git push --all")):
            self.assertEqual(got, "ask", command)

    def test_bare_push_from_main_asks(self) -> None:
        for command, got in self.decisions(("git push", "git push origin", "git push origin HEAD"), "main"):
            self.assertEqual(got, "ask", command)

    def test_quotes_short_flags_and_global_options_do_not_walk_past(self) -> None:
        for command, got in self.decisions(("git push -uf origin feat", "git push origin \"main\"", "git push origin 'main'",
                        "git push origin feat:\"main\"", "git -c a=b push origin feat",
                        "GIT_CONFIG_COUNT=1 git push origin feat", "git --no-pager push origin main",
                        "git push origin feat && git push origin main", "sh -c 'git push origin main'",
                        "git push origin 'main", "git push origin @:main")):
            self.assertEqual(got, "ask", command)

    def test_wrapped_expanded_fed_or_drifted_push_asks(self) -> None:
        for command, got in self.decisions(("if git push origin main; then :; fi", "timeout 9 git push origin main",
                        "echo main | xargs git push origin", "B=main; git push origin $B",
                        "git push origin $(echo main)", "git push origin $'\\x6dain'",
                        "git checkout main && git push", "git tag v2 && git push origin v2",
                        "git config remote.origin.push '+refs/heads/*:refs/heads/*' && git push",
                        "git -c alias.x='push -f' x", "ssh host 'git push origin main'")):
            self.assertEqual(got, "ask", command)

    def test_everyday_feature_flows_still_run(self) -> None:
        for command, got in self.decisions(("git checkout -b feat2 && git push -u origin feat2", "git status && git log --oneline -3",
                        "git commit -m 'fix login' && git push")):
            self.assertEqual(got, "allow", command)

    def test_a_cd_after_a_semicolon_or_an_assignment_moves_the_push(self) -> None:
        main = self.main_repo
        for command, got in self.decisions((f"cd {main}; git push", f"X=1 cd {main} && git push", f"cd {main} & git push")):
            self.assertEqual(got, "ask", command)
        self.assertEqual(self.decision(f"cd {main}; git status"), "allow")

    def test_a_push_from_a_folder_the_guard_cannot_follow_asks(self) -> None:
        # A cd $D the line does not set stays unknown. From a feature session the
        # unknown folder may be the main repo, so it does not fall back to the session start.
        main = self.main_repo
        for command, got in self.decisions(("cd $D && git push", f"cd {main} && cd - && git push",
                        f"pushd {main}; popd && git push")):
            self.assertEqual(got, "ask", command)

    def test_an_assignment_that_moves_a_cd_stops_it_from_narrowing(self) -> None:
        # CDPATH=<x> cd f lands in <x>/f, a main clone, not in the feature clone ./f.
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        with tempfile.TemporaryDirectory() as other:
            for repo, branch in ((os.path.join(self.repo, "f"), "feat"), (os.path.join(other, "f"), "main")):
                os.makedirs(repo)
                for args in (["init", "-q", "-b", branch], ["commit", "-q", "--allow-empty", "-m", "i"]):
                    subprocess.run(["git", "-C", repo, *args], check=True, env=env)
            self.assertEqual(self.decision("cd ./f && git push"), "allow")
            for command, got in self.decisions((f"CDPATH={other} cd f && git push", f"HOME={other} cd && git push",
                            f"OLDPWD={other}/f cd - && git push")):
                self.assertEqual(got, "ask", command)

    def test_a_cd_before_a_semicolon_keeps_the_old_folders(self) -> None:
        # cd /nope fails, so the cd after && never runs and the push runs on main.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            command = f"cd /nope && cd {feature}; git push"
            self.assertEqual(self.decision(command, "main"), "ask", command)

    def test_a_paren_that_closes_no_subshell_keeps_the_cd(self) -> None:
        # A case pattern or an escaped paren is a bare ")" to the parser.
        main = self.main_repo
        for command, got in self.decisions((f"(cd {main} && case x in x) git push;; esac)",
                        f"(cd {main}; case x in x) git push;; esac)",
                        f"(cd {main} && echo \\) && git push)")):
            self.assertEqual(got, "ask", command)

    def test_a_cd_inside_a_subshell_does_not_move_the_push(self) -> None:
        # The push runs in self.repo on main, not in the feature repo the subshell visits.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            for command, got in self.decisions((f"(cd {feature}; true); git push", f"echo $(cd {feature}; true); git push",
                            f"(cd {feature} && true); git push", f"echo $(cd {feature} && true) && git push",
                            "echo " + chr(96) + f"cd {feature} && true" + chr(96) + "; git push"), "main"):
                self.assertEqual(got, "ask", command)

    def test_a_paren_that_closes_no_subshell_stops_a_later_cd_from_narrowing(self) -> None:
        # The case pattern closes the subshell early to the parser, so the cd looks
        # like it runs outside, and the real ")" finds nothing open.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            command = f"(case x in x) cd {feature} && true;; esac); git push"
            self.assertEqual(self.decision(command, "main"), "ask", command)

    def test_a_quote_escape_comment_or_heredoc_stops_a_cd_from_narrowing(self) -> None:
        # Quoted or escaped punctuation, a comment and a heredoc body are not
        # operators or commands, so a cd next to them may not have run before the push.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            bt = chr(96)
            for command, got in self.decisions((f'echo ";" cd {feature}; git push', f"echo \\; cd {feature}; git push",
                            f"true # ; cd {feature}\ngit push", f"true # && cd {feature}\ngit push",
                            f"cat <<EOF\ncd {feature}\nEOF\ngit push", f"cat <<EOF\ncd {feature} && true\nEOF\ngit push",
                            f"cd {feature} extra; git push",
                            f"(echo \\) && cd {feature} && echo \\( && true); git push",
                            f"(echo ')' && cd {feature} && echo '(' && true); git push",
                            f"(echo \\) && cd {feature} && echo \\{bt} && true); git push",
                            f"(case x in x) cd {feature} && true;; esac; echo \\( ); git push",
                            f'echo "&&" cd {feature} && git push', f"echo '&&' cd {feature} && git push"), "main"):
                self.assertEqual(got, "ask", command)

    def test_a_cd_or_dash_c_the_push_may_not_follow_asks(self) -> None:
        # Converted from the vault cases: the session is on main, the line visits a
        # feature repo, and the push asks unless it surely runs in the feature repo.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            bt, rel = chr(96), "../" + os.path.basename(feature)
            for command, got in self.decisions((f"cd {feature}; git push", f"cd {rel}; git push", f"(cd {feature}); git push",
                            f"(cd {feature}) && git push",
                            f"cd {feature} && cd \"$OLDPWD\" && git push", f"cd {feature} && cd - && git push",
                            f"(echo \\) && cd {feature} && true); git push",
                            f"echo $(echo \\) && cd {feature} && true); git push",
                            f"echo {bt}cd {feature}; true{bt}; git push", "sudo -u git git push"), "main"):
                self.assertEqual(got, "ask", command)
            for command, got in self.decisions((f"cd {feature} && git push", f"cd {feature} && (git status) && git push",
                            f"D={feature}; cd $D && git push",
                            f"git -C {feature} push"), "main"):
                self.assertEqual(got, "allow", command)
        main = os.path.basename(self.main_repo)
        for command, got in self.decisions((f"cd {self.main_repo} && git push", f"git -C {self.main_repo} push",
                        f"# save\ngit -C {self.main_repo} push", f"git -C ../{main} push")):
            self.assertEqual(got, "ask", command)

    def test_a_tilde_expands_only_at_the_start_of_a_path(self) -> None:
        # The old vault sat under iCloud~md~obsidian: a ~ inside a path is no expansion.
        with tempfile.TemporaryDirectory() as home:
            tilde_main = os.path.join(home, "iCloud~md~obsidian", "main")
            os.makedirs(tilde_main)
            for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", tilde_main, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            os.symlink(self.repo, os.path.join(home, "feature"))
            env = {**os.environ, "HOME": home}
            for command, got in self.decisions((f'git -C "{tilde_main}" push', f'cd "{tilde_main}" && git push',
                            "cd ~/iCloud~md~obsidian/main && git push"), env=env):
                self.assertEqual(got, "ask", command)
            self.assertEqual(self.decision("cd ~/feature && git push", "main", env=env), "ask")
            self.assertEqual(self.decision("cd ~/feature && git push", env=env), "allow")

    def test_an_unparseable_line_asks_only_for_a_push(self) -> None:
        for command, got in self.decisions(("python3 - <<'EOF'\nprint(\"it's the git log\")\nEOF",
                        "python3 - <<'EOF'\nprint(\"it's the git log\")\nEOF\ngit push",
                        "git log --grep \"it's")):
            self.assertEqual(got, "allow", command)
        self.assertEqual(self.decision("git push origin feat \"it's"), "ask")

    def test_only_what_the_shell_runs_is_checked(self) -> None:
        # A heredoc body is text unless its line feeds it to a shell. A quoted string
        # is a command only in a segment that runs a shell.
        for command, got in self.decisions(("cat > notes.md <<'EOF'\ngit push origin main\nEOF",
                        "cat <<-EOF\n\tgit push origin main\n\tEOF\ngit status",
                        'echo "cd x && git push origin main"', "grep -n 'git push origin main' notes.md",
                        'run "cd x; git push origin main"')):
            self.assertEqual(got, "allow", command)
        for command, got in self.decisions(("bash <<'EOF'\ngit push origin main\nEOF", "ssh host <<'EOF'\ngit push origin main\nEOF",
                        "cat <<'EOF' | sh\ngit push origin main\nEOF", "cat > x <<'EOF'\nx\nEOF\ngit push origin main",
                        "eval 'git push origin main'", "su -c 'git push origin main'",
                        "cat <<'EOF'\ngit status\ngit push origin main")):
            self.assertEqual(got, "ask", command)

    def test_a_cd_into_a_variable_the_line_surely_set_is_followed(self) -> None:
        # The session is on main. The push runs in the feature repo only when the
        # variable surely holds its path.
        feature, main = self.feature_repo, self.main_repo
        parent, name = os.path.dirname(feature), os.path.basename(feature)
        for command, got in self.decisions((f'F={feature}; cd "$F" && git push', f"F={feature} && cd $F && git push",
                        f"export F={feature}; cd ${{F}} && git push", f'P={parent}; cd "$P/{name}" && git push',
                        f'F={feature}; git -C "$F" push', f"W={feature}\ngit add x && git commit -F - <<'EOF'\nmsg\nEOF\ncd $W && git push"), "main"):
            self.assertEqual(got, "allow", command)
        for command, got in self.decisions((f"true && F={feature}; cd $F && git push", f"true || F={feature}; cd $F && git push",
                        f"(F={feature}); cd $F && git push", f"sh -c 'F={feature}'; cd $F && git push",
                        f"F={feature}; F={main}; cd $F && git push", f"F={feature}; read F; cd $F && git push",
                        f"F={feature}; unset F; cd $F && git push", f"for F in {main}; do :; done; F={feature}; cd $F && git push",
                        f"F=../{name}; cd $F && git push", f"F={feature}$X; cd $F && git push",
                        f"F={feature}; F+=x; cd $F && git push", f"cd $F && F={feature}; git push", f"F={feature}; git commit -F x && cd $F && git push",
                        f"bash <<'EOF'\ncd {feature} && true\nEOF\ngit push"), "main"):
            self.assertEqual(got, "ask", command)

    def test_a_variable_holding_a_tilde_inside_its_path_is_followed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            feature = os.path.join(root, "iCloud~md~obsidian", "f")
            os.makedirs(feature)
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            self.assertEqual(self.decision(f'F="{feature}"; cd "$F" && git push', "main"), "allow")
            # An assignment expands a ~ after ":": F=/a:~+ is /a:$PWD, not the folder "/a:~+".
            colon = os.path.join(root, "p:~+")
            os.makedirs(colon)
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", colon, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            self.assertEqual(self.decision(f'F={colon}; cd "$F" && git push', "main"), "ask")

    def test_the_reset_and_rm_walls_skip_a_heredoc_body(self) -> None:
        reset, rm = "git re" + "set --hard", "rm -rf " + "/"
        for command, got in self.decisions((f"cat > n.md <<'EOF'\nnever {reset} or {rm}\nEOF", f"cat <<-EOF\n\t{reset}\n\tEOF")):
            self.assertEqual(got, "allow", command)
        for command, got in self.decisions((f"bash <<'EOF'\n{reset}\nEOF", f"cat > n.md <<'EOF'\nx\nEOF\n{reset}",
                        f"cat > n.md <<'EOF'\nx\nEOF\n{rm}", f"cat <<'EOF'\nno end line\n{reset}")):
            self.assertIn(got, ("deny", "ask"), command)

    def test_a_bare_push_from_an_unknown_repository_asks(self) -> None:
        # A folder made earlier in the line does not exist yet, so a named feature
        # branch still runs.
        self.assertEqual(self.decision(f"git -C {self.repo}/nope push"), "ask")
        self.assertEqual(self.decision(f"git -C {self.repo}/nope push origin feat"), "allow")

    def test_the_regression_commands_ask_only_for_a_real_push(self) -> None:
        # The 53 commands the 1.4 guard asked on (permission prompt analysis,
        # 2026-10-13). One is a real push to main.
        cases = json.loads(GUARD_ASKS.read_text())
        for case, (_, got) in zip(cases, self.decisions([case["cmd"] for case in cases])):
            expected = "ask" if case["real_push"] else "allow"
            self.assertEqual(got, expected, case["cmd"][:300])

    def test_remote_config_that_reaches_other_branches_asks(self) -> None:
        subprocess.run(["git", "-C", self.repo, "config", "remote.origin.push", "+refs/heads/*:refs/heads/*"], check=True)
        self.assertEqual(self.decision("git push"), "ask")
        self.assertEqual(self.decision("git push origin feat"), "ask")

    def test_a_push_alias_asks(self) -> None:
        subprocess.run(["git", "-C", self.repo, "config", "alias.p", "push --force"], check=True)
        self.assertEqual(self.decision("git p"), "ask")
        self.assertEqual(self.decision("git commit -m 'push fix'"), "allow")


    def test_a_push_alias_in_any_possible_repo_asks(self) -> None:
        # After cd <main>; the line may be in either repo, and only main has the alias.
        subprocess.run(["git", "-C", self.main_repo, "config", "alias.p", "push"], check=True)
        self.assertEqual(self.decision(f"cd {self.main_repo}; git p"), "ask")

    def test_a_cd_that_may_be_skipped_does_not_narrow(self) -> None:
        # true || cd F && git push is (true || cd F) && git push: the push runs
        # without the cd. Only a cd after the line start, && or ; surely ran.
        feature = self.feature_repo
        for command, got in self.decisions((f"true || cd {feature} && git push", f"false | cd {feature} && git push",
                        f"sleep 0 & cd {feature} && git push"), "main"):
            self.assertEqual(got, "ask", command)
        for command, got in self.decisions((f"cd {feature} && git push", f"true; cd {feature} && git push",
                        f"true && cd {feature} && git push"), "main"):
            self.assertEqual(got, "allow", command)

    def test_a_cd_behind_a_wrapper_is_unknown(self) -> None:
        # The guard does not follow a cd that is not the first word, so it cannot
        # say where the push runs.
        main = self.main_repo
        for command, got in self.decisions((f"builtin cd {main} && git push", f"command cd {main} && git push",
                        f"time cd {main} && git push", f"eval cd {main} && git push",
                        f"if cd {main}; then git push; fi", f"{{ cd {main}; }} && git push",
                        f"! cd {main} && git push", f"exec cd {main} && git push")):
            self.assertEqual(got, "ask", command)

    def test_a_cd_with_an_option_or_a_stack_move_is_unknown(self) -> None:
        # cd -P M lands in M, not in ./-P. pushd +1 and a bare pushd move to a folder
        # from the directory stack.
        main, feature = self.main_repo, self.feature_repo
        for command, got in self.decisions((f"cd -P {main} && git push origin v1", f"cd -- {main} && git push origin v1",
                        f"cd -L {main} && git push origin v1", f"pushd -n {main} && git push origin v1",
                        f"pushd {main} && pushd {feature} && pushd +1 && git push origin v1",
                        f"pushd {main} && pushd {feature} && pushd && git push origin v1")):
            self.assertEqual(got, "ask", command)

    def test_a_relative_cd_does_not_narrow(self) -> None:
        # CDPATH, set earlier in the line or in the user's shell profile, sends cd f
        # to <x>/f, a main clone, not to the feature clone ./f.
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        with tempfile.TemporaryDirectory() as other:
            for repo, branch in ((os.path.join(self.repo, "f"), "feat"), (os.path.join(other, "f"), "main")):
                os.makedirs(repo)
                for args in (["init", "-q", "-b", branch], ["commit", "-q", "--allow-empty", "-m", "i"]):
                    subprocess.run(["git", "-C", repo, *args], check=True, env=env)
            for command, got in self.decisions((f"CDPATH={other}; cd f && git push", f"export CDPATH={other}; cd f && git push",
                            "cd f && git push", "pushd f && git push")):
                self.assertEqual(got, "ask", command)
            for command, got in self.decisions(("cd ./f && git push", "cd ../" + os.path.basename(self.repo) + " && git push")):
                self.assertEqual(got, "allow", command)

    def test_a_home_set_in_the_line_makes_a_home_cd_unknown(self) -> None:
        # The hook's HOME is the feature repo, the line's HOME the main repo.
        env, main = {**os.environ, "HOME": self.repo}, self.main_repo
        for command, got in self.decisions((f"HOME={main}; cd && git push origin v1", f"export HOME={main}; cd ~ && git push",
                        f"export HOME={main}; cd ~/. && git push"), env=env):
            self.assertEqual(got, "ask", command)
        self.assertEqual(self.decision("cd ~ && git push", env=env), "allow")

    def test_a_push_with_git_dir_or_work_tree_asks(self) -> None:
        main = self.main_repo
        for command, got in self.decisions((f"git --git-dir={main}/.git push", f"git --git-dir {main}/.git push",
                        f"git --work-tree={main} push", f"git --work-tree {main} push")):
            self.assertEqual(got, "ask", command)

    def test_a_git_behind_a_chdir_wrapper_asks(self) -> None:
        main = self.main_repo
        for command, got in self.decisions((f"env -C {main} git push", f"env --chdir={main} git push", f"env --chdir {main} git push")):
            self.assertEqual(got, "ask", command)

    def test_an_alias_from_a_folder_the_guard_cannot_follow_asks(self) -> None:
        # Only the main repo holds the alias, and the guard cannot see the line is there.
        main = self.main_repo
        subprocess.run(["git", "-C", main, "config", "alias.p", "push"], check=True)
        for command, got in self.decisions(("cd $D && git p", f"git --git-dir={main}/.git p", f"env -C {main} git p")):
            self.assertEqual(got, "ask", command)
        self.assertEqual(self.decision("cd $D && git status"), "allow")

    def test_send_pack_and_subtree_push_ask(self) -> None:
        for command, got in self.decisions(("git send-pack origin feat", "git send-pack --all origin",
                        "git subtree push --prefix=lib origin feat", "git subtree -P lib push origin feat",
                        "git subtree --prefix lib -q push origin feat")):
            self.assertEqual(got, "ask", command)
        self.assertEqual(self.decision("git subtree split -P lib"), "allow")


class CodexGitGuardPushTests(ClaudeGitGuardPushTests):
    """The Codex guard holds the same push rule. Codex cannot ask, so every ask is a deny."""

    def decision(self, command: str, branch: str = "feat", env: dict | None = None) -> str:
        self.on(branch)
        result = subprocess.run(
            ["sh", str(CODEX_ASSETS / "hooks" / "git-guard.sh")],
            input=json.dumps({"cwd": self.repo, "tool_input": {"command": command}}),
            text=True, capture_output=True, check=False, env=env,
        )
        decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout else "allow"
        return "ask" if decision == "deny" else decision

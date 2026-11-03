"""The walls on absolute paths, kit files and `cd`, the canary script and the Codex trust warning."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from support import check_setup, main_of
from test_devloop import git, put
from test_seal_hooks import ROOT, SHELL, patch


class AbsolutePathWallTests(unittest.TestCase):
    """Walled paths match relative to the repo: a repo under a folder named src is not walled whole."""

    def test_absolute_paths_in_a_repo_under_a_source_folder_name(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "src" / "repo"
        put(repo / "README.md")
        git(repo, "init", "-q")
        tops = {str(repo), str(repo.resolve())}  # /var/... and /private/var/... on macOS
        claude = ROOT / "claude/hooks/deny-acceptance-to-subagents.sh"
        codex = ROOT / "codex/hooks/acceptance-wall.sh"

        def patch(header: str) -> str:
            return "\n".join(["*** Begin Patch", header, "+x", "*** End Patch"])

        rows = []
        for top in sorted(tops):
            rows += [
                (claude, "spec-tester", "Read", {"file_path": f"{top}/tests/acceptance/x.py"}, 0),
                (claude, "spec-tester", "Read", {"file_path": f"{top}/src/y.py"}, 2),
                (claude, "spec-tester", "Bash", {"command": f"cat {top}/README.md"}, 0),
                (claude, "spec-tester", "Bash", {"command": f"git -C {top} log"}, 0),
                (claude, "implementer", "Read", {"file_path": f"{top}/tests/acceptance/x.py"}, 2),
                (codex, "implementer", "Bash", {"command": f"cat -n{top}/tests/acceptance/x.py"}, 2),
                (codex, "implementer", "Bash", {"command": f"cat -n{top}/README.md"}, 0),
            ]
            for script, agent in [(claude, "spec-tester"), (codex, "spec_tester")]:
                rows += [(script, agent, "Bash", {"command": command}, code) for command, code in [
                    (f"gcc -I{top}/src x.c", 2), (f"ls -d{top}/src", 2), (f"cat {top}2/src/y.py", 2),
                    (f"cat {top}/../repo/src/y.py", 2), (f"cat {top}/docs/../src/y.py", 2),
                    (f"cat {top}/README.md", 0)]]
            rows += [
                (codex, "spec_tester", "Bash", {"command": f"cat {top}/tests/acceptance/x.py"}, 0),
                (codex, "spec_tester", "Bash", {"command": f"cat {top}/src/y.py"}, 2),
                (codex, "spec_tester", "Bash", {"command": f"cat {top}/README.md"}, 0),
                (codex, "spec_tester", "apply_patch", {"command": patch(f"*** Add File: {top}/tests/acceptance/x.py")}, 0),
                (codex, "spec_tester", "apply_patch", {"command": patch(f"*** Update File: {top}/src/y.py")}, 2),
                (codex, "implementer", "Bash", {"command": f"cat {top}/tests/acceptance/x.py"}, 2),
                (codex, "implementer", "apply_patch",
                 {"command": patch(f"*** Update File: {top}/tests/acceptance/x.py")}, 2),
                (codex, "implementer", "apply_patch", {"command": patch(f"*** Update File: {top}/README.md")}, 0),
            ]
        for script, agent, tool, tool_input, code in rows:
            with self.subTest(script=script.name, agent=agent, tool=tool, tool_input=tool_input):
                payload = {"agent_id": "b", "agent_type": agent, "cwd": str(repo), "tool_name": tool,
                           "tool_input": tool_input}
                result = subprocess.run([SHELL[script.parent.parent.name], str(script)], input=json.dumps(payload),
                                        capture_output=True, text=True, timeout=60,
                                        env={**os.environ, "KIT_MAIN": main_of(payload["cwd"])})
                self.assertEqual(result.returncode, code, result.stderr)


class CanaryScriptTests(unittest.TestCase):
    """Static only: the live run needs both harnesses and a login, so the controller runs it by hand."""
    script = ROOT / "scripts" / "canary.sh"

    def test_script_is_executable_and_parses(self):
        self.assertTrue(os.access(self.script, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", str(self.script)]).returncode, 0)

    def test_help_prints_usage_without_a_harness(self):
        bare = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, bare, True)
        for name in ("dirname", "cat"):  # the script may need these, never claude or codex
            os.symlink(shutil.which(name), os.path.join(bare, name))
        result = subprocess.run([shutil.which("bash"), str(self.script), "--help"], capture_output=True, text=True,
                                env={"PATH": bare})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)

    def test_each_route_has_its_own_token_under_system_bash(self):
        # /bin/bash is 3.2 on macOS: no associative arrays, or every route would share one token.
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        result = subprocess.run(["/bin/bash", str(self.script), "--self-test-tokens", f"{work}/repo"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line.split() for line in result.stdout.splitlines()]
        self.assertEqual(len({tok for _, _, tok in lines}), len(lines))
        self.assertGreaterEqual(len(lines), 14)
        for kind, route, token in lines:
            path = Path(work, "repo", "tests/acceptance" if kind == "acc" else "src",
                        ("test_canary_" if kind == "acc" else "canary_") + route.replace("-", "_") + ".py")
            self.assertIn(token, path.read_text())

    def test_scratch_repo_init_succeeds(self):
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        result = subprocess.run(["/bin/bash", str(self.script), "--self-test-init", f"{work}/repo"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(work, "repo", "dev-loop.json").is_file())
        self.assertEqual(result.stderr, "")

    def test_a_read_is_detected_when_the_agent_run_is_tainted(self):
        # An audit record keeps at most 3 lines, so one record may hold another route's token, or none.
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        transcripts, empty, denied, quoted = work / "t", work / "empty", work / "denied", work / "quoted"
        empty.mkdir()
        put(transcripts / "agent.jsonl", '{"out": "TOKREAD line 1"}\n{"out": "TOKOTHER line 1"}\n')
        put(denied / "agent.jsonl", '{"out": "Blocked: this agent cannot access tests/acceptance"}\n')
        put(quoted / "agent.jsonl", '{"out": "SEARCH_BLOCK_MESSAGE = (f\\"Blocked: this search reaches the held-out suite '
                                    '{ACCEPTANCE_DIR}"}\n')
        tainted, foreign, absent = work / "taint.json", work / "foreign.json", work / "absent.json"
        tainted.write_text(json.dumps({"records": [{"agent_type": "spec-tester", "set": "source",
                                                    "lines": ["TOKOTHER line 1"]}]}))
        foreign.write_text(json.dumps({"records": [{"agent_type": "implementer", "set": "acceptance",
                                                    "lines": ["TOKREAD line 1"]}]}))
        claude, codex, quoting = work / "claude", work / "codex", work / "quoting"

        def lines(path, *objs):
            put(path / "agent.jsonl", "".join(json.dumps(o) + "\n" for o in objs))

        lines(claude,
              {"message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                        "input": {"command": "cat src/canary_cat.py"}}]}},
              {"message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content":
                                        "PreToolUse:Bash hook error: Blocked: src/ is outside this agent's territory."}]}},
              {"message": {"content": [{"type": "tool_use", "id": "t2", "name": "Grep",
                                        "input": {"pattern": ".", "path": "src/canary_Grep.py"}}]}},
              {"message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content":
                                        "<tool_use_error>Error: No such tool available: Grep.</tool_use_error>"}]}})
        lines(codex,
              {"payload": {"type": "custom_tool_call", "call_id": "c1", "name": "exec",
                           "input": "cat tests/acceptance/test_canary_cat.py"}},
              {"payload": {"type": "custom_tool_call_output", "call_id": "c1", "output":
                           "Blocked: this agent cannot access tests/acceptance. Command: cat x"}})
        lines(quoting,
              {"payload": {"call_id": "q1", "arguments": "cat .claude/hooks/deny-path.sh"}},
              {"payload": {"call_id": "q1", "output": 'echo "Blocked: $FRAG is outside this agent\'s territory."\n'
                                                      'Blocked: this agent cannot access $denied'}},
              {"payload": {"call_id": "q2", "arguments": "cat src/canary_git_show.py > /dev/null"}},
              {"payload": {"call_id": "q2", "output": ""}})
        for where, taint, token, stem, verdict in [
            (transcripts, tainted, "TOKREAD", "canary_x", "read-detected"),
            (transcripts, tainted, "TOKOTHER", "canary_x", "read-detected"),
            (transcripts, foreign, "TOKREAD", "canary_x", "LEAK"),
            (transcripts, absent, "TOKREAD", "canary_x", "LEAK"),
            (denied, tainted, "TOKNEVER", "canary_x", "not-run"),
            (transcripts, tainted, "TOKNEVER", "canary_x", "not-run"),
            (quoted, tainted, "TOKNEVER", "canary_x", "not-run"),
            (empty, tainted, "TOKREAD", "canary_x", "ERROR"),
            (claude, tainted, "TOKNEVER", "canary_cat", "blocked"),
            (claude, tainted, "TOKNEVER", "canary_python", "skipped"),
            (claude, tainted, "TOKNEVER", "canary_Grep", "absent"),
            (codex, tainted, "TOKNEVER", "test_canary_cat", "blocked"),
            (quoting, tainted, "TOKNEVER", "canary_git_show", "unproven"),
            (quoting, tainted, "TOKNEVER", "canary_cat", "not-run"),
        ]:
            with self.subTest(where=where.name, taint=taint.name, token=token, stem=stem):
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-verdict", str(where), str(taint),
                                         "spec-tester", token, stem], capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout), (0, verdict + "\n"), result.stderr)

    def test_codex_runs_the_hooks_of_its_own_scratch_install(self):
        text = self.script.read_text()
        runs = [line for line in text.splitlines() if line.strip().startswith("codex exec")]
        self.assertGreaterEqual(len(runs), 2)
        for line in runs:
            self.assertIn("--dangerously-bypass-hook-trust", line)
        self.assertNotIn('-c "projects.', text)
        self.assertIn("never redirect or discard", text)
        self.assertIn("A route reported without running it is a failure", text)
        self.assertEqual(text.count('[ "$v" = not-run ] && [ "$h" = codex ] && [ "$agent" = spec_tester ] && v=refused'), 1)
        self.assertIn('[ "$4" = not-run ]', text.split("emit() {", 1)[1].split("\n", 1)[0])

    def test_trust_entry_is_added_for_the_run_and_removed_after(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        project = work / "sandbox \"q\" dir"
        for before in ['model = "x"\n\n[projects."/a"]\ntrust_level = "trusted"\n', 'model = "x"', ""]:
            with self.subTest(before=before):
                config = work / "config.toml"
                config.write_text(before)
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-trust", str(config), str(project)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                during = tomllib.loads(result.stdout)
                self.assertEqual(during["projects"][str(project)], {"trust_level": "trusted"})
                self.assertEqual(config.read_text(), before)
                self.assertEqual(sorted(p.name for p in work.iterdir()), ["config.toml"])

    def test_trust_add_refuses_to_run_over_an_earlier_backup(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        config, backup = work / "config.toml", work / "config.toml.canary-bak"
        config.write_text('model = "x"\n')
        backup.write_text('model = "old"\n')
        result = subprocess.run(["/bin/bash", str(self.script), "--self-test-trust", str(config), str(work / "p")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("canary: an earlier run left", result.stderr)
        self.assertEqual((config.read_text(), backup.read_text()), ('model = "x"\n', 'model = "old"\n'))

    def test_forget_drops_the_trust_codex_wrote_under_the_scratch_folder_only(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        entry = '\n[projects."%s"]\ntrust_level = "trusted"\n'
        config = work / "config.toml"
        for prefix, kept in [("/s/tmp.A", ["/s/tmp.AB", "/q"]), ("/none", ["/s/tmp.A", "/s/tmp.A/codex", "/s/tmp.AB", "/q"])]:
            with self.subTest(prefix=prefix):
                config.write_text('model = "x"\n' + "".join(entry % p for p in ["/s/tmp.A", "/s/tmp.A/codex", "/s/tmp.AB", "/q"])
                                  + "\n[hooks.state]\n")
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-forget", str(config), prefix],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                after = tomllib.loads(config.read_text())
                self.assertEqual(sorted(after["projects"]), sorted(kept))
                self.assertEqual(after["model"], "x")
                self.assertIn("hooks", after)

    def test_forget_edits_by_line_and_keeps_what_it_cannot_decode(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        head = '[projects."/s/a"]\ntrust_level = "trusted"\n'
        for name, before, after in [
            ("first", head + '\nmodel = "x"\n', 'model = "x"\n'),
            ("last-no-newline", 'model = "x"\n\n' + head.rstrip("\n"), 'model = "x"'),
            ("adjacent", 'm = 1\n\n' + head + '[projects."/s/b"]\ntrust_level = "trusted"\n[o]\n', 'm = 1\n[o]\n'),
            ("kept-key", head + '[projects."\\U0001F600"]\ntrust_level = "trusted"\n',
             '[projects."\\U0001F600"]\ntrust_level = "trusted"\n'),
            ("other-lines", head.replace("trusted", "untrusted") + '[projects."/s/b"]\ntrust_level = "trusted"\nx = 1\n', None),
        ]:
            with self.subTest(name=name):
                config = work / "config.toml"
                config.write_text(before)
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-forget", str(config), "/s"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(config.read_text(), before if after is None else after)
        missing = work / "none.toml"
        result = subprocess.run(["/bin/bash", str(self.script), "--self-test-forget", str(missing), "/s"],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, missing.exists()), (0, False), result.stderr)

    def test_codex_older_than_0_165_is_skipped_with_a_failure(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        put(work / "codex", "#!/bin/sh\necho codex-cli 0.164.0\n")
        (work / "codex").chmod(0o755)
        result = subprocess.run(["/bin/bash", str(self.script), "--codex"], capture_output=True, text=True,
                                env={**os.environ, "PATH": f"{work}:{os.environ['PATH']}"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("codex-cli 0.164.0 is older than 0.165.0: the canary needs --dangerously-bypass-hook-trust", result.stderr)
        self.assertNotIn("codex implementer", result.stdout)

    def test_second_review_canary_shows_a_reviewer_that_quotes_a_canary(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        table = "printf '| file | line | finding | class | verdict |\\n| --- | --- | --- | --- | --- |\\n'"
        quote = "for f in $(grep -o '/[^ ,]*canary[^ ,]*' .second-review.diff | sort -u); do cat \"$f\"; done"
        for name, body, line, code in [
            ("clean", table, "second-review codex done canary=no", 0),
            ("quotes", quote, "second-review codex tainted canary=yes", 1),
        ]:
            with self.subTest(name=name):
                bins = work / name / "bin"
                put(bins / "codex", "#!/bin/sh\nout=\nwhile [ $# -gt 0 ]; do [ \"$1\" = -o ] && out=$2; shift; done\n"
                                    f"cat >/dev/null\n{{ {body}; }} >\"$out\"\n")
                (bins / "codex").chmod(0o755)
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-review", str(work / name / "repo"), "codex"],
                                        capture_output=True, text=True,
                                        env={**os.environ, "PATH": f"{bins}:{os.environ['PATH']}"})
                self.assertEqual((result.returncode, result.stdout.strip().splitlines()[-1:]), (code, [line]), result.stderr)

    def test_second_review_state_maps_to_its_verdict_row(self):
        for name, js, line, code in [
            ("done", '{"state": "done", "record": null}', "second-review codex done canary=no", 0),
            ("tainted", '{"state": "tainted", "record": null}', "second-review codex tainted canary=yes", 1),
            ("shown", '{"state": "done", "record": null, "text": "CANARYacc"}', "second-review codex done canary=yes", 1),
            ("skipped", '{"state": "skipped", "record": null}', "second-review codex skipped canary=no", 1),
            ("failed", '{"state": "failed", "record": null}', "second-review codex failed canary=no", 1),
            ("no-json", "", "second-review codex ERROR canary=no", 1),
            ("garbage", "not json", "second-review codex ERROR canary=no", 1),
        ]:
            with self.subTest(name=name):
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-review-verdict", "codex", js,
                                         "CANARYacc", "CANARYout"], capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout.strip()), (code, line), result.stderr)

    def test_network_verdict_reads_the_probe_file(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        ok = "engine rc=1 permission denied while trying to connect\ndocker rc=125\nloopback 200\ngit rc=0\n"
        for name, probe, reply, docker, lines, code in [
            ("refused", ok, "", "probe", ["docker refused", "loopback ok", "git ok"], 0),
            ("marker", "engine rc=1 permission denied\ndocker rc=0\nCANARYNET1\nloopback 200\ngit rc=0\n", "", "probe", ["docker LEAK", "loopback ok", "git ok"], 1),
            ("marker-in-reply", ok, "the file says CANARYNET1", "probe", ["docker LEAK", "loopback ok", "git ok"], 1),
            ("engine-reached", "engine rc=1 permission denied\ndocker rc=0\nloopback 200\ngit rc=0\n", "", "probe", ["docker LEAK", "loopback ok", "git ok"], 1),
            ("engine-answers-container-fails", "engine rc=0 \ndocker rc=1\nloopback 200\ngit rc=0\n", "", "probe",
             ["docker LEAK", "loopback ok", "git ok"], 1),
            ("mounts-denied", "engine rc=0\ndocker rc=125\nloopback 200\ngit rc=0\n", "", "probe",
             ["docker LEAK", "loopback ok", "git ok"], 1),
            ("cli-error-no-refusal-text", "engine rc=1 unknown flag: -H\ndocker rc=125\nloopback 200\ngit rc=0\n", "", "probe",
             ["docker ERROR", "loopback ok", "git ok"], 1),
            ("cannot-connect", "engine rc=1 Cannot connect to the Docker daemon\ndocker rc=125\nloopback 200\ngit rc=0\n", "", "probe",
             ["docker refused", "loopback ok", "git ok"], 0),
            ("operation-not-permitted", "engine rc=1 dial unix: connect: operation not permitted\ndocker rc=125\nloopback 200\ngit rc=0\n", "", "probe",
             ["docker refused", "loopback ok", "git ok"], 0),
            ("skipped", "loopback 200\ngit rc=0\n", "", "skipped: no engine answers at unix:///x",
             ["docker skipped: no engine answers at unix:///x", "loopback ok", "git ok"], 0),
            ("no-loopback-no-git", "engine rc=1 permission denied\ndocker rc=125\nloopback 000\ngit rc=128\n", "", "probe",
             ["docker refused", "loopback ERROR", "git ERROR"], 1),
            ("no-probe-file", None, "done", "probe", ["docker ERROR", "loopback ERROR", "git ERROR"], 1),
        ]:
            with self.subTest(name=name):
                probe_file, reply_file = work / f"{name}.probe", work / f"{name}.reply"
                if probe is not None:
                    probe_file.write_text(probe)
                reply_file.write_text(reply)
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-network-verdict", str(probe_file),
                                         str(reply_file), "CANARYNET1", docker], capture_output=True, text=True)
                expected = "\n".join(f"codex network {line}" for line in lines)
                self.assertEqual((result.returncode, result.stdout.strip()), (code, expected), result.stderr)

    def test_codex_network_check_runs_after_the_sandbox_canary(self):
        text = self.script.read_text()
        loop = text.split('for h in "${want[@]}"; do', 1)[1]
        self.assertIn('[ "$h" = codex ] && network_canary', loop)
        self.assertLess(loop.index("sandbox_canary"), loop.index("network_canary"))
        body = text.split("network_canary() {", 1)[1].split("\n}\n", 1)[0]
        for needle in ["docker context inspect", "unix://", "docker -H", "cat /w/.env", "http.server", "127.0.0.1",
                       "git ls-remote https://github.com/", "version", "engine rc="]:
            self.assertIn(needle, body)
        self.assertEqual(text.count("sha256:bdf57e528e45"), 1)
        self.assertNotIn("default_permissions=dev-docker", body)
        self.assertNotIn("--sandbox", body)

    def test_main_session_denial_text_of_either_wall_reads_as_blocked(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        for name, text, verdict in [
            ("claude", "Blocked: held-out suite. Only spec-tester writes tests/acceptance. The main session may read it.", "blocked"),
            ("claude-path", "Blocked: tests/acceptance is outside this agent's territory.", "blocked"),
            ("codex", "Blocked: this agent cannot access tests/acceptance", "blocked"),
            ("none", "edit applied", "ERROR"),
        ]:
            with self.subTest(name=name):
                put(work / name / "main.jsonl", json.dumps({"out": text}) + "\n")
                result = subprocess.run(["/bin/bash", str(self.script), "--self-test-main", str(work / name)],
                                        capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout), (0, verdict + "\n"), result.stderr)

    def test_unknown_flag_is_a_usage_error(self):
        result = subprocess.run(["bash", str(self.script), "--nope"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


KIT_AGENTS = sorted([(".claude/agents", p.name) for p in (ROOT / "claude/agents").glob("*.md")]
                    + [(".codex/agents", p.name) for p in (ROOT / "codex/agents").glob("*.toml")])


class KitWallTests(unittest.TestCase):
    """No caller edits an installed kit file through a file tool: kit files change only by `upgrade`."""

    def run_hook(self, script: str, payload: dict) -> subprocess.CompletedProcess[str]:
        harness = "codex" if script == "acceptance-wall.sh" else "claude"
        return subprocess.run([SHELL[harness], str(ROOT / harness / "hooks" / script)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60, env={**os.environ, "KIT_MAIN": main_of(payload.get("cwd"))})

    def test_file_tools_cannot_change_a_kit_file(self) -> None:
        kit = [".agents/skills/dev-loop/scripts/seal/check.py", ".agents/skills/dev-loop/SKILL.md",
               ".dev-loop/hooks/claude/git-guard.sh", ".dev-loop/MANIFEST.json", ".claude/skills/dev-loop/SKILL.md",
               ".codex/rules/dev-loop.rules", ".agents/skills/continue/SKILL.md", ".agents/skills/pause/agents/openai.yaml",
               ".claude/skills/pause/SKILL.md", ".claude/skills/continue", ".agents/skills/release-audit/SKILL.md",
               ".agents/skills/garden/SKILL.md", ".agents/skills/release/SKILL.md", ".agents/skills/release-notes/x.md",
               ".claude/skills/release/SKILL.md", *[f"{folder}/{name}" for folder, name in KIT_AGENTS]]
        open_paths = ["skill/scripts/seal/check.py", "claude/hooks/git-guard.sh", ".claude/agents/my-reviewer.md",
                      ".codex/agents/mine.toml", ".codex/rules/mine.rules", "docs/dev-loop/x.md",
                      ".agents/skills/verify-shop/SKILL.md", ".claude/skills/pauses/SKILL.md", "skills/pause/SKILL.md",
                      "dev-loop.json", "../dev-loop-worktrees/x/README.md", ".agents/skills/releases/SKILL.md",
                      ".agents/skills/release-plan/x.md", ".agents/skills/gardener/x.md",
                      ".claude/skills/release-notesx/SKILL.md", "skills/release/SKILL.md"]
        callers = [{}, {"agent_id": "a", "agent_type": "implementer"}]
        rows = []
        for path, deny in [(p, True) for p in kit] + [(p, False) for p in open_paths]:
            for caller in callers + [{"agent_id": "b", "agent_type": "spec-tester"}]:
                rows += [("deny-acceptance-to-subagents.sh", caller, "Edit", {"file_path": f"/repo/{path}"}, deny),
                         ("deny-acceptance-to-subagents.sh", caller, "Write", {"file_path": path}, deny)]
            for caller in callers + [{"agent_id": "b", "agent_type": "spec_tester"}]:
                rows += [("acceptance-wall.sh", caller, "apply_patch",
                          {"command": patch(f"*** Update File: {path}")}, deny)]
        rows += [
            ("deny-acceptance-to-subagents.sh", {}, "NotebookEdit",
             {"notebook_path": "/repo/.agents/skills/dev-loop/x.ipynb"}, True),
            ("deny-acceptance-to-subagents.sh", {}, "Edit", {"file_path": "/repo/.Dev-Loop//hooks/./claude/gate.sh"}, True),
            ("deny-acceptance-to-subagents.sh", {}, "Write",
             {"file_path": "/repo/docs/x.md", "content": "edit .dev-loop/hooks/claude/gate.sh"}, False),
            ("deny-acceptance-to-subagents.sh", {}, "Read", {"file_path": "/repo/.dev-loop/MANIFEST.json"}, False),
            ("deny-acceptance-to-subagents.sh", {"agent_id": "a", "agent_type": "implementer"}, "Read",
             {"file_path": "/repo/.agents/skills/dev-loop/SKILL.md"}, False),
            ("acceptance-wall.sh", {}, "apply_patch",
             {"command": patch("*** Update File: src/y.py", "*** Move to: .dev-loop/hooks/codex/gate.sh")}, True),
            ("acceptance-wall.sh", {}, "apply_patch",
             {"command": patch("*** Add File: docs/y.md", body="+see .dev-loop/hooks/codex/gate.sh")}, False),
            ("acceptance-wall.sh", {}, "Bash", {"command": "cat .dev-loop/hooks/codex/gate.sh"}, False),
            ("deny-acceptance-to-subagents.sh", {}, "Write", {"file_path": "../other/.dev-loop/x"}, False),
            ("acceptance-wall.sh", {}, "apply_patch", {"command": patch("*** Update File: .codex/rules/dev-loop.rules\r")}, True),
            ("acceptance-wall.sh", {}, "apply_patch", {"command": patch("*** Update File: .codex/rules/dev-loop.rules ")}, True),
        ]
        for caller in callers:
            rows += [("deny-acceptance-to-subagents.sh", caller, "Write", {"file_path": "/home/u/.claude/agents/implementer.md"}, False),
                     ("acceptance-wall.sh", caller, "apply_patch",
                      {"command": patch("*** Update File: /home/u/.codex/agents/implementer.toml")}, False)]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        git(repo, "init", "-q")
        (repo / "tests").mkdir()
        rows = [(*row, "/repo") for row in rows] + [
            ("deny-acceptance-to-subagents.sh", {}, "Write", {"file_path": "../.dev-loop/hooks/x.sh"}, True, str(repo / "tests")),
            ("acceptance-wall.sh", {}, "apply_patch", {"command": patch("*** Update File: ../.dev-loop/hooks/x.sh")}, True,
             str(repo / "tests")),
        ]
        for script, caller, tool, tool_input, deny, cwd in rows:
            with self.subTest(script=script, caller=caller.get("agent_type", "main"), tool=tool, tool_input=tool_input,
                              cwd=cwd):
                result = self.run_hook(script, {**caller, "cwd": cwd, "tool_name": tool, "tool_input": tool_input})
                self.assertEqual(result.returncode, 2 if deny else 0, result.stderr)
                if deny:
                    self.assertEqual(result.stderr.count("\n"), 1, result.stderr)
                    self.assertIn("upgrade", result.stderr)


class CdWallTests(unittest.TestCase):
    """A `cd` earlier in the same command moves the folder the walls resolve a relative path against."""

    def test_a_cd_in_the_command_cannot_hide_a_walled_path(self) -> None:
        claude = ("bash", ROOT / "claude/hooks/deny-acceptance-to-subagents.sh")
        codex = ("sh", ROOT / "codex/hooks/acceptance-wall.sh")
        rows = []
        for (shell, script), tester in [(claude, "spec-tester"), (codex, "spec_tester")]:
            implementer = {"agent_id": "a", "agent_type": "implementer"}
            spec_tester = {"agent_id": "b", "agent_type": tester}
            rows += [(shell, script, implementer, command, deny) for command, deny in [
                ("cd tests && cat acceptance/x.py", True),
                ("cd tests; cd acceptance && ls", True),
                ("cd tests && cd acceptance && cat x.py", True),
                ("cd /repo/tests && cat acceptance/x.py", True),
                ("cd ./docs/../tests && cat acceptance/x.py", True),
                ("(cd tests && ls acceptance)", True),
                ("bash -c 'cd tests && cat acceptance/x.py'", True),
                ("pushd tests && cat acceptance/x.py", True),
                ("cd -P tests && cat acceptance/x.py", True),
                ("cd tests && cat unit/x.py", False),
                ("cd docs && ls", False),
                ("cd tests/unit && cat ../README.md", False),
                ("{ cd tests; cat acceptance/x.py; }", True),
                ("if true; then cd tests; cat acceptance/x.py; fi", True),
                ("for f in a; do cd tests; cat acceptance/x.py; done", True),
                ("echo `cd tests && cat acceptance/x.py`", True),
                ("cd -- tests && cat acceptance/x.py", True),
                ("builtin cd tests && cat acceptance/x.py", True),
                ("command cd tests && cat acceptance/x.py", True),
                ("cat acceptance/notes.md; cd tests && make", False),
            ]]
            rows += [(shell, script, spec_tester, command, deny) for command, deny in [
                ("cd lib/.. && cd backend && cat main.py", True),
                ("cd tests && cat ../src/x.py", True),
                ("cd tests && cat acceptance/x.py", False),
            ]]
        rows.append(("sh", codex[1], {}, "cd tests && cat acceptance/x.py", False))
        for shell, script, caller, command, deny in rows:
            with self.subTest(script=script.name, caller=caller.get("agent_type", "main"), command=command):
                payload = {**caller, "cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": command}}
                result = subprocess.run([shell, str(script)], input=json.dumps(payload), capture_output=True,
                                        text=True, timeout=60)
                self.assertEqual(result.returncode, 2 if deny else 0, result.stderr)


class CodexTrustWarningTests(unittest.TestCase):
    """Codex skips an untrusted hook without a message: its SessionStart hook names the untrusted events."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve() / "home"
        self.repo = Path(tmp.name).resolve() / "repo"
        put(self.repo / "dev-loop.json", '{"version": "2.2.0", "harnesses": ["codex"]}\n')
        put(self.repo / ".codex" / "hooks.json", json.dumps({"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "y"}, {"type": "command", "command": "z"}]}]}}))
        (self.repo / ".agents" / "skills").mkdir(parents=True)
        (self.repo / ".agents" / "skills" / "dev-loop").symlink_to(ROOT / "skill")
        git(self.repo, "init", "-q")

    def start(self, *events: str, hooks: int = 2) -> subprocess.CompletedProcess[str]:
        lines = [f'[projects."{self.repo}"]', 'trust_level = "trusted"']
        entries = json.loads((self.repo / ".codex" / "hooks.json").read_text())["hooks"]
        for name, groups in entries.items():
            if check_setup.snake_case(name) not in events:
                continue
            for j, hook in enumerate(groups[0]["hooks"][:hooks]):
                key = f"{self.repo}/.codex/hooks.json:{check_setup.snake_case(name)}:0:{j}"
                lines += [f'[hooks.state."{key}"]', f'trusted_hash = "{check_setup.codex_hook_hash(name, groups[0], hook)}"']
        put(self.home / ".codex" / "config.toml", "\n".join(lines) + "\n")
        return subprocess.run(["sh", str(ROOT / "codex/hooks/session-start.sh")],
                              input=json.dumps({"cwd": str(self.repo)}), capture_output=True, text=True, timeout=60,
                              env={**os.environ, "HOME": str(self.home), "KIT_MAIN": str(self.repo)})

    def test_untrusted_hooks_are_named_and_trusted_hooks_add_nothing(self) -> None:
        result = self.start("session_start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Invoke it before any other action.", result.stdout)
        self.assertIn("Warning:", result.stdout)
        self.assertIn("stop", result.stdout)
        self.assertNotIn("session_start", result.stdout.split("Warning:")[1])
        self.assertIn("/hooks", result.stdout)
        result = self.start("session_start", "stop")
        self.assertEqual((result.returncode, result.stdout.count("\n")), (0, 1), result.stdout)
        self.assertNotIn("Warning:", result.stdout)

    def test_a_second_untrusted_hook_of_a_trusted_event_is_named(self) -> None:
        result = self.start("session_start", "stop", hooks=1)
        self.assertIn("Warning:", result.stdout)
        self.assertIn("stop", result.stdout.split("Warning:")[1])

    def test_no_kit_checker_or_no_dev_loop_adds_no_warning(self) -> None:
        (self.repo / ".agents" / "skills" / "dev-loop").unlink()
        result = self.start()
        self.assertEqual((result.returncode, result.stdout.count("\n")), (0, 1), result.stdout)
        (self.repo / "dev-loop.json").unlink()
        self.assertEqual(self.start().stdout, "")

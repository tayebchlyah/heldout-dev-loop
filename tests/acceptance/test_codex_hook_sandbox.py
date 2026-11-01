#!/usr/bin/env python3
"""Codex Hook Sandbox flows through installed hooks in linked scratch worktrees.

The native confinement flow uses a real Codex launcher when available. The other
flows use a transparent launcher only to isolate routing and startup outcomes.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "bin/install.mjs"
HOOK = re.compile(r"/\.dev-loop/hooks/codex/([\w.-]+)\"(.*)$")


def put(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)


class CodexHookSandboxFlows(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="codex-hook-flow-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.main = self.root / "main checkout"
        self.feature = self.root / "feature checkout"
        self.home = self.root / "home"
        self.notes = self.root / "notes"
        self.bin = self.root / "bin"
        for folder in (self.main, self.home, self.notes, self.bin):
            folder.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("GIT_", "CODEX", "CLAUDE", "DEVLOOP", "DEV_LOOP"))}
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        self.env.update(HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                        GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="false",
                        DEVLOOP_BOARD_PORT=str(port), DEV_LOOP_NOTES=str(self.notes),
                        PYTHONDONTWRITEBYTECODE="1")
        self.run_at(self.main, "git", "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false")):
            self.run_at(self.main, "git", "config", key, value)
        self.run_at(self.main, "node", str(INSTALLER), "init", "--yes", "--harness=both",
                    f"--notes={self.notes}")
        self.marker = self.root / "host-execution-marker"
        self.configure_gate("python3 unit_probe.py")
        put(self.main / "unit_probe.py", "raise SystemExit(0)\n")
        self.run_at(self.main, "git", "add", "-A")
        self.run_at(self.main, "git", "commit", "-q", "-m", "base")
        self.run_at(self.main, "git", "worktree", "add", "-q", "-b", "fix/pelican", str(self.feature))
        put(self.home / ".codex/config.toml",
            f"[projects.{json.dumps(str(self.main))}]\ntrust_level = \"trusted\"\n")
        put(self.bin / "codex", "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then echo 'codex-cli 0.166.0'; exit 0; fi\n"
            "while [ \"$#\" -gt 0 ] && [ \"$1\" != -- ]; do shift; done\n"
            "[ \"$#\" -gt 0 ] || exit 93\nshift\nexec \"$@\"\n", True)
        self.env["PATH"] = f"{self.bin}{os.pathsep}{self.env.get('PATH', '')}"
        self.addCleanup(self.stop_board)

    def run_at(self, cwd: Path, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(args, cwd=cwd, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, expected, f"{args}: {done.stdout[-1500:]} {done.stderr[-1500:]}")
        return done

    def stop_board(self) -> None:
        cli = self.main / ".agents/skills/dev-loop/scripts/devloop.py"
        subprocess.run([sys.executable, "-I", str(cli), "board", "--stop-server"], cwd=self.main,
                       env=self.env, capture_output=True, timeout=20)

    def configure_gate(self, gate: str | None, project: Path | None = None) -> None:
        project = project or self.main
        path = project / "dev-loop.json"
        config = json.loads(path.read_text()) if path.exists() else {}
        config["commands"] = {"gate": gate} if gate is not None else {}
        put(path, json.dumps(config) + "\n")

    def hooks(self) -> dict:
        return json.loads((self.main / ".codex/hooks.json").read_text())["hooks"]

    def call(self, event: str, script: str, **fields) -> subprocess.CompletedProcess[str]:
        entries = [hook["command"] for group in self.hooks().get(event, []) for hook in group["hooks"]
                   if f"/{script}\"" in hook.get("command", "")]
        self.assertEqual(len(entries), 1, f"{event} has {len(entries)} entries for {script}")
        payload = {"cwd": str(self.feature), "hook_event_name": event,
                   "agent_id": "pelican-agent", "agent_type": "implementer", **fields}
        return subprocess.run(["sh", "-c", entries[0]], cwd=self.feature, env=self.env,
                              input=json.dumps(payload), capture_output=True, text=True, timeout=90)

    def block(self, done: subprocess.CompletedProcess[str]) -> str:
        self.assertEqual(done.returncode, 0, done.stderr[-1200:])
        try:
            value = json.loads(done.stdout)
        except ValueError:
            self.fail(f"No block JSON: {done.stdout[-1200:]!r} {done.stderr[-1200:]!r}")
        self.assertEqual(value.get("decision"), "block", value)
        return value.get("reason", "")

    def test_feature_changes_are_automatically_checked(self) -> None:
        """F1, O1/O2/O8: both Stop events test dirty feature files through both gate routes."""
        put(self.feature / "unit_probe.py", "import os\n"
            "assert os.environ['GATE_SCOPE'] == 'changed'\n"
            "raise AssertionError('pelican feature-only test failed')\n")
        for event, mapped in (("Stop", True), ("SubagentStop", False)):
            with self.subTest(event=event, mapped=mapped):
                self.configure_gate("python3 unit_probe.py" if mapped else None, self.feature)
                if not mapped:
                    put(self.feature / "scripts/gate.sh", "#!/bin/sh\npython3 unit_probe.py\n", True)
                reason = self.block(self.call(event, "gate.sh"))
                self.assertIn("pelican feature-only test failed", reason)
        put(self.feature / "unit_probe.py", "from pathlib import Path\n"
            "Path('normal-feature-test-ran').write_text('ok')\n")
        done = self.call("Stop", "gate.sh")
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertTrue((self.feature / "normal-feature-test-ran").exists())

    def test_native_gate_confines_host_access_and_preserves_local_tests(self) -> None:
        """F2, O3/O4: real native enforcement covers gate descendants and ignores feature policy."""
        native = shutil.which("codex", path=os.environ.get("PATH", ""))
        if not native or sys.platform not in ("darwin", "linux"):
            self.skipTest("native Codex launcher unavailable on this host")
        version = subprocess.run([native, "--version"], capture_output=True, text=True, timeout=10)
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
        if not match or tuple(map(int, match.groups())) < (0, 166, 0):
            self.skipTest("native confinement needs codex-cli >= 0.166.0")
        (self.bin / "codex").unlink()
        (self.bin / "codex").symlink_to(native)
        outside = Path.home() / f"devloop-hook-probe-{uuid.uuid4().hex}"
        self.addCleanup(outside.unlink, missing_ok=True)
        secret = "pelican-secret-" + uuid.uuid4().hex
        put(self.feature / ".env", secret)
        put(self.feature / ".codex/config.toml", 'default_permissions = ":full-access"\n')
        probe = '''import json, os, socket, subprocess, sys
from pathlib import Path
def attempt(action):
    try:
        action()
        return "allowed"
    except OSError:
        return "denied"
outside = Path(sys.argv[1])
result = {}
result["workspace"] = attempt(lambda: Path("workspace-" + sys.argv[2]).write_text("ok"))
result["outside"] = attempt(lambda: outside.write_text("unexpected"))
result["env"] = attempt(lambda: Path(".env").read_text())
with socket.socket() as local:
    result["loopback"] = attempt(lambda: local.bind(("127.0.0.1", 0)))
docker = subprocess.run(["docker", "version"], capture_output=True, text=True)
result["docker"] = "allowed" if docker.returncode == 0 else "denied"
result["scope"] = os.environ.get("GATE_SCOPE")
Path("result-" + sys.argv[2] + ".json").write_text(json.dumps(result))
if sys.argv[2] == "parent":
    subprocess.run([sys.executable, __file__, str(outside), "child"], check=True)
'''
        put(self.feature / "host_probe.py", probe)
        docker = shutil.which("docker", path=os.environ.get("PATH", ""))
        if not docker:
            self.skipTest("Docker client unavailable; socket denial cannot be distinguished from missing client")
        context = subprocess.run([docker, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                                 capture_output=True, text=True, timeout=20)
        endpoint = os.environ.get("DOCKER_HOST") or context.stdout.strip()
        if context.returncode or not endpoint.startswith("unix://"):
            self.skipTest("Docker unix-socket endpoint unavailable for socket denial probe")
        self.env["DOCKER_HOST"] = endpoint
        baseline = subprocess.run([docker, "version"], env=self.env, capture_output=True, text=True, timeout=20)
        if baseline.returncode:
            self.skipTest("Docker engine unavailable before native confinement probe")
        for mapped in (True, False):
            with self.subTest(mapped=mapped):
                command = f"python3 host_probe.py {shlex.quote(str(outside))} parent"
                self.configure_gate(command if mapped else None, self.feature)
                if not mapped:
                    put(self.feature / "scripts/gate.sh", "#!/bin/sh\n" + command + "\n", True)
                for who in ("parent", "child"):
                    (self.feature / f"result-{who}.json").unlink(missing_ok=True)
                done = self.call("Stop", "gate.sh")
                self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr[-1200:])
                for who in ("parent", "child"):
                    report = self.feature / f"result-{who}.json"
                    self.assertTrue(report.is_file(), f"{who} did not run; no denial can be inferred")
                    self.assertEqual(json.loads(report.read_text()), {
                        "workspace": "allowed", "outside": "denied", "env": "denied",
                        "loopback": "allowed", "docker": "denied", "scope": "changed"})
                self.assertFalse(outside.exists(), "gate wrote outside workspace")

    def test_upgrade_trusts_main_hooks_and_preserves_claude_and_network_scope(self) -> None:
        """F3, O5/O9/O10 plus Network Scope: migrate old entries, keep user hooks and network rules."""
        claude = (self.main / ".claude/settings.json").read_bytes()
        config = self.main / ".codex/config.toml"
        text = config.read_text().replace('[permissions.dev-workspace.network.domains]',
            '[permissions.dev-workspace.network.domains]\n"pelican.invalid" = "deny"')
        config.write_text(text)
        network = tomllib.loads(text)["permissions"]["dev-workspace"]["network"]
        hooks = self.hooks()
        expected = []
        for event, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    match = HOOK.search(hook.get("command", ""))
                    self.assertIsNotNone(match, hook)
                    script, suffix = match.groups()
                    expected.append((event, script, group.get("matcher")))
                    hook["command"] = f'"$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/{script}"{suffix}'
        user = {"type": "command", "command": "echo pelican-user-hook"}
        hooks["Stop"].append({"hooks": [user]})
        put(self.main / ".codex/hooks.json", json.dumps({"hooks": hooks}))
        for _ in range(2):
            self.run_at(self.main, "node", str(INSTALLER), "upgrade", "--yes")
        self.assertEqual((self.main / ".claude/settings.json").read_bytes(), claude)
        self.assertEqual(tomllib.loads(config.read_text())["permissions"]["dev-workspace"]["network"], network)
        self.assertIn(user, [hook for group in self.hooks()["Stop"] for hook in group["hooks"]])
        for event, script, matcher in expected:
            matching = [hook for group in self.hooks()[event] if group.get("matcher") == matcher
                        for hook in group["hooks"] if f"/{script}\"" in hook.get("command", "")]
            self.assertEqual(len(matching), 1, f"duplicate {event}/{script}")
        marker = shlex.quote(str(self.marker))
        for script in (self.feature / ".dev-loop/hooks/codex").glob("*.sh"):
            put(script, f"#!/bin/sh\nprintf feature-hook >> {marker}\n", True)
        for filename in ("devloop.py", "check_setup.py"):
            put(self.feature / ".agents/skills/dev-loop/scripts" / filename,
                f"from pathlib import Path\nPath({str(self.marker)!r}).write_text('feature checker')\n")
        put(self.feature / "json.py", f"from pathlib import Path\nPath({str(self.marker)!r}).write_text('cwd import')\n")
        self.env["PYTHONPATH"] = str(self.feature)
        for event, script, _ in expected:
            with self.subTest(event=event, script=script):
                self.call(event, script, tool_name="Bash", tool_input={"command": "echo local-test"})
        self.assertFalse(self.marker.exists(), "installed entry ran feature-controlled code on host")

    def test_launcher_failure_blocks_without_host_fallback(self) -> None:
        """F4, O7: startup error blocks a dirty feature gate and never runs its command on host."""
        put(self.bin / "codex", "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then echo 'codex-cli 0.166.0'; exit 0; fi\n"
            "echo 'pelican sandbox startup failed' >&2\nexit 78\n", True)
        self.configure_gate(f"printf escaped > {shlex.quote(str(self.marker))}", self.feature)
        reason = self.block(self.call("Stop", "gate.sh"))
        self.assertRegex(reason.lower(), r"sandbox.*(start|launch)|(?:start|launch).*sandbox")
        self.assertFalse(self.marker.exists(), "failed sandbox used host fallback")


if __name__ == "__main__":
    unittest.main()

"""Shared fixtures of the Codex Kit Copy held-out tests (spec: Codex Kit Copy, seam 1).

A scratch project made by `bin/install.mjs init`, with a linked feature worktree; HOME, CODEX_HOME, TMPDIR and
the notes folder all live in one temporary folder. Each Codex hook command of `.codex/hooks.json` runs through
`sh -c` with hook JSON on stdin. Changed or planted code appends a line to one marker file outside the
workspace: a line there means that code ran on the host. Every name in the scratch projects is invented.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import tomllib
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "bin" / "install.mjs"
KIT_LIB = ROOT / "lib" / "kit.mjs"

MANIFEST = ".dev-loop/MANIFEST.json"
HOOKS_DIR = ".dev-loop/hooks/codex/"
SCRIPTS_DIR = ".agents/skills/dev-loop/scripts/"
D6 = (HOOKS_DIR, SCRIPTS_DIR)
HEAD = "${CODEX_HOME:-$HOME/.codex}/dev-loop/kit/"
KIT_ENTRY = re.compile(re.escape(HEAD) + r"([0-9a-f]{64})/" + re.escape(".dev-loop/hooks/codex/") + r'([\w.-]+)"')
SCRIPTS = frozenset({"acceptance-wall.sh", "board.sh", "gate.sh", "git-guard.sh", "seal.sh", "session-start.sh"})
# The script arguments of each kit entry, by event and script; no line: no argument.
ARGS = {("SessionStart", "board.sh"): "session-start", ("UserPromptSubmit", "board.sh"): "prompt",
        ("PostToolUse", "board.sh"): "post-tool", ("Stop", "seal.sh"): "turn", ("SubagentStart", "seal.sh"): "start",
        ("SubagentStop", "seal.sh"): "stop"}

BLOCK = ("Blocked: dev-loop kit at {source} does not match the trusted hooks: {path}. "
         "Restore the kit files on main (git checkout), or run upgrade, then /hooks.")
BLOCK_PART = " does not match the trusted hooks: "
STALE = "dev-loop: the hooks run the trusted kit copy; these files differ from it: "

SUBAGENT = {"agent_id": "heron-agent", "agent_type": "implementer"}
ECHO = {"tool_name": "Bash", "tool_input": {"command": "echo heron"}}
START = {"source": "startup"}
STOP = {"stop_hook_active": False}
# Inputs that make each wall reach the CLI: a push of the scratch tag `v9.9.9` (release-check), a subagent
# search (search-wall).
TAG_PUSH = {"tool_name": "Bash", "tool_input": {"command": "git push origin v9.9.9"}}
SEARCH = {**SUBAGENT, "tool_name": "Bash", "tool_input": {"command": "grep -rn heron ."}}

# The earlier Codex heads, as earlier kits wrote them: `toplevel` (2.x), `rc.4` (#200), `#222` (the main walk).
MISSING = '; [ -x "$f" ] || { echo "Blocked: dev-loop hook script missing: $f" >&2; exit 2; }; exec "$f"'
RC4_PREFIX = r"""f="$(git worktree list --porcelain | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')"""
WALK_PREFIX = (
    r"""f="$(d=$(pwd -P) m= i=; while :; do if [ -e "$d/.git" ]; then w=$(git -C "$d" worktree list --porcelain"""
    r""" | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}'); if [ -z "$i" ]; then m=$w;"""
    r""" elif [ -d "$w/.dev-loop" ] && git -C "$d" worktree list --porcelain | sed -n 's/^worktree //p' | {"""
    r""" while IFS= read -r p; do [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$d" ] && exit 0; done; exit 1; } &&"""
    r""" sed -n -E 's|^[[]hooks[.]state[.]"(/.*)/[.]codex/hooks[.]json:[^"]*"[]][[:space:]]*$|\1|p'"""
    r""" "${CODEX_HOME:-$HOME/.codex}/config.toml" 2>/dev/null | { while IFS= read -r p; do"""
    r""" [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$w" ] && exit 0; done; exit 1; }; then m=$w; fi; i=1; fi;"""
    r""" [ "$d" = / ] && break; d=${d%/*}; d=${d:-/}; done; printf %s "$m")""")
EARLIER_FORMS = ("toplevel", "rc.4", "#222")


def earlier_head(form: str, script: str, args: str) -> str:
    tail = f" {args}" if args else ""
    if form == "toplevel":
        return f'"$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/{script}"{tail}'
    prefix = RC4_PREFIX if form == "rc.4" else WALK_PREFIX
    return f'{prefix}/.dev-loop/hooks/codex/{script}"{MISSING}{tail}'


def put(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree(folder: Path) -> dict[str, bytes | None]:
    """Each path under `folder`: file bytes, None for a folder."""
    if not folder.exists():
        return {}
    return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(folder.rglob("*"))}


def hook_keys(commands: list[str]) -> list:
    """`codexHookKey` of `lib/kit.mjs` for each command."""
    script = ("const {pathToFileURL} = require('url');"
              "import(pathToFileURL(process.argv[1]).href).then(m => {"
              "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
              "process.stdout.write(JSON.stringify(input.map(c => m.codexHookKey(c))));})")
    done = subprocess.run(["node", "-e", script, str(KIT_LIB)], input=json.dumps(commands), capture_output=True,
                          text=True, timeout=60)
    if done.returncode:
        raise AssertionError(f"codexHookKey failed: {done.stderr[-1500:]}")
    return json.loads(done.stdout)


def kit_source(test: unittest.TestCase) -> Path:
    """A copy of this kit source whose `skill/references/tests.md` differs: another MANIFEST."""
    temporary = tempfile.TemporaryDirectory(prefix="codex-kit-source-")
    test.addCleanup(temporary.cleanup)
    source = Path(temporary.name) / "kit"
    skip = {".git", "node_modules", "tests", ".claude", ".codex", ".agents", ".dev-loop"}
    shutil.copytree(ROOT, source, symlinks=True,
                    ignore=lambda folder, names: skip & set(names) if Path(folder) == ROOT else set())
    changed = source / "skill/references/tests.md"
    test.assertTrue(changed.is_file(), changed)
    changed.write_text(changed.read_text() + "\nA later kit line, heron.\n")
    return source


class Scratch:
    """One installed scratch project: main checkout, linked feature worktree, temporary HOME."""

    def __init__(self, test: unittest.TestCase, *, codex_home: str | None = None, harness: str = "codex",
                 installer: Path = INSTALLER) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="codex-kit-copy-")
        test.addCleanup(temporary.cleanup)
        self.test = test
        self.root = Path(temporary.name).resolve()
        self.main = self.root / "main checkout"
        self.feature = self.root / "feature checkout"
        self.home = self.root / "home"
        self.notes = self.root / "notes"
        self.bin = self.root / "bin"
        self.tmp = self.root / "tmp"
        self.marker = self.root / "host marker"
        for folder in (self.main, self.home, self.notes, self.bin, self.tmp):
            folder.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("GIT_", "CODEX", "CLAUDE", "DEVLOOP", "DEV_LOOP", "KIT_"))}
        for key in ("TMPDIR", "TMP", "TEMP"):
            if key == "TMPDIR" or key in self.env:
                self.env[key] = str(self.tmp)
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        self.env.update(HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                        DEVLOOP_BOARD_PORT=str(port), DEV_LOOP_NOTES=str(self.notes), PYTHONDONTWRITEBYTECODE="1")
        self.codex_home = self.root / codex_home if codex_home else self.home / ".codex"
        if codex_home:
            self.env["CODEX_HOME"] = str(self.codex_home)
        self.kit_dir = self.codex_home / "dev-loop" / "kit"
        put(self.codex_home / "config.toml", f"[projects.{json.dumps(str(self.main))}]\ntrust_level = \"trusted\"\n")
        put(self.bin / "codex", "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then echo 'codex-cli 0.166.0'; exit 0; fi\n"
            "while [ \"$#\" -gt 0 ] && [ \"$1\" != -- ]; do shift; done\n"
            "[ \"$#\" -gt 0 ] || exit 93\nshift\nexec \"$@\"\n", True)
        self.env["PATH"] = f"{self.bin}{os.pathsep}{self.env.get('PATH', '')}"
        self.git(self.main, "init", "-q", "-b", "main")
        self.run(self.main, "node", str(installer), "init", "--yes", f"--harness={harness}", f"--notes={self.notes}")
        config = json.loads((self.main / "dev-loop.json").read_text())
        config["commands"] = {"gate": "python3 unit_probe.py"}
        put(self.main / "dev-loop.json", json.dumps(config) + "\n")
        put(self.main / "unit_probe.py", "raise SystemExit(0)\n")
        self.git(self.main, "add", "-A")
        self.git(self.main, "commit", "-q", "-m", "base")
        self.git(self.main, "tag", "v9.9.9")
        self.git(self.main, "worktree", "add", "-q", "-b", "feat/heron", str(self.feature))
        test.addCleanup(self.stop_board)

    # Processes.

    def run(self, cwd: Path, *args: str, expected: int | None = 0, env: dict | None = None,
            stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(args, cwd=cwd, env=env or self.env, input=stdin, capture_output=True, text=True,
                              timeout=180)
        if expected is not None:
            self.test.assertEqual(done.returncode, expected,
                                  f"{args}: {done.stdout[-1500:]} {done.stderr[-1500:]}")
        return done

    def git(self, cwd: Path, *args: str) -> str:
        return self.run(cwd, "git", "-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid",
                        "-c", "commit.gpgsign=false", *args).stdout.strip()

    def install(self, command: str, installer: Path = INSTALLER) -> subprocess.CompletedProcess[str]:
        return self.run(self.main, "node", str(installer), command, "--yes")

    def stop_board(self) -> None:
        subprocess.run(["git", "checkout", "-q", "--", "."], cwd=self.main, env=self.env, capture_output=True,
                       timeout=60)
        cli = self.main / SCRIPTS_DIR / "devloop.py"
        if cli.is_file():
            subprocess.run(["python3", "-I", str(cli), "board", "--stop-server"], cwd=self.main, env=self.env,
                           capture_output=True, timeout=30)

    # Hook entries.

    def hooks(self) -> dict:
        return json.loads((self.main / ".codex/hooks.json").read_text())["hooks"]

    def save_hooks(self, hooks: dict) -> None:
        put(self.main / ".codex/hooks.json", json.dumps({"hooks": hooks}, indent=2) + "\n")

    def all_hooks(self) -> list[tuple[str, str | None, dict]]:
        return [(event, group.get("matcher"), hook) for event, groups in self.hooks().items()
                for group in groups for hook in group["hooks"]]

    def kit_entries(self) -> list[tuple[str, str | None, str]]:
        """(event, matcher, command) of each entry whose command holds the kit copy head, sorted."""
        return sorted((event, matcher, hook["command"]) for event, matcher, hook in self.all_hooks()
                      if self.entry(hook.get("command", "")))

    @staticmethod
    def entry(command: str) -> tuple[str, str] | None:
        """(H, script) of the kit copy head in `command`."""
        match = KIT_ENTRY.search(command)
        return match.groups() if match else None

    def command(self, event: str, script: str) -> str:
        found = [hook["command"] for name, _, hook in self.all_hooks()
                 if name == event and (self.entry(hook.get("command", "")) or ("", ""))[1] == script]
        self.test.assertEqual(len(found), 1, f"{event} entries for {script}")
        return found[0]

    def payload(self, event: str, cwd: Path, fields: dict) -> str:
        return json.dumps({"session_id": str(uuid.uuid4()), "cwd": str(cwd), "hook_event_name": event, **fields})

    def call(self, event: str, script: str, cwd: Path | None = None, env: dict | None = None,
             **fields) -> subprocess.CompletedProcess[str]:
        cwd = cwd or self.feature
        return subprocess.run(["sh", "-c", self.command(event, script)], cwd=cwd, env=env or self.env,
                              input=self.payload(event, cwd, fields), capture_output=True, text=True, timeout=180)

    def start(self, event: str, script: str, cwd: Path | None = None, **fields) -> subprocess.Popen:
        cwd = cwd or self.feature
        process = subprocess.Popen(["sh", "-c", self.command(event, script)], cwd=cwd, env=self.env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process.stdin.write(self.payload(event, cwd, fields))
        process.stdin.close()
        return process

    @staticmethod
    def result(done: subprocess.CompletedProcess[str]) -> tuple[int, str]:
        return done.returncode, done.stdout

    # The kit and its copy.

    def manifest_hash(self, top: Path | None = None) -> str:
        return digest_of(((top or self.main) / MANIFEST).read_bytes())

    def manifest_files(self, top: Path | None = None) -> dict[str, str]:
        return json.loads(((top or self.main) / MANIFEST).read_text())["files"]

    def kit_files(self, top: Path | None = None) -> dict[str, bytes]:
        """`.dev-loop/MANIFEST.json` and each MANIFEST entry under the D6 folders, from `top`."""
        top = top or self.main
        names = [MANIFEST] + sorted(name for name in self.manifest_files(top) if name.startswith(D6))
        return {name: (top / name).read_bytes() for name in names}

    @staticmethod
    def copy_files(folder: Path) -> dict[str, bytes]:
        """Each file under a copy folder, Python's own cache left out."""
        return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*"))
                if p.is_file() and "__pycache__" not in p.relative_to(folder).parts}

    def copy(self, digest: str | None = None) -> Path:
        return self.kit_dir / (digest or self.manifest_hash())

    def kit_children(self) -> list[str]:
        return sorted(os.listdir(self.kit_dir)) if self.kit_dir.exists() else []

    def drop_copy(self) -> None:
        if self.kit_dir.exists():
            for path in self.kit_dir.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            shutil.rmtree(self.kit_dir)

    def marker_code(self, rel: str, label: str) -> str:
        if rel.endswith(".py"):
            return (f"with open({str(self.marker)!r}, 'a') as marker:\n"
                    f"    marker.write({label!r} + '\\n')\n")
        return f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(label)} >> {shlex.quote(str(self.marker))}\n"

    def change(self, rel: str, top: Path | None = None, label: str | None = None) -> bytes:
        """Replace the kit file `rel` under `top` (default main) with code that writes the marker."""
        path = (top or self.main) / rel
        self.test.assertTrue(path.is_file(), path)
        path.write_text(self.marker_code(rel, label or f"changed {rel}"))
        return path.read_bytes()

    def restore(self) -> None:
        self.git(self.main, "checkout", "-q", "--", ".")

    def markers(self) -> str:
        return self.marker.read_text() if self.marker.exists() else ""

    @staticmethod
    def settle() -> None:
        """Let a background `devloop.py board` that a hook started finish before a marker check."""
        time.sleep(2)

    # Hook output.

    def block(self, path: str, source: Path | None = None) -> str:
        return BLOCK.format(source=source or self.main, path=path)

    def stdout_json(self, done: subprocess.CompletedProcess[str]) -> dict:
        try:
            value = json.loads(done.stdout)
        except ValueError:
            self.test.fail(f"no JSON on stdout: {done.stdout[-1200:]!r} {done.stderr[-1200:]!r}")
        self.test.assertIsInstance(value, dict, done.stdout)
        return value

    def system_message(self, done: subprocess.CompletedProcess[str]) -> str:
        self.test.assertEqual(done.returncode, 0, done.stdout[-1200:] + done.stderr[-1200:])
        return str(self.stdout_json(done).get("systemMessage", ""))

    def contexts(self, done: subprocess.CompletedProcess[str]) -> list[str]:
        """Each `additionalContext` string in the stdout JSON, at any depth."""
        found: list[str] = []

        def walk(node: object) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "additionalContext" and isinstance(value, str):
                        found.append(value)
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(self.stdout_json(done))
        return found

    def assert_wall_blocks(self, done: subprocess.CompletedProcess[str], message: str) -> None:
        self.test.assertEqual(done.returncode, 2, done.stdout[-1200:] + done.stderr[-1200:])
        self.test.assertIn(message, done.stdout + done.stderr)

    def assert_no_kit_filesystem_entry(self) -> None:
        """O15: the `dev-workspace` filesystem table names no `.dev-loop`, `.agents` or `.codex` path."""
        config = tomllib.loads((self.main / ".codex/config.toml").read_text())
        table = json.dumps(config["permissions"]["dev-workspace"].get("filesystem", {}))
        for name in (".dev-loop", ".agents", ".codex"):
            self.test.assertNotIn(name, table)

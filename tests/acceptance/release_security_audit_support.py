#!/usr/bin/env python3
"""Shared fixtures of the held-out release security audit tests (spec: Release Security Audit).

One test per `## Acceptance oracle` line (O<n>), the variants a line lists as subtests, plus one test per
`## Acceptance flows` item (F<n>). The tests use only the declared seams: the stdout JSON and exit code of
`devloop.py release-check`, `release-plan` and `release-record`; `skill/scripts/sandbox-run.py` (its stdout,
stderr, exit code and the host scratch folder) against a real Docker engine; the kit `git-guard.sh` hooks fed
hook JSON on stdin; `check_setup.py` output; `bin/install.mjs init` and `upgrade`; the kit text. Every name in
the temp repos is invented.

Fixture choices the spec leaves open:
- Notes path: `DEV_LOOP_NOTES` (release commands), the `init --notes` local file (guard, setup checks).
- Run folders and ids as Feature Folders replaced them: `Security Audit/Run <N>`, id `Run <N>`.
- The `security-audit` validators are stub `.cjs` files in a folder named by `DEVLOOP_TEST_SECURITY_AUDIT_DIR`.
- `findings.json` verdicts are `confirmed`, `needs_validation`, `rejected` (the skill's schema names).
- `<today>` is the local date or the UTC date (a run at midnight may see either).
- Docker cases use `busybox:1.37` pinned by digest, pulled once per suite run. They skip only when
  `docker info` fails and `DEVLOOP_REQUIRE_DOCKER` is unset; with it set, a missing engine fails them.
- O40 "holds only `f`": the only file that is not a folder is the regular file `f` (the runner itself makes
  the `home`, `tmp` and `cache` folders under `/scratch`).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
_GIT_CONFIG_COUNT = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
if not any(
    os.environ.get(f"GIT_CONFIG_KEY_{i}") == "core.fsmonitor" and os.environ.get(f"GIT_CONFIG_VALUE_{i}") == "false"
    for i in range(_GIT_CONFIG_COUNT)
):
    os.environ.update({
        f"GIT_CONFIG_KEY_{_GIT_CONFIG_COUNT}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{_GIT_CONFIG_COUNT}": "false",
        "GIT_CONFIG_COUNT": str(_GIT_CONFIG_COUNT + 1),
    })

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
SANDBOX = REPO_ROOT / "skill" / "scripts" / "sandbox-run.py"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
SKILL = REPO_ROOT / "skill" / "SKILL.md"
CLAUDE_REF = REPO_ROOT / "skill" / "references" / "claude.md"
CODEX_REF = REPO_ROOT / "skill" / "references" / "codex.md"
AUDIT_SKILL = REPO_ROOT / "skills" / "release-audit" / "SKILL.md"
AUDIT_OPENAI = REPO_ROOT / "skills" / "release-audit" / "agents" / "openai.yaml"
AGENTS = REPO_ROOT / "AGENTS.md"

IMAGE = "busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
IMAGE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ABSENT_IMAGES = ("busybox:1.31.1-uclibc", "busybox:1.30.0-musl", "busybox:1.29.3-glibc")
SANDBOX_LABEL = "label=devloop.sandbox=1"

APP_A = "def colour(theme):\n    return 'red'\n"
APP_B = "def colour(theme):\n    return theme or 'blue'\n"
BASE_CONFIG = {"version": "2.1.1", "harnesses": ["claude", "codex"]}
NOTES_NAME = "Acme Widgets"


def put(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)


def todays() -> set[str]:
    return {dt.date.today().isoformat(), dt.datetime.now(dt.timezone.utc).date().isoformat()}


def base_env(home: Path, tmp: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))}
    env.update({"HOME": str(home), "TMPDIR": str(tmp), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"})
    return env


def tools_dir(root: Path, *names: str) -> Path:
    """A folder with links to the named tools only (no docker, no node unless named)."""
    folder = root / ("tools-" + "-".join(names))
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        found = sys.executable if name == "python3" else shutil.which(name)
        if found and not (folder / name).exists():
            os.symlink(found, folder / name)
    return folder


def record_line(run: int, sha: str, scope: str = "whole", profile: str = "quick", date: str = "2026-10-25",
                confirmed: int = 0, needs: int = 0, hardening: int = 0) -> str:
    return (f"- {date} Run {run} at {sha}: confirmed {confirmed}, needs validation {needs}, "
            f"hardening {hardening}, profile {profile}, scope {scope}")


def note_text(records: list[str] | None = None, deferred: list[str] | None = None,
              gardener: list[str] | None = None) -> str:
    parts = [f"# {NOTES_NAME}\n\nThe widget service.\n"]
    if records is not None:
        parts.append("## Release audits\n\n" + "".join(line + "\n" for line in records))
    if deferred is not None:
        parts.append("## Deferred\n\n" + "".join(line + "\n" for line in deferred))
    if gardener is not None:
        parts.append("## Gardener\n\n" + "".join(line + "\n" for line in gardener))
    return "\n".join(parts)


def section(text: str, heading: str) -> list[str]:
    """The lines of a `## ` section, up to the next `## ` heading."""
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return []
    out = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        out.append(line)
    return out


def subsection(lines: list[str], heading: str) -> list[str]:
    try:
        start = lines.index(heading)
    except ValueError:
        return []
    out = []
    for line in lines[start + 1:]:
        if line.startswith("#"):
            break
        out.append(line)
    return out


def item_text(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("line", json.dumps(item)))
    return str(item)


def reasons_named(payload: dict, name: str) -> list[dict]:
    return [r for r in payload.get("reasons") or [] if isinstance(r, dict) and r.get("reason") == name]


def hook_decision(result: subprocess.CompletedProcess[str]) -> tuple[str | None, str]:
    """The permissionDecision and the reason text a guard gave (JSON on stdout, else exit 2 and stderr)."""
    decision, reason = None, ""
    try:
        out = json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:
        out = {}
    if isinstance(out, dict):
        specific = out.get("hookSpecificOutput") or {}
        decision = specific.get("permissionDecision") or out.get("decision")
        reason = str(specific.get("permissionDecisionReason") or out.get("reason") or "")
    if decision is None and result.returncode == 2:
        decision = "deny"
    return decision, reason + "\n" + result.stderr


class GitRepo:
    """A git repo with commits A then B (B changes src/app.py), and a notes folder outside it."""

    def __init__(self, root: Path, config: dict | None = None) -> None:
        self.root = root
        self.home = root / "home"
        self.tmp = root / "tmp"
        self.notes = root / "notes" / NOTES_NAME
        for folder in (self.home, self.tmp, self.notes):
            folder.mkdir(parents=True, exist_ok=True)
        self.env = base_env(self.home, self.tmp)
        self.env["DEV_LOOP_NOTES"] = str(self.notes)
        self.repo = root / "acme-widgets"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        self.Z = self.commit({"dev-loop.json": json.dumps(config or BASE_CONFIG, indent=2) + "\n",
                              "README.md": "# Acme widgets\n", "src/app.py": "pass\n",
                              "tests/unit/test_x.py": "def test_x():\n    assert 1 == 1\n"}, "Z")
        self.A = self.commit({"src/app.py": APP_A}, "A")
        self.B = self.commit({"src/app.py": APP_B}, "B")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def commit(self, files: dict[str, str], message: str) -> str:
        for path, text in files.items():
            put(self.repo / path, text)
        self.git("add", "-A", "--", *files)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def side_commit(self, start: str, files: dict[str, str], message: str) -> str:
        """A commit on its own branch from `start`; the checkout returns to main."""
        branch = f"side-{uuid.uuid4().hex[:8]}"
        self.git("checkout", "-q", "-b", branch, start)
        sha = self.commit(files, message)
        self.git("checkout", "-q", "main")
        return sha

    def tag(self, name: str, sha: str) -> None:
        self.git("tag", "-a", "-m", f"Release {name}", name, sha)

    def note_path(self, name: str = "Project Note.md") -> Path:
        return self.notes / name

    def write_note(self, text: str, name: str = "Project Note.md") -> Path:
        path = self.note_path(name)
        put(path, text)
        return path

    def run_folder(self, n: int, sha: str, status: str = "complete", profile: str = "quick") -> Path:
        folder = self.notes / "Security Audit" / f"Run {n}"
        folder.mkdir(parents=True, exist_ok=True)
        put(folder / "run-metadata.json", json.dumps({"source_ref": {"commit": sha}, "profile": profile,
                                                      "scope_paths": [], "run_status": status}, indent=2) + "\n")
        return folder

    def devloop(self, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None,
                timeout: float = 120) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd or self.repo,
                                env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=timeout)
        try:
            payload = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {"unparsed_stdout": result.stdout}
        if not isinstance(payload, dict):
            payload = {"unparsed_stdout": result.stdout}
        payload.setdefault("_stderr", result.stderr)
        return result.returncode, payload


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.count = 0

    def repo(self, config: dict | None = None) -> GitRepo:
        self.count += 1
        return GitRepo(self.root / f"r{self.count}", config)

    def audited(self, deferred: list[str] | None = None, gardener: list[str] | None = None) -> GitRepo:
        """The release-check base case: tag v1.0.0 on A, record line at A with its run folder."""
        r = self.repo()
        r.tag("v1.0.0", r.A)
        r.run_folder(1, r.A)
        r.write_note(note_text([record_line(1, r.A)], deferred if deferred is not None else [], gardener))
        return r


FINDINGS = [
    {"verdict": "confirmed", "fingerprint": "f-c", "title": "Session token written to the debug log",
     "blockers": []},
    {"verdict": "needs_validation", "fingerprint": "f-n1", "title": "Upload path join may leave the media root",
     "blockers": ["fact one", "fact two"]},
    {"verdict": "needs_validation", "fingerprint": "f-n2", "title": "Unbounded body size on the import route",
     "blockers": ["fact one", "fact two"]},
    {"verdict": "rejected", "fingerprint": "f-r", "title": "Rejected lead about the colour cache",
     "blockers": []},
]
REPORT = ("# Security audit report\n\n## Summary\n\nTwo subsystems.\n\n### Hardening (not findings)\n\n"
          "- Pin the base image.\n- Add a body size limit.\n- Drop the debug flag.\n\n## Coverage\n\n- not counted\n")
REPORT_NO_HARDENING = "# Security audit report\n\n## Summary\n\nTwo subsystems.\n\n### Hardening (not findings)\n\nNone.\n"
BASE_NOTE = note_text([], ["- Rename the parser module"], ["- 2026-10-01 (cleanup): tidy the fixtures"])


class RecordCase(Case):

    def setup_record(self, findings: list[dict] | None = None, status: str = "complete", report: str = REPORT,
                     note: str | None = BASE_NOTE, validator_exit: int = 0) -> tuple[GitRepo, Path]:
        r = self.repo()
        run = r.run_folder(2, r.A, status=status, profile="standard")
        put(run / "findings.json", json.dumps(FINDINGS if findings is None else findings, indent=2) + "\n")
        put(run / "coverage-ledger.json", "{}\n")
        put(run / "REPORT.md", report)
        put(run / "FINDINGS-DETAIL.md", "# Findings\n")
        put(run / "NEEDS-VALIDATION.md", "# Needs validation\n")
        skill = r.root / "security-audit-skill"
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            put(skill / name, f"process.exit({validator_exit})\n")
        r.env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"] = str(skill)
        if note is not None:
            r.write_note(note)
        return r, run

    def record(self, r: GitRepo, run: Path, *extra: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
        return r.devloop("release-record", "--run", str(run), *extra, env=env)

    def today_of(self, line: str) -> str:
        match = re.match(r"- (\d{4}-\d{2}-\d{2}) ", line)
        self.assertIsNotNone(match, line)
        self.assertIn(match.group(1), todays(), line)
        return match.group(1)

    def new_subsection(self, r: GitRepo) -> list[str]:
        deferred = section(r.note_path().read_text(), "## Deferred")
        heads = [line for line in deferred if re.fullmatch(r"### Release audit Run 2, \d{4}-\d{2}-\d{2}", line)]
        self.assertEqual(len(heads), 1, deferred)
        self.assertIn(heads[0].rsplit(", ", 1)[1], todays())
        return [line for line in subsection(deferred, heads[0]) if line.strip()]


def docker_up() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def docker(*args: str, check: bool = True, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result


def sandbox_containers() -> list[str]:
    return docker("ps", "-a", "-q", "--filter", SANDBOX_LABEL).stdout.split()


class SandboxCase(unittest.TestCase):
    """A target repo with `security_audit.images.t` the pinned busybox, an empty host scratch folder."""

    @classmethod
    def setUpClass(cls) -> None:
        if not docker_up():
            if os.environ.get("DEVLOOP_REQUIRE_DOCKER"):
                raise AssertionError("DEVLOOP_REQUIRE_DOCKER is set and `docker info` fails")
            raise unittest.SkipTest("no Docker engine (docker info fails)")
        if docker("image", "inspect", IMAGE, check=False).returncode != 0:
            docker("pull", "-q", IMAGE)

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        # The real HOME stays: the docker CLI reads its context (the engine socket) from ~/.docker.
        self.env = {key: value for key, value in os.environ.items() if not key.startswith(("DEV_LOOP", "DEVLOOP"))}
        self.env.update({"GIT_CONFIG_NOSYSTEM": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
        self.target = self.make_target({"security_audit": {"images": {"t": IMAGE}}})
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.addCleanup(self.no_container_left)

    def no_container_left(self) -> None:
        """O44: no sandbox container after any case."""
        left = sandbox_containers()
        if left:
            docker("rm", "-f", *left, check=False)
        self.assertEqual(left, [], "a devloop.sandbox container was left behind")

    def make_target(self, extra: dict, name: str = "target") -> Path:
        target = self.root / name
        target.mkdir(mode=0o755)
        put(target / "dev-loop.json", json.dumps({**BASE_CONFIG, **extra}, indent=2) + "\n")
        put(target / "src" / "app.py", APP_A)
        quiet = ("-c", "maintenance.auto=false", "-c", "gc.auto=0")
        for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                     ["-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Target"]):
            subprocess.run(["git", *quiet, *args], cwd=target, env=self.env, check=True, capture_output=True)
        for folder in (target, target / "src"):
            folder.chmod(0o755)
        return target

    def run_sandbox(self, *command: str, opts: tuple[str, ...] = (), image: str = "t", target: Path | None = None,
                    stdin: str | None = None, env: dict[str, str] | None = None,
                    timeout: float = 180) -> subprocess.CompletedProcess[str]:
        args = [sys.executable, str(SANDBOX), "--image", image, "--scratch", str(self.scratch),
                "--target", str(target or self.target), *opts, "--", *command]
        return subprocess.run(args, cwd=target or self.target, env={**self.env, **(env or {})}, input=stdin,
                              capture_output=True, text=True, timeout=timeout)

    def scratch_files(self) -> dict[str, str]:
        """Every entry under the host scratch folder: relative path to its kind."""
        kinds = {}
        for folder, dirs, names in os.walk(self.scratch):
            for name in dirs + names:
                path = Path(folder) / name
                rel = str(path.relative_to(self.scratch))
                if path.is_symlink():
                    kinds[rel] = "link"
                elif path.is_dir():
                    kinds[rel] = "dir"
                elif path.is_file():
                    kinds[rel] = "file"
                else:
                    kinds[rel] = "other"
        return kinds

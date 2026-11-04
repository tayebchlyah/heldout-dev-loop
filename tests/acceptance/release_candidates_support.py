#!/usr/bin/env python3
"""Shared fixtures of the held-out release candidate tests (spec: Release Candidates).

One test per `## Acceptance oracle` line (O<n>) or per `## Acceptance flows` item (F<n>); a flow test proves
the lines it names. The tests use only the seams the spec names: `bin/install.mjs init` and `upgrade` run from
a copy of the package with another `package.json` version; `version_key` of `skill/scripts/board/store.py`;
`devloop.py board`, `status`, `release-plan`, `release-check` and `check_setup.py` as processes on scratch git
repos; `scripts/repo_rules.py promoted`; the `release-notes` step of `.github/workflows/release.yml` on a scratch
repo with a written `CHANGELOG.md` (spec Written Release Notes supersedes the `git cliff` notes lines); the text
of `skill/SKILL.md`, `skills/release-audit/SKILL.md`, `AGENTS.md`, `reference/release.md` and
`reference/cli/*.md`. Every name in the scratch repos is invented.

Fixture choices the spec leaves open:
- `release.yml` is read with Ruby's YAML loader (no YAML module ships with Python). Its steps are evaluated
  for a tag by a small GitHub expression evaluator (`github.ref_name`, `env.*`, `steps.*.outputs.*`,
  `contains`, `startsWith`, `endsWith`, `!`, `==`, `!=`, `&&`, `||`). A `run` step with an `id` that writes
  `$GITHUB_OUTPUT` or `$GITHUB_ENV` runs (bash, as GitHub runs it) so later steps can read its outputs.
- The `release-notes` step runs the kit source: a copy of `skill/` at `.agents/skills/dev-loop` of the
  scratch repo, as the tagged commit holds its own installed kit.
- The release step of O35 runs with stub `python3`, `python` and `git` on `PATH` and a stub
  `scripts/repo_rules.py`, each logging its arguments, so the test sees whether `promoted` would run.
"""

from __future__ import annotations

import hashlib
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

from release_security_audit_support import base_env, note_text, put, record_line

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
REPO_RULES = REPO_ROOT / "scripts" / "repo_rules.py"
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"
RELEASE_MD = REPO_ROOT / "reference" / "release.md"
SKILL = REPO_ROOT / "skill" / "SKILL.md"
AUDIT_SKILL = REPO_ROOT / "skills" / "release-audit" / "SKILL.md"
AGENTS = REPO_ROOT / "AGENTS.md"
STATUS_MD = REPO_ROOT / "reference" / "cli" / "status.md"
PLAN_MD = REPO_ROOT / "reference" / "cli" / "release-plan.md"

KIT_ROOTS = (".agents/skills/", ".claude/agents/", ".claude/skills/", ".codex/agents/", ".codex/rules/",
             ".dev-loop/hooks/")
MANIFEST = ".dev-loop/MANIFEST.json"
GIT_ID = ("-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid", "-c", "commit.gpgsign=false",
          "-c", "tag.gpgsign=false", "-c", "core.hooksPath=/dev/null")


def snapshot(root: Path) -> dict[str, str]:
    """Every path under `root` except `.git`, with its content or link target."""
    out = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not (Path(folder) == root and d == ".git")]
        for name in dirs + names:
            path = Path(folder) / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                out[rel] = "link:" + os.readlink(path)
            elif path.is_dir():
                out[rel] = "dir"
            else:
                out[rel] = path.read_bytes().hex()
    return out


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class Scratch(unittest.TestCase):
    """A temporary root with a sandboxed HOME and TMPDIR."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.tmp = self.root / "tmp"
        for folder in (self.home, self.tmp):
            folder.mkdir()
        self.env = base_env(self.home, self.tmp)

    def git(self, cwd: Path, *args: str, check: bool = True) -> str:
        result = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def new_repo(self, name: str) -> Path:
        repo = self.root / name
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        return repo

    def commit(self, repo: Path, files: dict[str, str | None], message: str) -> str:
        """Write (or, for None, delete) each file, then commit them all."""
        for path, text in files.items():
            target = repo / path
            if text is None:
                if target.is_symlink() or target.exists():
                    target.unlink()
            else:
                put(target, text)
        self.git(repo, "add", "-A", "--", *files)
        self.git(repo, "commit", "-q", "--allow-empty", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def tag(self, repo: Path, name: str, rev: str = "HEAD") -> None:
        self.git(repo, "tag", "-a", "-m", f"dev-loop {name}", name, rev)


# ---------------------------------------------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------------------------------------------

class InstallerCase(Scratch):
    """Copies of the package at given versions, and a git project to install into."""

    def package(self, version: str, changelog: str | None = None) -> Path:
        pkg = self.root / f"pkg-{uuid.uuid4().hex[:8]}"
        for name in ("bin", "lib", "skill", "skills", "claude", "codex"):
            shutil.copytree(REPO_ROOT / name, pkg / name, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        data = json.loads((REPO_ROOT / "package.json").read_text())
        data["version"] = version
        put(pkg / "package.json", json.dumps(data, indent=2) + "\n")
        if changelog is not None:
            put(pkg / "CHANGELOG.md", changelog)
        return pkg

    def project(self) -> Path:
        return self.new_repo(f"project-{uuid.uuid4().hex[:8]}")

    def install(self, pkg: Path, command: str, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["node", str(pkg / "bin" / "install.mjs"), command, *args], cwd=repo, env=self.env,
                              capture_output=True, text=True, timeout=300)

    def init_at(self, version: str, repo: Path | None = None) -> Path:
        repo = repo or self.project()
        result = self.install(self.package(version), "init", repo, "--yes", "--harness=claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return repo

    def upgrade_to(self, repo: Path, version: str, changelog: str | None = None) -> subprocess.CompletedProcess[str]:
        return self.install(self.package(version, changelog), "upgrade", repo)

    def versions(self, repo: Path) -> tuple[str, str]:
        config = json.loads((repo / "dev-loop.json").read_text())
        manifest = json.loads((repo / MANIFEST).read_text())
        return config.get("version"), manifest.get("version")


# ---------------------------------------------------------------------------------------------------------------
# Audit exclusion: a project with kit copies, a MANIFEST and a notes folder
# ---------------------------------------------------------------------------------------------------------------

K1 = ".agents/skills/dev-loop/SKILL.md"
K2 = ".agents/skills/dev-loop/scripts/status.py"
K3 = ".agents/skills/pause/SKILL.md"
K4 = ".agents/skills/garden/SKILL.md"
NOTES_NAME = "Acme Widgets"


class AuditCase(Scratch):
    """Commit A: the kit at 3.0.0-rc.1 (copies K1 to K4 in the MANIFEST), `src/app.py`, `dev-loop.json` and the
    merged config files. `upgrade_commit` makes commit B on top of it."""

    def setUp(self) -> None:
        super().setUp()
        self.notes = self.root / "notes" / NOTES_NAME
        self.notes.mkdir(parents=True)
        self.env["DEV_LOOP_NOTES"] = str(self.notes)
        self.repo = self.new_repo("acme-widgets")
        self.kit = {K1: "# dev-loop skill one\n", K2: "STAGE = 1\n", K3: "# pause one\n", K4: "# garden one\n"}
        self.A = self.commit(self.repo, {
            "dev-loop.json": self.config("3.0.0-rc.1"), "README.md": "# Acme widgets\n",
            "src/app.py": "def colour():\n    return 'red'\n", ".claude/settings.json": '{"hooks": {}}\n',
            ".codex/hooks.json": '{"hooks": {}}\n', **self.kit,
            MANIFEST: self.manifest("3.0.0-rc.1", {p: sha(t) for p, t in self.kit.items()}),
        }, "A: kit 3.0.0-rc.1")

    @staticmethod
    def config(version: str) -> str:
        return json.dumps({"version": version, "harnesses": ["claude", "codex"]}, indent=2) + "\n"

    @staticmethod
    def manifest(version: str, files: dict[str, str]) -> str:
        return json.dumps({"version": version, "files": dict(sorted(files.items()))}, indent=2) + "\n"

    def upgrade_commit(self, version: str = "3.0.0-rc.2", extra: dict[str, str | None] | None = None,
                       entries: dict[str, str | None] | None = None, manifest: bool = True) -> str:
        """Commit B: K1 and K2 changed and listed with their new hashes, MANIFEST `version`, `src/app.py` and
        `dev-loop.json` changed. `extra`: more files to write or delete; `entries`: MANIFEST entries to set
        (None: drop); `manifest` False: delete the MANIFEST."""
        new = {K1: "# dev-loop skill two\n", K2: "STAGE = 2\n"}
        kit = {**self.kit, **new}
        listed = {p: sha(t) for p, t in kit.items()}
        for path, value in (entries or {}).items():
            if value is None:
                listed.pop(path, None)
            else:
                listed[path] = value
        files: dict[str, str | None] = {**new, "src/app.py": "def colour():\n    return 'blue'\n",
                                        "dev-loop.json": self.config(version)}
        files.update(extra or {})
        files[MANIFEST] = self.manifest(version, listed) if manifest else None
        return self.commit(self.repo, files, f"B: upgrade to {version}")

    def record_at(self, commit: str, run: int = 1) -> None:
        folder = self.notes / "Security Audit" / f"Run {run}"
        put(folder / "run-metadata.json", json.dumps({"source_ref": {"commit": commit}, "profile": "quick",
                                                      "scope_paths": [], "run_status": "complete"}) + "\n")
        put(self.notes / "Project Note.md", note_text([record_line(run, commit)], []))

    def no_record(self) -> None:
        put(self.notes / "Project Note.md", note_text(None, []))

    def devloop(self, *args: str) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = {"unparsed_stdout": result.stdout}
        if not isinstance(payload, dict):
            payload = {"unparsed_stdout": result.stdout}
        payload.setdefault("_stderr", result.stderr)
        return result.returncode, payload

    def plan(self) -> dict:
        code, payload = self.devloop("release-plan")
        self.assertEqual(code, 0, payload)
        self.assertIn("exclude", payload)
        self.assertIsInstance(payload["exclude"], list, payload)
        return payload


# ---------------------------------------------------------------------------------------------------------------
# release.yml: Ruby YAML load, GitHub expressions, step runs
# ---------------------------------------------------------------------------------------------------------------

def load_workflow() -> dict:
    ruby = shutil.which("ruby")
    if not ruby:
        raise AssertionError("ruby not on PATH: the release.yml tests read the workflow with Ruby's YAML loader")
    result = subprocess.run([ruby, "-ryaml", "-rjson", "-e", "print JSON.generate(YAML.safe_load(STDIN.read))"],
                            input=RELEASE_YML.read_text(), capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"release.yml does not parse: {result.stderr}")
    return json.loads(result.stdout)


TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+(?:\.\d+)?)|(?P<op>==|!=|&&|\|\||<=|>=|[!()<>,])"
                   r"|(?P<id>[A-Za-z_][\w-]*(?:\.[\w*-]+)*))")


def as_text(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


def truthy(value: object) -> bool:
    return value not in (None, False, "", 0)


class Expression:
    """The GitHub expression subset the release workflow may use."""

    def __init__(self, text: str, context: dict) -> None:
        self.text, self.context, self.tokens, self.at = text, context, [], 0
        pos = 0
        while pos < len(text):
            if not text[pos:].strip():
                break
            match = TOKEN.match(text, pos)
            if not match or match.end() == pos:
                raise AssertionError(f"expression the test cannot read: {text!r}")
            kind = match.lastgroup
            self.tokens.append((kind, match.group(kind)))
            pos = match.end()

    def value(self) -> object:
        out = self.or_()
        if self.at != len(self.tokens):
            raise AssertionError(f"expression the test cannot read: {self.text!r}")
        return out

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self, op: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None or (op is not None and token != ("op", op)):
            raise AssertionError(f"expression the test cannot read: {self.text!r}")
        self.at += 1
        return token

    def or_(self) -> object:
        left = self.and_()
        while self.peek() == ("op", "||"):
            self.take()
            right = self.and_()
            left = left if truthy(left) else right
        return left

    def and_(self) -> object:
        left = self.eq()
        while self.peek() == ("op", "&&"):
            self.take()
            right = self.eq()
            left = right if truthy(left) else left
        return left

    def eq(self) -> object:
        left = self.not_()
        while self.peek() in (("op", "=="), ("op", "!=")):
            op = self.take()[1]
            right = self.not_()
            same = as_text(left).lower() == as_text(right).lower()
            left = same if op == "==" else not same
        return left

    def not_(self) -> object:
        if self.peek() == ("op", "!"):
            self.take()
            return not truthy(self.not_())
        return self.primary()

    def primary(self) -> object:
        kind, text = self.take()
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text) if "." in text else int(text)
        if (kind, text) == ("op", "("):
            out = self.or_()
            self.take(")")
            return out
        if kind != "id":
            raise AssertionError(f"expression the test cannot read: {self.text!r}")
        if self.peek() == ("op", "("):
            self.take()
            args = []
            while self.peek() != ("op", ")"):
                args.append(self.or_())
                if self.peek() == ("op", ","):
                    self.take()
            self.take(")")
            return self.call(text, args)
        if text in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[text]
        node: object = self.context
        for part in text.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return node

    def call(self, name: str, args: list) -> object:
        low = name.lower()
        if low in ("success", "always"):
            return True
        if low in ("failure", "cancelled"):
            return False
        if low == "contains":
            return as_text(args[1]).lower() in as_text(args[0]).lower()
        if low == "startswith":
            return as_text(args[0]).lower().startswith(as_text(args[1]).lower())
        if low == "endswith":
            return as_text(args[0]).lower().endswith(as_text(args[1]).lower())
        if low == "format":
            out = as_text(args[0])
            for n, arg in enumerate(args[1:]):
                out = out.replace("{%d}" % n, as_text(arg))
            return out
        raise AssertionError(f"function the test cannot evaluate: {name}")


def interpolate(text: object, context: dict) -> str:
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: as_text(Expression(m.group(1), context).value()), as_text(text))


def condition(step: dict, context: dict) -> bool:
    value = step.get("if")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    whole = re.fullmatch(r"\$\{\{(.*)\}\}", text, re.S)
    if whole and "${{" not in whole.group(1):
        return truthy(Expression(whole.group(1), context).value())
    if "${{" in text:
        return truthy(interpolate(text, context))
    return truthy(Expression(text, context).value())


def read_github_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    lines = path.read_text().splitlines() if path.exists() else []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delim = line.split("<<", 1)
            body = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                body.append(lines[i])
                i += 1
            out[name] = "\n".join(body)
        elif "=" in line:
            name, value = line.split("=", 1)
            out[name] = value
        i += 1
    return out


class Workflow:
    """The release job of release.yml, evaluated for one tag."""

    def __init__(self) -> None:
        data = load_workflow()
        jobs = data.get("jobs") or {}
        self.job = None
        for job in jobs.values():
            if any("release-action" in str(s.get("uses", "")) for s in job.get("steps") or []):
                self.job = job
        if self.job is None:
            raise AssertionError("no job of release.yml has the ncipollo/release-action step")
        self.steps: list[dict] = self.job["steps"]
        self.top_env = {**(data.get("env") or {}), **(self.job.get("env") or {})}

    def index(self, predicate) -> list[int]:
        return [n for n, step in enumerate(self.steps) if predicate(step)]

    @staticmethod
    def is_cliff(step: dict) -> bool:
        return "git-cliff" in str(step.get("uses", "")) or bool(re.search(r"git[ -]cliff", str(step.get("run", ""))))

    @staticmethod
    def is_promoted(step: dict) -> bool:
        run = str(step.get("run", ""))
        return "repo_rules.py" in run and "promoted" in run

    @staticmethod
    def is_release(step: dict) -> bool:
        return "release-action" in str(step.get("uses", ""))

    def context(self, tag: str, cwd: Path, env: dict[str, str], upto: int) -> tuple[dict, dict[str, str]]:
        """The expression context and process env after the output steps before step `upto` ran."""
        context: dict = {"github": {"ref_name": tag, "ref": f"refs/tags/{tag}", "ref_type": "tag",
                                    "event_name": "push", "repository": "tayebchlyah/heldout-dev-loop"},
                         "env": {}, "steps": {}, "secrets": {"GITHUB_TOKEN": ""}}
        proc = {**env, "GITHUB_REF_NAME": tag, "GITHUB_REF": f"refs/tags/{tag}", "GITHUB_REF_TYPE": "tag",
                "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "tayebchlyah/heldout-dev-loop", "GITHUB_ACTIONS": "true",
                "CI": "true"}
        for key, value in self.top_env.items():
            proc[key] = context["env"][key] = interpolate(value, context)
        for step in self.steps[:upto]:
            if not condition(step, context):
                continue
            run = str(step.get("run", ""))
            outputs: dict[str, str] = {}
            if step.get("id") and run and ("GITHUB_OUTPUT" in run or "GITHUB_ENV" in run) \
                    and not self.is_promoted(step) and not self.is_cliff(step):
                out_file, env_file = cwd.parent / f"out-{uuid.uuid4().hex}", cwd.parent / f"env-{uuid.uuid4().hex}"
                step_env = {**proc, "GITHUB_OUTPUT": str(out_file), "GITHUB_ENV": str(env_file)}
                for key, value in (step.get("env") or {}).items():
                    step_env[key] = interpolate(value, context)
                result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                                         interpolate(run, context)], cwd=cwd, env=step_env, capture_output=True,
                                        text=True, timeout=60)
                if result.returncode != 0:
                    raise AssertionError(f"step {step.get('name')} failed for {tag}: {result.stdout}{result.stderr}")
                outputs = read_github_file(out_file)
                for key, value in read_github_file(env_file).items():
                    proc[key] = context["env"][key] = value
            if step.get("id"):
                context["steps"][step["id"]] = {"outputs": outputs, "outcome": "success", "conclusion": "success"}
        return context, proc


def headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("## ")]


def section_of(text: str, heading_prefix: str) -> str:
    lines = text.splitlines()
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = line.startswith(heading_prefix)
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def paragraphs(text: str) -> list[str]:
    """Blank-line separated blocks, each list item its own block."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        out.extend(re.split(r"\n(?=\s*(?:[-*]|\d+\.) )", block))
    return out

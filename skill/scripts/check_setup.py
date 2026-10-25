#!/usr/bin/env python3
"""Validate harness walls and dependencies required by dev-loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Mapping

# Write no __pycache__ into the skill folder: in a project it is a vendored kit folder.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
from board import store, writer  # noqa: E402
from notes_path import NotesPathError, resolve, worktrees  # noqa: E402
import devloop  # noqa: E402
import second_review  # noqa: E402
from verify import check as check_verify, gh_line  # noqa: E402


CODEX_SKILLS = ["grilling", "domain-modeling", "codebase-design", "code-review", "tdd", "diagnosing-bugs", "pr", "prototype"]
# D16: the Superpowers skills that Pocock grilling, tdd, diagnosing-bugs and pr replace, denied per project.
PROJECT_SKILL_DENIES = [
    "Skill(superpowers:test-driven-development)",
    "Skill(superpowers:systematic-debugging)",
    "Skill(superpowers:brainstorming)",
    "Skill(superpowers:finishing-a-development-branch)",
]
COMMAND_KEYS = ("gate", "stack_up", "stack_reset", "acceptance", "acceptance_failed")
MERGE_VALUES = ("auto", "human")
EXACT_PIN = re.compile(r"^mutmut==\d+(\.\d+)*$")
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
STRYKER_CONFIGS = [
    f"stryker.{kind}.{ext}" for kind in ("config", "conf") for ext in ("mjs", "js", "cjs", "json")
]


def load_json(path: Path, failures: list[str]) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"invalid or missing {path}: {exc}")
        return {}


def load_toml(path: Path, failures: list[str]) -> tuple[dict, str]:
    try:
        raw = path.read_text()
        return tomllib.loads(raw), raw
    except (OSError, tomllib.TOMLDecodeError) as exc:
        failures.append(f"invalid or missing {path}: {exc}")
        return {}, ""


def require_file(path: Path, failures: list[str]) -> None:
    if not path.is_file():
        failures.append(f"missing {path}")


def require_text(raw: str, token: str, label: str, failures: list[str]) -> None:
    if token not in raw:
        failures.append(f"missing {label}: {token}")


def uses_dev_loop(repo: Path) -> bool:
    return (repo / "dev-loop.json").is_file()


def check_notes(home: Path, repo: Path, env: Mapping[str, str] | None = None) -> list[str]:
    try:
        resolve(repo, os.environ if env is None else env, home)
    except NotesPathError as exc:
        return [re.sub(r"^no notes path", "missing notes path", str(exc))]
    return []


def dataview_ready(root: Path) -> bool:
    try:
        plugins = json.loads((root / ".obsidian" / "community-plugins.json").read_text())
        data = json.loads((root / ".obsidian" / "plugins" / "dataview" / "data.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(plugins, list) and "dataview" in plugins and isinstance(data, dict) and data.get("enableDataviewJs") is True


def check_board_vault(home: Path, repo: Path, env: Mapping[str, str] | None = None) -> list[str]:
    """D24, D34: user actions for the board note in an Obsidian vault. Nothing when the notes path does not resolve."""
    try:
        notes = resolve(repo, os.environ if env is None else env, home)
    except NotesPathError:
        return []
    root = store.root_of(Path(notes))
    actions = []
    try:
        local = json.loads((worktrees(repo)[1] / "dev-loop.local.json").read_text())
    except (OSError, ValueError):
        local = {}
    if not isinstance(local, dict):
        local = {}
    if local.get("board_dataview") is not False and (root / ".obsidian").is_dir() and not dataview_ready(root):
        actions.append("Install the Dataview plugin and turn on JavaScript queries (Dataview settings) for the board note")
    if local.get("board_vault_git") != "shared" and (root / ".git").exists():
        try:
            ignored = subprocess.run(
                ["git", "check-ignore", "-q", "--no-index", f"{Path(notes).resolve().relative_to(root)}/dev-loop-board/"],
                cwd=root,
                capture_output=True,
                check=False,
            ).returncode
        except OSError:
            ignored = 0
        if ignored == 1:
            actions.append("add dev-loop-board/ to the vault .gitignore, unless a partner receives the notes by git")
    # D24: `board` keeps a note at the kit version whatever its body, so a planted body is the user's to delete.
    note = Path(notes) / "dev-loop-board" / store.BOARD_NOTE
    kit = writer.kit_version(repo)
    if note.is_file() and not note.is_symlink():
        have = (store.read_note(note) or {}).get("version")
        if store.version_key(have) == store.version_key(kit) and note.read_text() != writer.board_text(kit):
            actions.append(f"{store.BOARD_NOTE} differs from the dev-loop template: delete it, the next board run writes it again")
    return actions


def check_second_review(repo: Path, harness: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """D2 to D4: the project allows a second reviewer at the main ref, so the user must choose one or turn it off."""
    ref = devloop.main_ref(repo)
    if ref is None:
        return [], []
    # project_lock reads the ref with git: a replace ref must not change what it shows.
    had = os.environ.get("GIT_NO_REPLACE_OBJECTS")
    os.environ["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        lock = second_review.project_lock(repo, ref)
    finally:
        if had is None:
            del os.environ["GIT_NO_REPLACE_OBJECTS"]
        else:
            os.environ["GIT_NO_REPLACE_OBJECTS"] = had
    if lock is None:
        return [f"second_review in dev-loop.json at {ref} must be true or false"], []
    if not lock:
        return [], []
    path = second_review.main_worktree(repo) / "dev-loop.local.json"
    reviewer = second_review.REVIEWER[harness]
    bad = [f"second_review in {path} is not valid: see D4 (false, or an object keyed by claude or codex)"]
    value: object = {}
    if path.exists():
        try:
            local = second_review.load_unique(path.read_bytes())
        except (OSError, ValueError):
            return bad, []
        if not isinstance(local, dict):
            return bad, []
        value = local.get("second_review", {})
    status, _ = second_review.local_entry(value, reviewer)
    if status == "bad-config":
        return bad, []
    if status != "not-chosen":
        return [], []
    heading = (f'Choose the {reviewer} second reviewer: write "second_review": {{"{reviewer}": {{"model": "<model>"}}}} '
               f'(or {{}} for the CLI default model) or "second_review": false to {path}')
    return [], [(heading, [])]


def check_project_config(repo: Path) -> list[str]:
    """The optional `commands` map and `merge` value of dev-loop.json."""
    failures: list[str] = []
    config = load_json(repo / "dev-loop.json", failures)
    if not isinstance(config, dict):
        return failures + ["dev-loop.json must hold a JSON object"]
    commands = config.get("commands", {})
    if not isinstance(commands, dict):
        return failures + ["commands in dev-loop.json must be an object"]
    for key, value in commands.items():
        if key not in COMMAND_KEYS:
            failures.append(f"unknown commands key in dev-loop.json: {key}")
        elif not isinstance(value, str) or not value.strip():
            failures.append(f"commands.{key} in dev-loop.json must be a non-empty string")
    if config.get("merge", "auto") not in MERGE_VALUES:
        failures.append("merge in dev-loop.json must be auto or human")
    paths = config.get("merge_human_paths")
    if "merge_human_paths" in config and not (
        isinstance(paths, list)
        and all(isinstance(item, str) and item.strip() and not item.endswith("/") for item in paths)
    ):
        failures.append("merge_human_paths in dev-loop.json must be a list of non-empty strings")
    return failures


GATE_SCRIPT_CALL = re.compile(r"(?:^|\s)(?:\./)?scripts/gate\.sh(?:\s|$)")
GIT_CALL = re.compile(r"(?:^|[;&|({]|\$\()\s*(?:(?:if|!|then|while|until|exec|command|sudo|env)\s+)*git\b")
GUARD_PARTS = [
    re.compile(r"\bexport\b"),
    re.compile(r"GIT_CONFIG_KEY_[^\s=]+=[\"']?core\.fsmonitor\b"),
    re.compile(r"GIT_CONFIG_VALUE_[^\s=]+=[\"']?false\b"),
    re.compile(r"GIT_CONFIG_COUNT="),
]
GUARD_MISSING = (
    "scripts/gate.sh lacks the core.fsmonitor guard: export core.fsmonitor=false through "
    "GIT_CONFIG_COUNT, GIT_CONFIG_KEY_<n> and GIT_CONFIG_VALUE_<n>, before its first git call"
)
GUARD_AFTER_GIT = (
    "scripts/gate.sh runs git before the core.fsmonitor guard: move the GIT_CONFIG_* export above its first git call"
)


def check_gate_guard(repo: Path) -> list[str]:
    """A project scripts/gate.sh disables core.fsmonitor through GIT_CONFIG_* before its first git call.

    Skipped when commands.gate maps a command that does not run scripts/gate.sh."""
    gate = repo / "scripts" / "gate.sh"
    config = load_json(repo / "dev-loop.json", [])
    commands = config.get("commands") if isinstance(config, dict) else None
    mapped = commands.get("gate") if isinstance(commands, dict) else None
    if (isinstance(mapped, str) and mapped and not GATE_SCRIPT_CALL.search(mapped)) or not gate.is_file():
        return []
    lines = [line for line in gate.read_text(errors="replace").splitlines() if not line.lstrip().startswith("#")]
    guard = next((i for i, line in enumerate(lines) if all(part.search(line) for part in GUARD_PARTS)), None)
    if guard is None:
        return [GUARD_MISSING]
    if any(GIT_CALL.search(line) for line in lines[:guard]):
        return [GUARD_AFTER_GIT]
    return []


MIN_VERSIONS = {
    "claude": ((2, 1, 295), "Claude Code {found} is older than 2.1.295 (onFailure needs it). Update Claude Code."),
    "codex": ((0, 162, 1), "codex-cli {found} is older than 0.162.1 (hooks need agent_type). Update Codex."),
}
KIT_GUARDS = (".dev-loop/hooks/claude/git-guard.sh", ".dev-loop/hooks/claude/deny-acceptance-to-subagents.sh")
WALLED_AGENTS = ("implementer", "spec-tester")


def check_version(harness: str) -> list[str]:
    """D11: the harness binary on PATH must be new enough for the seal hooks."""
    try:
        out = subprocess.run([harness, "--version"], capture_output=True, text=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"cannot read the {harness} version: {exc}"]
    match = re.search(r"\d+(?:\.\d+)+", out)
    if not match:
        return [f"cannot read the {harness} version: no version in {out.strip()!r}"]
    found = match.group()
    minimum, message = MIN_VERSIONS[harness]
    return [message.format(found=found)] if tuple(map(int, found.split("."))) < minimum else []


def check_on_failure(repo: Path) -> list[str]:
    """D8: each kit guard hook, in the settings and in the agent frontmatter, carries onFailure: block."""
    missing = []
    settings = load_json(repo / ".claude" / "settings.json", [])
    for entry in settings.get("hooks", {}).get("PreToolUse", []):
        for hook in entry.get("hooks", []):
            command = hook.get("command", "")
            if any(guard in command for guard in KIT_GUARDS) and hook.get("onFailure") != "block":
                missing.append(command)
    for name in WALLED_AGENTS:
        path = repo / ".claude" / "agents" / f"{name}.md"
        text = path.read_text(errors="replace") if path.is_file() else ""
        for block in re.split(r"(?m)^\s*- type:", text.partition("\n---\n")[0])[1:]:
            command = re.search(r"command:\s*(.+)", block)
            if command and "deny-path.sh" in command.group(1) and not re.search(r"onFailure:\s*[\"']?block[\"']?\s*$", block, re.M):
                missing.append(command.group(1).strip().strip("'\""))
    return [f'missing onFailure: "block" on {command}' for command in missing]


def check_claude_settings(path: Path, failures: list[str]) -> None:
    settings = load_json(path, failures)
    hooks = settings.get("hooks", {})
    for event in ["SessionStart", "PreToolUse", "Stop", "SubagentStart", "SubagentStop"]:
        if event not in hooks:
            failures.append(f"missing Claude hook event: {event}")
    for event, matcher, script in [("PreToolUse", "AskUserQuestion", "deny-ask.sh"), ("StopFailure", "rate_limit", "pause.sh")]:
        groups = hooks.get(event, [])
        if not any(isinstance(g, dict) and g.get("matcher") == matcher
                   and any(script in str(h.get("command", "")) for h in g.get("hooks", []) if isinstance(h, dict))
                   for g in groups if isinstance(groups, list)):
            failures.append(f"missing Claude hook: {event} {matcher} {script}")
    denies = settings.get("permissions", {}).get("deny", [])
    for rule in [
        "Skill(superpowers:verification-before-completion)",
        "Read(./.env)",
        "Read(./.env.*)",
        *PROJECT_SKILL_DENIES,
    ]:
        if rule not in denies:
            failures.append(f"missing Claude deny: {rule}")


def check_claude_repo(repo: Path) -> list[str]:
    """The repo walls a dev-loop repo needs in its .claude/settings.json."""
    failures: list[str] = []
    if uses_dev_loop(repo):
        repo_settings = load_json(repo / ".claude" / "settings.json", failures)
        repo_denies = repo_settings.get("permissions", {}).get("deny", [])
        for agent in [
            "Agent(caveman:cavecrew-reviewer)",
            "Agent(caveman:cavecrew-builder)",
            "Agent(caveman:cavecrew-investigator)",
        ]:
            if agent not in repo_denies:
                failures.append(f"missing Claude repo deny: {agent}")
        repo_env = repo_settings.get("env", {})
        for variable, setting in [
            ("ENABLE_STOP_REVIEW", "0"),
            ("ENABLE_COMMIT_REVIEW", "0"),
            ("SG_PUSH_SWEEP", "0"),
            ("CLAUDE_CODE_ENABLE_TODO_TOOLS", "1"),
        ]:
            if repo_env.get(variable) != setting:
                failures.append(f"missing Claude repo env: {variable}={setting}")
    return failures


def check_codex_root(root: Path, rules_path: Path) -> list[str]:
    """Config, hooks, agents and rules of the project .codex folder."""
    failures: list[str] = []
    config, raw_config = load_toml(root / "config.toml", failures)
    if "sandbox_mode" in raw_config:
        failures.append("legacy sandbox_mode disables permission-profile enforcement")
    if config.get("features", {}).get("hooks") is not True:
        failures.append("Codex hooks are not enabled")

    profile_name = config.get("default_permissions")
    profiles = config.get("permissions", {})
    profile = profiles.get(profile_name, {}) if isinstance(profile_name, str) else {}
    if not profile_name or profile.get("extends") != ":workspace":
        failures.append("missing Codex workspace permission profile")
    workspace_rules = profile.get("filesystem", {}).get(":workspace_roots", {})
    for pattern in ["**/.env", "**/.env.*"]:
        if workspace_rules.get(pattern) != "deny":
            failures.append(f"missing Codex filesystem deny: {pattern}")

    hooks = load_json(root / "hooks.json", failures).get("hooks", {})
    for event in ["SessionStart", "PreToolUse", "Stop", "SubagentStart", "SubagentStop"]:
        if event not in hooks:
            failures.append(f"missing Codex hook event: {event}")

    for filename in ["implementer.toml", "spec-tester.toml", "task-reviewer.toml", "final-reviewer.toml", "security-reviewer.toml"]:
        path = root / "agents" / filename
        require_file(path, failures)
        if path.is_file():
            _, raw = load_toml(path, failures)
            for field in ["name", "description", "developer_instructions"]:
                require_text(raw, field, f"{filename} field", failures)

    require_file(rules_path, failures)
    if rules_path.is_file():
        rules = rules_path.read_text()
        require_text(rules, '"/usr/bin/git"', "absolute git executable rule", failures)
        require_text(rules, '["git", "-C"]', "git -C rule", failures)
        require_text(rules, 'decision="prompt"', "git push prompt", failures)
        require_text(rules, '"reset", "--hard"', "hard reset rule", failures)
        require_text(rules, 'decision="forbidden"', "forbidden command decision", failures)
        require_text(rules, "match=", "command rule inline tests", failures)
    return failures


def codex_skill_groups(home: Path) -> list[tuple[str, list[str]]]:
    skill_failures: list[str] = []
    for skill in CODEX_SKILLS:
        path = home / ".agents" / "skills" / skill / "SKILL.md"
        if not path.is_file():
            skill_failures.append(f"missing {path}")
    if not skill_failures:
        return []
    return [(f"Install these skills from their own source: {', '.join(CODEX_SKILLS)}.", skill_failures)]


MANIFEST = ".dev-loop/MANIFEST.json"
LINK_PREFIX = "link:"


def safe_kit_path(repo: Path, key: str) -> Path | None:
    """The kit path for a MANIFEST.json key, or None when the key could leave the repo."""
    segments = key.split("/")
    if key.startswith("/") or "\\" in key or "\0" in key or any(s in ("", ".", "..") for s in segments):
        return None
    path = repo / key
    if not path.parent.resolve().is_relative_to(repo.resolve()):
        return None
    return path


def kit_entry_state(path: Path, expected: object) -> str:
    """same, changed or missing. A link is read, never followed."""
    if not os.path.lexists(path):
        return "missing"
    if not isinstance(expected, str):
        return "changed"
    if expected.startswith(LINK_PREFIX):
        same = path.is_symlink() and os.readlink(path) == expected[len(LINK_PREFIX):]
        return "same" if same else "changed"
    if path.is_symlink() or not path.is_file():
        return "changed"
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return "same" if digest == expected else "changed"


def check_manifest(repo: Path) -> list[str]:
    try:
        files = json.loads((repo / MANIFEST).read_text()).get("files")
    except (OSError, ValueError, AttributeError):
        files = None
    if not isinstance(files, dict):
        return [f"missing or invalid {MANIFEST}"]
    failures: list[str] = []
    for key, expected in files.items():
        path = safe_kit_path(repo, key)
        if path is None:
            failures.append(f"invalid kit path in MANIFEST.json: {key}")
            continue
        state = kit_entry_state(path, expected)
        if state == "changed":
            failures.append(f"kit file changed locally: {key}. Move the change to dev-loop, then run upgrade")
        elif state == "missing":
            failures.append(f"missing kit file: {key}")
    return failures


def check_user_skill(home: Path) -> list[str]:
    return [
        f"user-level dev-loop skill found: {path}. Run npx github:tchlyah/dev-loop#<tag> uninstall-global"
        for path in [home / ".agents" / "skills" / "dev-loop", home / ".claude" / "skills" / "dev-loop"]
        if os.path.lexists(path)
    ]


def main_checkout(repo: Path) -> str:
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return str(Path(common.stdout.strip()).parent) if common.returncode == 0 else str(repo.resolve())


def snake_case(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def untrusted_hook_events(config: dict, repo: Path, main: str) -> list[str]:
    """The project hook events with a hook entry lacking a trusted hash in ~/.codex/config.toml: Codex skips it without a message. Codex's hash method is unknown, so only the sha256: prefix is checked."""
    hooks_file = f"{main}/.codex/hooks.json"
    states = config.get("hooks", {}).get("state", {})
    events = load_json(repo / ".codex" / "hooks.json", []).get("hooks", {})

    def trusted(event: str, i: int, j: int) -> bool:
        value = states.get(f"{hooks_file}:{event}:{i}:{j}")
        return isinstance(value, dict) and str(value.get("trusted_hash", "")).startswith("sha256:")

    return [
        snake_case(event) for event, groups in events.items()
        if not all(trusted(snake_case(event), i, j) for i, group in enumerate(groups) for j in range(len(group.get("hooks", []))))
    ]


def project_trust_groups(home: Path, repo: Path) -> list[tuple[str, list[str]]]:
    """D14: the trust Codex records in ~/.codex/config.toml once the user trusts the project."""
    main = main_checkout(repo)
    config, _ = load_toml(home / ".codex" / "config.toml", [])
    trust_failures: list[str] = []
    if config.get("projects", {}).get(main, {}).get("trust_level") != "trusted":
        trust_failures.append(f'missing trusted Codex project: [projects."{main}"] trust_level = "trusted"')
    trust_failures += [f"missing trusted Codex hook hash: {event}" for event in untrusted_hook_events(config, repo, main)]
    return [("Trust the project, then run /hooks in Codex.", trust_failures)] if trust_failures else []


SECURITY_AUDIT_COMMIT = "c1c8a8c1471069fb0e188eeaff69b8e8db6564a8"
SECURITY_AUDIT_TREE = "ccbc33ed119b28c3ef7b44a7e2081ab191ad63af"
SECURITY_AUDIT_INSTALL = (
    f"npx skills add https://github.com/cloudflare/security-audit-skill/tree/{SECURITY_AUDIT_COMMIT} "
    "--skill security-audit --global --agent claude-code codex"
)


def tree_id(folder: Path) -> str | None:
    """The git tree id of the folder's files: the Skills CLI lock's skillFolderHash."""
    with tempfile.TemporaryDirectory() as tmp:
        git = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.attributesFile=/dev/null",
               f"--git-dir={tmp}/g", f"--work-tree={folder}"]
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env |= {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        try:
            subprocess.run(["git", "init", "-q", "--bare", f"{tmp}/g"], check=True, capture_output=True, env=env)
            # Finder litter is not skill content
            subprocess.run([*git, "add", "-A", "-f", ".", ":(exclude,glob)**/.DS_Store"],
                           check=True, capture_output=True, env=env)
            out = subprocess.run([*git, "write-tree"], check=True, capture_output=True, text=True, env=env)
        except (subprocess.CalledProcessError, OSError):
            return None
        return out.stdout.strip()


def security_audit_groups(home: Path, harness: str, env: Mapping[str, str]) -> list[tuple[str, list[str]]]:
    """D6: the Cloudflare security-audit skill at the pinned commit, in this harness's skills folder."""
    folder = home / (".claude" if harness == "claude" else ".agents") / "skills" / "security-audit"
    if not (folder / "SKILL.md").is_file():
        item = f"missing {folder / 'SKILL.md'}"
    else:
        tree = tree_id(folder.resolve())
        if tree == (env.get("DEVLOOP_TEST_SECURITY_AUDIT_TREE") or SECURITY_AUDIT_TREE):
            return []
        item = f"{folder} is tree {tree}"
    heading = f"Install the Cloudflare security-audit skill at {SECURITY_AUDIT_COMMIT}: {SECURITY_AUDIT_INSTALL}"
    return [(heading, [item])]


def check_project(home: Path, repo: Path, harness: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """D10: the kit against its manifest, the project merged files, and no user-level skill."""
    failures = check_manifest(repo) + check_user_skill(home) + check_gate_guard(repo)
    if harness == "claude":
        check_claude_settings(repo / ".claude" / "settings.json", failures)
        return failures + check_on_failure(repo) + check_claude_repo(repo), security_audit_groups(home, harness, os.environ)
    root = repo / ".codex"
    failures += check_codex_root(root, root / "rules" / "dev-loop.rules")
    return failures, project_trust_groups(home, repo) + codex_skill_groups(home) + security_audit_groups(home, harness, os.environ)


def repo_files(repo: Path, name: str) -> list[Path]:
    """Tracked files with this basename, or every such file outside tool directories."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode == 0:
        return sorted(repo / p for p in listed.stdout.splitlines() if Path(p).name == name)
    skip = {".git", ".venv", "node_modules", "mutants", ".stryker-tmp"}
    return sorted(p for p in repo.rglob(name) if not skip & set(p.relative_to(repo).parts))


def is_ignored(repo: Path, path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", str(path.relative_to(repo))],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def dev_dependencies(config: dict) -> list[str]:
    groups = config.get("dependency-groups", {}).get("dev", [])
    uv_dev = config.get("tool", {}).get("uv", {}).get("dev-dependencies", [])
    optional = config.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    return [d for d in [*groups, *uv_dev, *optional] if isinstance(d, str)]


def check_mutmut(repo: Path) -> list[str]:
    failures: list[str] = []
    configs: list[tuple[Path, dict]] = []
    pinned = False
    for path in repo_files(repo, "pyproject.toml"):
        config, _ = load_toml(path, failures)
        if isinstance(config.get("tool", {}).get("mutmut"), dict):
            configs.append((path, config["tool"]["mutmut"]))
        pinned = pinned or any(EXACT_PIN.match(d.replace(" ", "")) for d in dev_dependencies(config))
    if not pinned:
        failures.append("missing mutation dev dependency: mutmut pinned as mutmut==<version>")
    if len(configs) != 1:
        failures.append(f"expected one pyproject.toml with [tool.mutmut], found {len(configs)}")
        return failures
    path, mutmut = configs[0]
    root = path.parent
    if not mutmut.get("source_paths"):
        failures.append(f"missing [tool.mutmut] source_paths in {path}")
    if not mutmut.get("only_mutate"):
        failures.append(f"missing [tool.mutmut] only_mutate list of critical modules in {path}")
    selection = mutmut.get("pytest_add_cli_args_test_selection", [])
    if not selection:
        failures.append(f"missing [tool.mutmut] pytest_add_cli_args_test_selection in {path}")
    elif any("acceptance" in str(arg) for arg in selection):
        failures.append(f"[tool.mutmut] test selection in {path} must run unit tests only")
    if not is_ignored(repo, root / "mutants" / "probe"):
        failures.append(f"mutmut working directory not ignored by git: {(root / 'mutants').relative_to(repo)}/")
    return failures


def check_stryker(repo: Path) -> list[str]:
    failures: list[str] = []
    configs = [p for name in STRYKER_CONFIGS for p in repo_files(repo, name)]
    if len(configs) != 1 or configs[0].name != "stryker.config.json":
        return [f"expected one stryker.config.json, found {', '.join(p.name for p in configs) or 'none'}"]
    root = configs[0].parent
    if not load_json(configs[0], failures).get("mutate"):
        failures.append(f"missing mutate list of critical files in {configs[0]}")
    package = load_json(root / "package.json", failures)
    dev = package.get("devDependencies", {})
    for name in ["@stryker-mutator/core", "@stryker-mutator/vitest-runner"]:
        if not EXACT_VERSION.match(str(dev.get(name, ""))):
            failures.append(f"missing mutation dev dependency: {name} pinned to an exact version in {root / 'package.json'}")
    for probe in [root / ".stryker-tmp" / "probe", root / "reports" / "mutation" / "mutation.json"]:
        if not is_ignored(repo, probe):
            failures.append(f"Stryker output not ignored by git: {probe.parent.relative_to(repo)}/")
    return failures


def check_mutation(repo: Path) -> list[str]:
    """The mutation tool setup Stage 0 owns, per declared stack."""
    agents = repo / "AGENTS.md"
    if not agents.is_file():
        return []
    text = agents.read_text()
    match = re.search(r"^stacks:\s*(.+)$", text, re.MULTILINE)
    if not uses_dev_loop(repo) or not match:
        return []
    stacks = {s.strip() for s in match.group(1).split(",")}
    failures: list[str] = []
    if "python" in stacks:
        failures += check_mutmut(repo)
    if "ui" in stacks:
        failures += check_stryker(repo)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", choices=["claude", "codex"], required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    # Codex SessionStart: one line, the untrusted project hook events joined by ", " (empty when all are trusted).
    parser.add_argument("--hooks-trust", action="store_true")
    args = parser.parse_args()

    if args.hooks_trust:
        config, _ = load_toml(args.home / ".codex" / "config.toml", [])
        print(", ".join(untrusted_hook_events(config, args.repo, main_checkout(args.repo))))
        return 0

    if not uses_dev_loop(args.repo):
        print(f"{args.harness}: FAIL")
        print(f"- no dev-loop.json in {args.repo}. Run npx github:tchlyah/dev-loop#<tag> init")
        return 1
    failures, action_groups = check_project(args.home, args.repo, args.harness)
    failures += check_version(args.harness)
    failures += check_notes(args.home, args.repo)
    failures += check_project_config(args.repo)
    second_failures, second_groups = check_second_review(args.repo, args.harness)
    failures += second_failures
    action_groups += second_groups
    failures += check_mutation(args.repo)
    failures += check_verify(args.repo)
    failures += gh_line(args.repo)
    action_groups += [(line, []) for line in check_board_vault(args.home, args.repo)]

    if failures or action_groups:
        print(f"{args.harness}: FAIL")
        for failure in failures:
            print(f"- {failure}")
        if action_groups:
            print()
            print("User actions required (expected manual steps, not install defects):")
            for heading, items in action_groups:
                print(f"- {heading} ({'; '.join(items)})" if items else f"- {heading}")
        return 1
    print(f"{args.harness}: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

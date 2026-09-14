#!/usr/bin/env python3
"""Validate harness walls and dependencies required by dev-loop."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path


CODEX_SKILLS = ["domain-modeling", "grilling", "codebase-design", "code-review", "research", "handoff"]


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


def check_config(home: Path) -> list[str]:
    failures: list[str] = []
    path = home / ".agents" / "dev-loop.config.json"
    if not path.is_file():
        failures.append(f"missing dev-loop config: {path}. Run npx github:tchlyah/dev-loop")
        return failures
    config = load_json(path, failures)
    vault = config.get("vault")
    if not vault:
        failures.append(f"missing vault in dev-loop config: {path}. Rerun npx github:tchlyah/dev-loop --vault=<path>")
        return failures
    if not Path(vault).is_dir():
        failures.append(f"vault path does not exist: {vault}. Fix vault in {path} or rerun with --vault=<path>")
    return failures


def check_claude(home: Path, repo: Path | None = None) -> list[str]:
    failures: list[str] = []
    root = home / ".claude"
    for relative in [
        "hooks/gate.sh",
        "hooks/deny-path.sh",
        "hooks/deny-acceptance-to-subagents.sh",
        "hooks/git-guard.sh",
        "agents/implementer.md",
        "agents/spec-tester.md",
    ]:
        require_file(root / relative, failures)

    settings = load_json(root / "settings.json", failures)
    hooks = settings.get("hooks", {})
    for event in ["SessionStart", "PreToolUse", "Stop", "SubagentStop"]:
        if event not in hooks:
            failures.append(f"missing Claude hook event: {event}")
    denies = settings.get("permissions", {}).get("deny", [])
    for rule in [
        "Skill(superpowers:verification-before-completion)",
        "Read(./.env)",
        "Read(./.env.*)",
    ]:
        if rule not in denies:
            failures.append(f"missing Claude deny: {rule}")

    if repo is not None and (repo / "AGENTS.md").is_file():
        agents_text = (repo / "AGENTS.md").read_text()
        if "vault_notes:" in agents_text:
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
            for variable in ["ENABLE_STOP_REVIEW", "ENABLE_COMMIT_REVIEW", "SG_PUSH_SWEEP"]:
                if repo_env.get(variable) != "0":
                    failures.append(f"missing Claude repo env: {variable}=0")
    return failures


def check_codex(home: Path) -> tuple[list[str], list[tuple[str, list[str]]]]:
    failures: list[str] = []
    root = home / ".codex"
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
    for event in ["SessionStart", "PreToolUse", "Stop", "SubagentStop"]:
        if event not in hooks:
            failures.append(f"missing Codex hook event: {event}")
    for relative in ["hooks/gate.sh", "hooks/deny-path.sh", "hooks/git-guard.sh", "hooks/session-start.sh"]:
        require_file(root / relative, failures)

    agents = {
        "implementer.toml": "dev-implementer",
        "spec-tester.toml": "dev-spec-tester",
        "task-reviewer.toml": "dev-read-only",
        "simplifier.toml": "dev-workspace",
        "final-reviewer.toml": "dev-read-only",
        "security-reviewer.toml": "dev-read-only",
    }
    for filename, permission_profile in agents.items():
        path = root / "agents" / filename
        require_file(path, failures)
        if path.is_file():
            agent_config, raw = load_toml(path, failures)
            for field in ["name", "description", "developer_instructions"]:
                require_text(raw, field, f"{filename} field", failures)
            if agent_config.get("default_permissions") != permission_profile:
                failures.append(f"wrong {filename} permission profile: expected {permission_profile}")

    required_profiles = {
        "dev-read-only": (":read-only", ["**/.env", "**/.env.*"]),
        "dev-implementer": ("dev-workspace", ["tests/acceptance", "tests/acceptance/**"]),
        "dev-spec-tester": (
            "dev-workspace",
            ["src", "src/**", "app", "app/**", "backend", "backend/**", "frontend", "frontend/**"],
        ),
    }
    for name, (parent, denied_paths) in required_profiles.items():
        role_profile = profiles.get(name, {})
        if role_profile.get("extends") != parent:
            failures.append(f"wrong Codex profile parent: {name} must extend {parent}")
        role_rules = role_profile.get("filesystem", {}).get(":workspace_roots", {})
        for path in denied_paths:
            if role_rules.get(path) != "deny":
                failures.append(f"missing Codex role deny in {name}: {path}")

    rules_path = root / "rules" / "default.rules"
    require_file(rules_path, failures)
    if rules_path.is_file():
        rules = rules_path.read_text()
        require_text(rules, '"/usr/bin/git"', "absolute git executable rule", failures)
        require_text(rules, '["git", "-C"]', "git -C rule", failures)
        require_text(rules, 'decision="prompt"', "git push prompt", failures)
        require_text(rules, '"reset", "--hard"', "hard reset rule", failures)
        require_text(rules, 'decision="forbidden"', "forbidden command decision", failures)
        require_text(rules, "match=", "command rule inline tests", failures)

    # Ruling R10: trusted-hook state and third-party skills are not something
    # any installer can write. A user who just ran the installer will see
    # these fail until they take the corresponding manual step, so they are
    # reported separately as user actions rather than mixed in with real
    # install defects.
    action_groups: list[tuple[str, list[str]]] = []

    trusted_hooks = config.get("hooks", {}).get("state", {})
    hook_trust_failures: list[str] = []
    for event in ["pre_tool_use", "session_start", "user_prompt_submit", "subagent_stop", "stop"]:
        event_entries = [
            value
            for key, value in trusted_hooks.items()
            if f":{event}:" in key and isinstance(value, dict)
        ]
        if not any(str(entry.get("trusted_hash", "")).startswith("sha256:") for entry in event_entries):
            hook_trust_failures.append(f"missing trusted Codex hook hash: {event}")
    if hook_trust_failures:
        action_groups.append(
            ("Run `/hooks` in Codex and trust the dev-loop hook definitions.", hook_trust_failures)
        )

    skill_failures: list[str] = []
    for skill in CODEX_SKILLS:
        path = home / ".agents" / "skills" / skill / "SKILL.md"
        if not path.is_file():
            skill_failures.append(f"missing {path}")
    if skill_failures:
        action_groups.append(
            (
                f"Install these skills from their own source: {', '.join(CODEX_SKILLS)}.",
                skill_failures,
            )
        )

    return failures, action_groups


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", choices=["claude", "codex"], required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()

    failures = check_config(args.home)
    action_groups: list[tuple[str, list[str]]] = []
    if args.harness == "claude":
        failures += check_claude(args.home, args.repo)
    else:
        codex_failures, action_groups = check_codex(args.home)
        failures += codex_failures

    if failures or action_groups:
        print(f"{args.harness}: FAIL")
        for failure in failures:
            print(f"- {failure}")
        if action_groups:
            print()
            print("User actions required (expected manual steps, not install defects):")
            for heading, items in action_groups:
                print(f"- {heading} ({'; '.join(items)})")
        return 1
    print(f"{args.harness}: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

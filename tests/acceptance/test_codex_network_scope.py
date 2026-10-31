#!/usr/bin/env python3
"""Held-out oracle tests for the Codex network scope (spec: Codex Network Scope).

One test per `## Acceptance flows` item (F<n>), plus one for the kit text; each docstring names the
`## Acceptance oracle` lines (O<n>) it checks. Seams: `bin/install.mjs init` and `upgrade` run as processes on
scratch projects with the Codex harness (`.codex/config.toml` parsed as TOML, the stdout lines),
`check_setup.py --harness codex` as a subprocess with a fake `codex` on `PATH`, and the text of the kit source.
The live canary (`scripts/canary.sh --codex`) needs a real Codex and Docker and is never run here.
Every name in the scratch projects is invented.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
CODEX_REF = REPO_ROOT / "skill" / "references" / "codex.md"
KIT_REF = REPO_ROOT / "reference" / "kit.md"
RELEASE_AUDIT = REPO_ROOT / "skills" / "release-audit" / "SKILL.md"
CANARY = REPO_ROOT / "scripts" / "canary.sh"

CONFIG = ".codex/config.toml"
DOCKER_COMMAND = "codex -c default_permissions=dev-docker"
MINIMUM = "0.165.0"

FEATURES = ("features",)
NETWORK = ("permissions", "dev-workspace", "network")
DOMAINS = NETWORK + ("domains",)
ENV_DENY = ("permissions", "dev-workspace", "filesystem", ":workspace_roots")
D2_HEADERS = ("[features]", "[permissions.dev-workspace.network]", "[permissions.dev-workspace.network.domains]")
D3_HOSTS = ("github.com", "*.github.com", "*.githubusercontent.com", "registry.npmjs.org", "pypi.org",
            "files.pythonhosted.org", "proxy.golang.org", "sum.golang.org", "api.anthropic.com",
            "cdn.playwright.dev", "playwright.download.prss.microsoft.com")
NETWORK_FAILURE_MARKS = ("network_proxy", "dangerously_allow_all_unix_sockets", "Codex network")

# The `.codex/config.toml` the earlier kit wrote: `[features]` with `hooks = true` only, the network table with
# `enabled = true` only, no domains table, no `dev-docker`.
EARLIER_HEAD = '''default_permissions = "dev-workspace"
model_reasoning_effort = "high"

'''
EARLIER_FEATURES = '''[features]
hooks = true
'''
EARLIER_WORKSPACE = '''
[permissions.dev-workspace]
description = "Workspace editing with environment files unreadable."
extends = ":workspace"

[permissions.dev-workspace.filesystem.":workspace_roots"]
".env" = "deny"
".env.*" = "deny"
"**/.env" = "deny"
"**/.env.*" = "deny"

'''
EARLIER_NETWORK = '''[permissions.dev-workspace.network]
enabled = true
'''
EARLIER_CONFIG = EARLIER_HEAD + EARLIER_FEATURES + EARLIER_WORKSPACE + EARLIER_NETWORK

# User tables the upgrade must leave byte for byte (O10).
USER_READ_ONLY = '''[permissions.dev-read-only]
description = "Pelican audit browsing, my own profile."
extends = ":read-only"'''
USER_AGENTS = '''[agents]
max_threads = 3
max_depth = 1'''

HEADER = re.compile(r"^\s*\[(?!\[)")


def header_path(line: str) -> tuple[str, ...]:
    """The key path a `[a.b."c"]` table header line declares."""
    data = tomllib.loads(line.strip() + "\n")
    path: list[str] = []
    while isinstance(data, dict) and len(data) == 1:
        key, data = next(iter(data.items()))
        path.append(key)
    return tuple(path)


def tables(text: str) -> list[tuple[tuple[str, ...] | None, list[str]]]:
    """The text cut at each table header: (header path or None for the root keys, lines)."""
    blocks: list[tuple[tuple[str, ...] | None, list[str]]] = [(None, [])]
    for line in text.splitlines(keepends=True):
        if HEADER.match(line):
            blocks.append((header_path(line), [line]))
        else:
            blocks[-1][1].append(line)
    return blocks


def table_text(text: str, path: tuple[str, ...]) -> str:
    """The one table declared by header `path`, header included, trailing blank lines dropped."""
    found = ["".join(lines).rstrip() for p, lines in tables(text) if p == path]
    if len(found) != 1:
        raise AssertionError(f"{len(found)} tables {path} in:\n{text}")
    return found[0]


def edit_tables(text: str, bodies: dict[tuple[str, ...], str | None], extra: str = "") -> str:
    """`text` with the body of each table in `bodies` replaced (None: table dropped), then `extra` appended."""
    out = []
    for path, lines in tables(text):
        if path in bodies:
            if bodies[path] is not None:
                out.append(lines[0] + bodies[path] + "\n")
        else:
            out.append("".join(lines))
    result = "".join(out).rstrip("\n") + "\n"
    return result + ("\n" + extra if extra else "")


def dig(data: dict, path: tuple[str, ...]):
    for key in path:
        data = data[key]
    return data


def keys_anywhere(data, names: set[str]) -> list[str]:
    """Each key of `names` found at any depth of `data`."""
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in names:
                found.append(key)
            found.extend(keys_anywhere(value, names))
    return found


def network_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if any(mark in line for mark in NETWORK_FAILURE_MARKS)]


def kept_lines(stdout: str, item: str) -> list[str]:
    return [line for line in stdout.splitlines() if "kept your own" in line and item in line]


def paragraphs(text: str) -> list[str]:
    """Blank-line separated blocks, each list item its own block."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        out.extend(re.split(r"\n(?=\s*(?:[-*]|\d+\.) )", block))
    return out


def sections(text: str) -> list[str]:
    """Each heading with its body, up to the next heading of any level."""
    return re.split(r"(?m)(?=^#{1,6} )", text)


def headed_regions(text: str, heading: re.Pattern[str]) -> list[str]:
    """Each heading matching `heading` with its body, up to the next heading of the same or a higher level."""
    regions = []
    for match in re.finditer(r"(?m)^(#{1,6}) (.*)$", text):
        if not heading.search(match.group(2)):
            continue
        level = len(match.group(1))
        end = re.compile(rf"(?m)^#{{1,{level}}} ").search(text, match.end())
        regions.append(text[match.start():end.start() if end else len(text)])
    return regions


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"missing {path.relative_to(REPO_ROOT)}")
    return path.read_text()


class CodexProjectCase(unittest.TestCase):
    """Scratch projects under one temporary root: a temp `HOME`, a notes folder, and fake `codex` CLIs."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home, self.notes, scratch = self.root / "home", self.root / "notes", self.root / "tmp"
        for folder in (self.home, self.notes / "Features", scratch):
            folder.mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX", "GIT_"))}
        self.env.update(HOME=str(self.home), TMPDIR=str(scratch), GIT_CONFIG_NOSYSTEM="1", GIT_EDITOR="true",
                        GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")

    def run_in(self, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(args, cwd=project, env=self.env, capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, f"{args}: {done.stdout[-1500:]} {done.stderr[-1500:]}")
        return done

    def init(self, name: str) -> Path:
        project = self.root / name
        project.mkdir()
        self.run_in(project, "git", "init", "-q", "-b", "main")
        self.run_in(project, "node", str(INSTALLER), "init", "--yes", "--harness=codex", f"--notes={self.notes}")
        return project

    def upgrade(self, project: Path) -> str:
        """Run `upgrade`; return its stdout."""
        return self.run_in(project, "node", str(INSTALLER), "upgrade", "--yes").stdout

    def config_text(self, project: Path) -> str:
        return (project / CONFIG).read_text()

    def config(self, project: Path) -> dict:
        text = self.config_text(project)
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise AssertionError(f"{CONFIG} does not parse: {error}\n{text}") from None

    def write_config(self, project: Path, text: str) -> None:
        (project / CONFIG).write_text(text)

    def fake_codex(self, version: str) -> Path:
        folder = self.root / f"codex-{version}"
        if not folder.is_dir():
            folder.mkdir()
            (folder / "codex").write_text(f"#!/bin/sh\necho 'codex-cli {version}'\n")
            (folder / "codex").chmod(0o755)
        return folder

    def check_setup(self, project: Path, version: str = MINIMUM) -> tuple[int, str]:
        env = {**self.env, "PATH": f"{self.fake_codex(version)}{os.pathsep}{self.env.get('PATH', '')}"}
        done = subprocess.run([sys.executable, str(CHECKER), "--harness", "codex", "--home", str(self.home),
                               "--repo", str(project)], env=env, capture_output=True, text=True, timeout=180)
        return done.returncode, done.stdout + done.stderr

    def assert_kit_network(self, config: dict) -> None:
        """O1 to O4 on a parsed `.codex/config.toml`."""
        with self.subTest(oracle=1):
            self.assertIs(config.get("features", {}).get("network_proxy"), True, config.get("features"))
            self.assertIs(config.get("features", {}).get("hooks"), True, config.get("features"))
        permissions = config.get("permissions", {})
        workspace = permissions.get("dev-workspace", {})
        network = workspace.get("network", {})
        with self.subTest(oracle=2):
            self.assertIs(network.get("enabled"), True, network)
            self.assertEqual(network.get("mode"), "full", network)
            self.assertIs(network.get("allow_local_binding"), True, network)
        with self.subTest(oracle=3):
            domains = network.get("domains", {})
            self.assertEqual({host: domains.get(host) for host in D3_HOSTS}, dict.fromkeys(D3_HOSTS, "allow"), domains)
            self.assertEqual([host for host, value in domains.items() if value == "deny"], [], domains)
        with self.subTest(oracle=4):
            docker = permissions.get("dev-docker", {})
            self.assertEqual(docker.get("extends"), "dev-workspace", docker)
            self.assertIs(docker.get("network", {}).get("dangerously_allow_all_unix_sockets"), True, docker)
            self.assertEqual(config.get("default_permissions"), "dev-workspace")
            self.assertEqual(keys_anywhere(workspace, {"unix_sockets", "dangerously_allow_all_unix_sockets"}), [],
                             workspace)


class NewProjectTests(CodexProjectCase):
    def test_f1_new_codex_project_passes_the_network_checks(self) -> None:
        """F1, O1 to O4, O11, O13, O14; with the Held-Out Seal setup version check (its O26, Codex half, as
        replaced by O19): `init` writes the network scope and `dev-docker`, `check_setup.py` has no network
        failure on it, fails the two forms that open every local socket, keeps a stricter user form quiet,
        and asks for Codex 0.165.0."""
        project = self.init("tea-shop")
        kit = self.config_text(project)
        self.assert_kit_network(self.config(project))

        with self.subTest(oracle=11):
            code, output = self.check_setup(project)
            self.assertEqual(network_lines(output), [], output)
        with self.subTest(oracle=19, codex=MINIMUM):
            self.assertNotIn(MINIMUM, output)
        with self.subTest(oracle=19, codex="0.164.0"):
            code, output = self.check_setup(project, "0.164.0")
            self.assertNotEqual(code, 0, output)
            self.assertTrue([line for line in output.splitlines() if MINIMUM in line], output)
            self.assertEqual(network_lines(output), [], output)

        with self.subTest(oracle=13):
            network = table_text(kit, NETWORK).split("\n", 1)[1]
            self.write_config(project, edit_tables(kit, {NETWORK: network + "\ndangerously_allow_all_unix_sockets = true"}))
            self.config(project)
            code, output = self.check_setup(project)
            self.assertNotEqual(code, 0, output)
            self.assertTrue([line for line in output.splitlines() if "dangerously_allow_all_unix_sockets" in line],
                            output)
        with self.subTest(oracle=14, form="network off"):
            self.write_config(project, edit_tables(kit, {NETWORK: "enabled = false", DOMAINS: None}))
            self.config(project)
            code, output = self.check_setup(project)
            self.assertEqual(network_lines(output), [], output)
        with self.subTest(oracle=14, form="stricter user values"):
            strict = edit_tables(kit, {NETWORK: 'enabled = true\nmode = "limited"\nallow_local_binding = false',
                                       DOMAINS: None},
                                 '[permissions.dev-workspace.network.domains]\n\n'
                                 '[permissions.dev-workspace.network.unix_sockets]\n"/tmp/agent.sock" = "allow"\n')
            self.write_config(project, strict)
            self.assertEqual(dig(self.config(project), DOMAINS), {})
            code, output = self.check_setup(project)
            self.assertEqual(network_lines(output), [], output)


class UpgradeTests(CodexProjectCase):
    def test_f2_upgrade_brings_the_earlier_kit_config_to_the_network_scope(self) -> None:
        """F2, O5, O6, O11, O12: the earlier kit config fails `check_setup.py` on the missing managed proxy;
        `upgrade` adds the kit keys, the domains and `dev-docker` and leaves the `.env` deny table; a second
        `upgrade` changes nothing and keeps no D2 table; `check_setup.py` then has no network failure."""
        project = self.init("tea-shop")
        self.write_config(project, EARLIER_CONFIG)

        with self.subTest(oracle=12):
            code, output = self.check_setup(project)
            self.assertEqual(code, 1, output)
            self.assertTrue([line for line in output.splitlines() if "features.network_proxy" in line], output)

        first = self.upgrade(project)
        after = self.config_text(project)
        with self.subTest(oracle=5):
            self.assert_kit_network(self.config(project))
            self.assertEqual(table_text(after, ENV_DENY), table_text(EARLIER_CONFIG, ENV_DENY))
            self.assertEqual(dig(self.config(project), ENV_DENY), dig(tomllib.loads(EARLIER_CONFIG), ENV_DENY))
        with self.subTest(oracle="5, no kept D2 key"):
            self.assertEqual([line for header in D2_HEADERS for line in kept_lines(first, header)], [], first)

        second = self.upgrade(project)
        with self.subTest(oracle=6):
            self.assertEqual(self.config_text(project), after)
            self.assertEqual([line for header in D2_HEADERS for line in kept_lines(second, header)], [], second)

        with self.subTest(oracle=11):
            code, output = self.check_setup(project)
            self.assertEqual(network_lines(output), [], output)


class OwnValuesTests(CodexProjectCase):
    def test_f3_upgrade_keeps_the_project_values_and_says_so(self) -> None:
        """F3, O7 to O10: a project with its own `network_proxy`, `mode`, domain and tables keeps each one after
        `upgrade`, gets the missing kit keys and hosts, and stdout names each kept key; a network table with
        an inline `domains` keeps it and stdout names the domains table."""
        project = self.init("tea-shop")
        own = (EARLIER_HEAD + "[features]\nhooks = true\nnetwork_proxy = false\n" + EARLIER_WORKSPACE
               + '[permissions.dev-workspace.network]\nenabled = true\nmode = "limited"\n\n'
               + '[permissions.dev-workspace.network.domains]\n"example.com" = "allow"\n\n'
               + USER_READ_ONLY + "\n\n" + USER_AGENTS + "\n")
        self.write_config(project, own)
        stdout = self.upgrade(project)
        after = self.config_text(project)
        config = self.config(project)

        with self.subTest(oracle=7):
            self.assertIs(config["features"].get("network_proxy"), False, config["features"])
            self.assertTrue(kept_lines(stdout, f"kept your own [features] network_proxy in {CONFIG}"), stdout)
        with self.subTest(oracle=8):
            network = dig(config, NETWORK)
            self.assertEqual(network.get("mode"), "limited", network)
            domains = network.get("domains", {})
            self.assertEqual(domains.get("example.com"), "allow", domains)
            self.assertEqual({host: domains.get(host) for host in D3_HOSTS}, dict.fromkeys(D3_HOSTS, "allow"), domains)
            self.assertTrue(kept_lines(stdout, "[permissions.dev-workspace.network] mode"), stdout)
        with self.subTest(oracle=10):
            self.assertEqual(table_text(after, ("permissions", "dev-read-only")), USER_READ_ONLY)
            self.assertEqual(table_text(after, ("agents",)), USER_AGENTS)

        inline = self.init("pelican-post")
        self.write_config(inline, EARLIER_HEAD + EARLIER_FEATURES + EARLIER_WORKSPACE
                          + '[permissions.dev-workspace.network]\nenabled = true\ndomains = { "example.com" = "allow" }\n')
        stdout = self.upgrade(inline)
        with self.subTest(oracle=9):
            config = self.config(inline)
            self.assertEqual(dig(config, DOMAINS), {"example.com": "allow"})
            self.assertTrue(kept_lines(stdout, "[permissions.dev-workspace.network.domains]"), stdout)


class KitTextTests(unittest.TestCase):
    def test_kit_text_names_the_network_scope_and_dev_docker(self) -> None:
        """O15 to O18: the Codex route, the kit reference, the release audit skill and the canary name the managed
        proxy, `dev-docker` and its start command, the loopback TCP case and the blocked-domain stop item."""
        with self.subTest(oracle=15):
            text = read(CODEX_REF)
            for name in ("network_proxy", "dev-docker", DOCKER_COMMAND):
                self.assertIn(name, text)
            self.assertTrue([s for s in sections(text) if re.search(r'Network access to "[^"\n]*" was blocked', s)
                             and re.search(r"\bstop", s, re.IGNORECASE)], "no blocked-domain stop item")
            self.assertTrue([p for p in paragraphs(text) if re.search(r"loopback", p, re.IGNORECASE) and "TCP" in p],
                            "no loopback TCP line")
        with self.subTest(oracle=16):
            text = read(KIT_REF)
            self.assertRegex(text, re.compile(r"managed (network )?proxy", re.IGNORECASE))
            self.assertIn("dev-docker", text)
            self.assertIn("macOS", text)
            self.assertRegex(text, re.compile(r"user[- ]layer", re.IGNORECASE))
            self.assertTrue([p for p in paragraphs(text) if re.search(r"loopback", p, re.IGNORECASE) and "TCP" in p],
                            "no loopback TCP case")
            self.assertTrue([p for p in paragraphs(text) if "SSH" in p and re.search(r"\bagent", p, re.IGNORECASE)
                             and "unix_sockets" in p], "no SSH agent unix_sockets entry")
            notes = "\n".join(headed_regions(text, re.compile(r"upgrade", re.IGNORECASE)))
            self.assertTrue([p for p in paragraphs(notes) if "network" in p
                             and re.search(r"key[- ]level|\bkeys?\b[^\n]*merg|merg[^\n]*\bkeys?\b", p, re.IGNORECASE)],
                            "no upgrade note on the key-level merge")
        with self.subTest(oracle=17):
            text = read(RELEASE_AUDIT)
            self.assertTrue([p for p in paragraphs(text) if DOCKER_COMMAND in p
                             and re.search(r"engine|docker", p.replace(DOCKER_COMMAND, ""), re.IGNORECASE)],
                            "no no-engine branch with the dev-docker restart command")
        with self.subTest(oracle=18):
            text = read(CANARY)
            self.assertIn("default_permissions=dev-docker", text)
            for name in (".env", "ls-remote", "http.server"):
                self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()

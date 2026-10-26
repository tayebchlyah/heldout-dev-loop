"""Shared constants and helpers of the skill suite: kit paths, the loaded kit modules, file and git helpers."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
# The override wins over every config file, appends to the caller's GIT_CONFIG_* entries, skips if already set.
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

SKILL_ROOT = Path(__file__).resolve().parents[3] / "skill"
# The board tests import `board` from skill/scripts and their fixtures from board_fixtures beside this file,
# never from tests/acceptance (held out, run by the acceptance runner, not by this suite).
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(1, str(Path(__file__).resolve().parent))

SKILL = SKILL_ROOT / "SKILL.md"
CHECKER = SKILL_ROOT / "scripts" / "check_setup.py"
CODEX_ASSETS = SKILL_ROOT.parent / "codex"
CLAUDE_HOOKS = SKILL_ROOT.parent / "claude" / "hooks"
SELECTOR = SKILL_ROOT / "scripts" / "select_mutants.py"
NOTES_PATH = SKILL_ROOT / "scripts" / "notes_path.py"
GUARD_ASKS = Path(__file__).resolve().parent / "fixtures" / "git-guard-asks.json"

_spec = importlib.util.spec_from_file_location("check_setup", CHECKER)
if _spec is None or _spec.loader is None:
    # spec_from_file_location does not itself validate that CHECKER exists,
    # so this guard is here only to satisfy the type checker (_spec.loader
    # is Optional[Loader]); a missing CHECKER surfaces below instead, as
    # FileNotFoundError, once exec_module actually tries to read it.
    raise ImportError(f"cannot load check_setup module: {CHECKER}")
check_setup = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(check_setup)
except FileNotFoundError as exc:
    raise FileNotFoundError(f"cannot load check_setup module: missing {CHECKER}") from exc


_selector_spec = importlib.util.spec_from_file_location("select_mutants", SELECTOR)
if _selector_spec is None or _selector_spec.loader is None:
    raise ImportError(f"cannot load select_mutants module: {SELECTOR}")
select_mutants = importlib.util.module_from_spec(_selector_spec)
sys.modules["select_mutants"] = select_mutants
_selector_spec.loader.exec_module(select_mutants)

_notes_spec = importlib.util.spec_from_file_location("notes_path", NOTES_PATH)
if _notes_spec is None or _notes_spec.loader is None:
    raise ImportError(f"cannot load notes_path module: {NOTES_PATH}")
notes_path = importlib.util.module_from_spec(_notes_spec)
sys.modules["notes_path"] = notes_path
_notes_spec.loader.exec_module(notes_path)


def all_instructions() -> str:
    parts = [SKILL.read_text()]
    refs = SKILL_ROOT / "references"
    if refs.exists():
        parts.extend(path.read_text() for path in sorted(refs.glob("*.md")))
    return "\n".join(parts)


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def checker_env(bins: Path, claude: str | None = "2.1.309 (Claude Code)", codex: str | None = "codex-cli 0.162.1",
                **extra: str) -> dict[str, str]:
    """An env whose PATH holds git and fake claude and codex binaries printing the given version text."""
    bins.mkdir(parents=True, exist_ok=True)
    if not (bins / "git").exists():
        (bins / "git").symlink_to(shutil.which("git") or "git")
    for name, text in (("claude", claude), ("codex", codex)):
        script = bins / name
        script.unlink(missing_ok=True)
        if text is not None:
            script.write_text(f"#!/bin/sh\n[ \"$1\" = --version ] || exit 2\necho '{text}'\n" if text != "FAIL" else "#!/bin/sh\nexit 1\n")
            script.chmod(0o755)
    return {**os.environ, "PATH": str(bins), **extra}


PACKAGE_ROOT = SKILL_ROOT.parent


def git_in(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_git_repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        write(repo / path, text)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "base")
    return repo


MODULE_BASE = """LIMIT = 1


def changed(x):
    return x + 1


def untouched(x):
    return x - 1


class Box:
    size = 2

    def grow(self):
        return self.size * 2

    @staticmethod
    def make():
        return 1


@register
def routed():
    return 2
"""

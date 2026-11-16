from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).parent))
from devloop import security_audit_dir  # noqa: E402


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
    folder = security_audit_dir(harness, env, home)
    if not (folder / "SKILL.md").is_file():
        item = f"missing {folder / 'SKILL.md'}"
    else:
        tree = tree_id(folder.resolve())
        if tree == (env.get("DEVLOOP_TEST_SECURITY_AUDIT_TREE") or SECURITY_AUDIT_TREE):
            return []
        item = f"{folder} is tree {tree}"
    heading = f"Install the Cloudflare security-audit skill at {SECURITY_AUDIT_COMMIT}: {SECURITY_AUDIT_INSTALL}"
    return [(heading, [item])]

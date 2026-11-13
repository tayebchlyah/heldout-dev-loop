"""Fill the Codex kit copy: python3 -I -c <this file> <block|warn|session> <kit> <source>.

Stages the MANIFEST and its D6 files from the source checkout, checks each staged
byte against the hash, then renames the staging folder to the kit folder, whose
name is the MANIFEST hash. Exit 0: the kit folder holds the copy. Exit 2: no copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile

MANIFEST = ".dev-loop/MANIFEST.json"
D6_PREFIXES = (".dev-loop/hooks/codex/", ".agents/skills/dev-loop/scripts/")


def stage(source_file: str, target: str) -> bytes:
    with open(source_file, "rb") as src:
        data = src.read()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as out:
        out.write(data)
    os.chmod(target, 0o644)
    with open(target, "rb") as back:
        return back.read()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stage_all(kit: str, source: str, staging: str) -> str | None:
    if not source or not os.path.isabs(source):
        return MANIFEST
    try:
        data = stage(os.path.join(source, MANIFEST), os.path.join(staging, MANIFEST))
        if sha(data) != os.path.basename(kit):
            return MANIFEST
        files = json.loads(data)["files"]
        entries = sorted(files.items())
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return MANIFEST
    for rel, value in entries:
        if not (isinstance(value, str) and value.startswith("sha256:") and rel.startswith(D6_PREFIXES)):
            continue
        try:
            got = stage(os.path.join(source, rel), os.path.join(staging, rel))
        except OSError:
            return rel
        if sha(got) != value[len("sha256:"):]:
            return rel
    return None


def fill(kit: str, source: str) -> str | None:
    try:
        parent = os.path.dirname(kit)
        os.makedirs(parent, exist_ok=True)
        staging = tempfile.mkdtemp(prefix=".stage-", dir=parent)
    except OSError:
        return MANIFEST
    failed = stage_all(kit, source, staging)
    if failed is None:
        try:
            os.rename(staging, kit)
            return None
        except OSError:
            if os.path.isdir(kit):
                failed = None
            else:
                failed = MANIFEST
    shutil.rmtree(staging, ignore_errors=True)
    return failed


def message(source: str, path: str) -> str:
    return (
        f"Blocked: dev-loop kit at {source} does not match the trusted hooks: {path}. "
        "Restore the kit files on main (git checkout), or run upgrade, then /hooks."
    )


def main(argv: list[str]) -> int:
    mode, kit, source = argv[0], argv[1], argv[2]
    failed = fill(kit, source)
    if failed is None:
        return 0
    text = message(source, failed)
    if mode == "warn":
        print(json.dumps({"systemMessage": text}))
    elif mode == "session":
        context = f"This repo runs the dev-loop skill. Invoke it before any other action.\n{text}"
        print(json.dumps({"systemMessage": text, "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))
    else:
        print(text, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""`audit-run promote`: copy each scratch file the local checks of an agent's result name into its `artifacts/`,
every part opened no-follow, each file bounded and checked again after the read."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

from audit import common, safefs
from audit.check import known_agent, local_artifacts, read_result

FILE_LIMIT = 256 * 1024
AGENT_LIMIT = 1024 * 1024
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Rejected(Exception):
    """One file not promoted, with a short reason."""


def read_scratch(run: Path, agent: str, rel: str, room: int) -> bytes:
    *folders, leaf = rel.split("/")
    try:
        dir_fd = safefs.walk(run, ["agents", agent, "scratch", *folders])
    except FileNotFoundError:
        raise Rejected("missing") from None
    except OSError:
        raise Rejected("link") from None
    try:
        fd = safefs.open_leaf(dir_fd, leaf)
    except FileNotFoundError:
        raise Rejected("missing") from None
    except OSError:
        raise Rejected("link") from None
    finally:
        os.close(dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Rejected("not-regular")
        if info.st_nlink != 1:
            raise Rejected("hard-linked")
        if info.st_size > FILE_LIMIT:
            raise Rejected("too-large")
        if info.st_size > room:
            raise Rejected("agent-limit")
        data = os.read(fd, info.st_size + 1)
        again = os.fstat(fd)
        same = all(getattr(info, f) == getattr(again, f) for f in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size"))
        if len(data) != info.st_size or not same:
            raise Rejected("changed")
        return data
    finally:
        os.close(fd)


def write_artifact(run: Path, agent: str, rel: str, data: bytes) -> None:
    *folders, leaf = rel.split("/")
    try:
        fd = safefs.walk(run, ["agents", agent, "artifacts"])
    except OSError:
        raise Rejected("link") from None
    try:
        for part in folders:
            try:
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=fd)
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            out = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        except FileExistsError:
            raise Rejected("exists") from None
        try:
            if safefs.regular_single(out) is None:
                raise Rejected("not-regular")
            os.write(out, data)
        finally:
            os.close(out)
    except OSError:
        raise Rejected("link") from None
    finally:
        os.close(fd)


def cmd_promote(args: argparse.Namespace) -> int:
    def body(run: Path, state: dict | None) -> dict:
        assert state is not None
        known_agent(state, args.agent)
        found, errors, result = read_result(run, args.agent)
        if result is None:
            common.fail("invalid", state=found, errors=errors)
        brief = state["briefs"][args.agent]
        own = f"agents/{args.agent}/artifacts/"
        promoted, rejected = [], []
        for artifact in local_artifacts(result):
            rel = artifact[len(own):] if artifact.startswith(own) else ""
            try:
                if not rel or rel.endswith("/") or common.path_error(rel):
                    raise Rejected("unsafe-path")
                if artifact in brief["promoted"]:
                    raise Rejected("exists")
                data = read_scratch(run, args.agent, rel, AGENT_LIMIT - sum(brief["promoted"].values()))
                write_artifact(run, args.agent, rel, data)
            except Rejected as exc:
                rejected.append({"path": artifact, "reason": str(exc)})
                continue
            brief["promoted"][artifact] = len(data)
            promoted.append(artifact)
        if promoted:
            common.save_state(run, state)
        return {"promoted": promoted, "rejected": rejected}

    return common.run_step(args, body)

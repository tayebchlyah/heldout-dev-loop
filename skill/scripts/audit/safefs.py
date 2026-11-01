"""No-follow file access under an agent folder: every folder part and the leaf opened with `O_NOFOLLOW`, relative to
the folder above it."""

from __future__ import annotations

import os
import stat
from pathlib import Path

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_dir(parent_fd: int, name: str) -> int:
    """Open the folder `name` of `parent_fd`; a link or a non-folder raises `OSError`."""
    return os.open(name, DIR_FLAGS, dir_fd=parent_fd)


def walk(root: Path, parts: list[str]) -> int:
    """Open `root`, then each folder part below it, no-follow; the caller closes the returned fd."""
    fd = os.open(root, DIR_FLAGS)
    try:
        for part in parts:
            child = open_dir(fd, part)
            os.close(fd)
            fd = child
    except OSError:
        os.close(fd)
        raise
    return fd


def open_leaf(dir_fd: int, name: str) -> int:
    """Open the file `name` of `dir_fd` for reading, no-follow and non-blocking (a FIFO does not hang)."""
    return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)


def regular_single(fd: int) -> os.stat_result | None:
    """The `fstat` of `fd` when it is a regular file with one link, else `None`."""
    info = os.fstat(fd)
    return info if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 else None


def read_file(root: Path, rel: str, limit: int) -> bytes | None:
    """The bytes of `root/rel` when every part is a real folder, the leaf a regular single-link file of at most
    `limit` bytes; else `None`."""
    *folders, leaf = rel.split("/")
    try:
        dir_fd = walk(root, folders)
    except OSError:
        return None
    try:
        fd = open_leaf(dir_fd, leaf)
    except OSError:
        return None
    finally:
        os.close(dir_fd)
    try:
        info = regular_single(fd)
        if info is None or info.st_size > limit:
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        return data if len(data) <= limit else None
    finally:
        os.close(fd)

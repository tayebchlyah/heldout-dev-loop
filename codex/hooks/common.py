"""Helpers the Codex gate launcher shares on every platform."""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

METADATA = ('.git', '.codex', '.agents')


def darwin_temp():
    """The resolved per-user temporary root of macOS."""
    root = subprocess.run(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'],
                          capture_output=True, text=True, check=True).stdout.strip()
    if not root or not Path(root).is_absolute():
        raise ValueError('invalid Darwin temporary root')
    return Path(root).resolve()


def start_marker(path):
    """Shell text that writes the started marker at `path`, else exits 125."""
    return 'printf started >' + path + ' || exit 125'


def require_started(marker, label, code):
    if not marker.is_file():
        raise ValueError(label + ' exited before command start (exit ' + str(code) + ')')


class LineReader:
    """Read newline-terminated lines from a pipe before a deadline."""

    def __init__(self, stream, limit=None):
        self.fd, self.limit, self.buffer = stream.fileno(), limit, bytearray()

    def line(self, deadline, what):
        while True:
            end = self.buffer.find(b'\n')
            if self.limit is not None and (end > self.limit or end < 0 and len(self.buffer) > self.limit):
                raise ValueError(what + ' line too long')
            if end >= 0:
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 1]
                return line
            if not select.select([self.fd], [], [], max(0, deadline - time.monotonic()))[0]:
                raise ValueError(what + ' timed out')
            chunk = os.read(self.fd, 65536)
            if not chunk:
                raise ValueError(what + ' ended before a full line')
            self.buffer.extend(chunk)


def stop(process):
    """Terminate a launch group even when its leader has already exited."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as error:
        if sys.platform != 'darwin':
            raise
        try:
            # allow-comment: dependency: Darwin can return EPERM between process exit and reaping after native resolver EOF.
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            raise error
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # allow-comment: dependency: Darwin can return EPERM for an emptied process group after its leader is reaped.
        if sys.platform != 'darwin' or process.poll() is None:
            raise
    process.wait(timeout=3)

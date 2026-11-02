"""Helpers the Codex gate launcher shares on every platform."""
from __future__ import annotations

import os
import signal
import subprocess
import sys


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

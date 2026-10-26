"""`board`: write the progress board notes and run the local board server."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import devloop
from devloop import emit, git
from notes_path import NotesPathError, resolve


def cmd_board(args: argparse.Namespace) -> int:
    raw = os.environ.get("DEVLOOP_BOARD_PORT") or "7777"
    port = int(raw) if raw.isascii() and raw.isdigit() and 0 < int(raw) < 65536 else None
    bad_port = {"state": "error", "url": f"http://127.0.0.1:{raw}/",
                "message": f"DEVLOOP_BOARD_PORT is not a port: {raw}"}
    from board import store, writer  # board.server only when a server is stopped or ensured: its imports are slow.

    if args.stop_server:
        from board import server
        stopped = server.stop(port) if port is not None else bad_port
        return emit({"notes": [], "removed": [], "board_note": None, "skipped": [], "server": stopped}, 0)
    repo = Path(args.repo).resolve()
    try:
        notes = resolve(repo, os.environ, Path.home())
    except NotesPathError as exc:
        return emit({"error": str(exc)}, 1)
    hook_cwd = hook_session = hook_agent = hook_transcript = None
    if args.event:
        try:
            hook = json.loads(sys.stdin.read())
        except ValueError:
            hook = None
        hook = hook if isinstance(hook, dict) else {}
        cwd, session = hook.get("cwd"), hook.get("session_id")
        hook_cwd = Path(cwd) if isinstance(cwd, str) and cwd else None
        hook_session = session if isinstance(session, str) and session else None  # D12: else no session map.
        agent = hook.get("agent_id")
        hook_agent = agent if isinstance(agent, str) and agent else None
        log = hook.get("transcript_path")
        hook_transcript = log if isinstance(log, str) and log else None

    try:
        code, payload = writer.run(devloop, repo, notes, Path.home(), event=args.event,
                                   hook_cwd=hook_cwd, hook_session=hook_session, hook_agent=hook_agent,
                                   hook_transcript=hook_transcript,
                                   claim=args.claim)
        if code != 0:
            return emit(payload, code)
        whole = {s["reason"] for s in payload["skipped"]} & {"no-dev-loop", "notes-in-repo"}
        if args.no_serve or whole:
            payload["server"] = {"state": "disabled", "url": f"http://127.0.0.1:{raw}/"}
        elif port is None:
            payload["server"] = bad_port
        else:
            from board import server
            machine = store.load_machine(Path.home())
            top = Path(git(repo, "rev-parse", "--show-toplevel") or repo)
            payload["server"] = server.ensure(port, writer.kit_version(top), [store.root_of(notes)], machine["id"],
                                              machine["owner"])
    except Exception as exc:  # A bad machine.json, an undecodable spec, an OSError: JSON, never a traceback.
        error = f"{type(exc).__name__}: {exc}"
        try:  # A hook drops the JSON: `last_run` shows the crash in `status`. Best effort.
            from board import state
            common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if common:
                state.save_last_run(Path(common) / "dev-loop", time.strftime(writer.TIME, time.gmtime()),
                                    [{"feature": None, "reason": "error", "message": error}])
        except Exception:
            pass
        return emit({"error": error}, 1)
    return emit(payload, 0)

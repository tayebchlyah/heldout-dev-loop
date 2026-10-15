#!/usr/bin/env python3
"""dev-loop board server: 127.0.0.1 only, standard library only (spec D19 to D21, B1, B2).

  server.py --port <n> --version <v> --here <machine_id> --me <owner> --root <path> [--root <path> ...]

Endpoints: `/` (page.html with render.js inline), `/api/status` (status notes under the roots), `/health`.
`ensure` and `stop` are what `devloop.py board` calls.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "dev-loop board"
FOLDER = "dev-loop-board"
MAX_BYTES = 65536
MAX_NOTES = 500
WALK_EVERY = 60
LOG = Path(tempfile.gettempdir()) / "dev-loop-board.log"


def root_of(notes: Path) -> Path:
    """D19: the nearest parent of the notes path that holds `.obsidian`, else the notes path."""
    notes = notes.resolve()
    return next((p for p in [notes, *notes.parents] if (p / ".obsidian").is_dir()), notes)


def version_key(text: object) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return ()


def parse(data: bytes) -> dict | None:
    """Schema 1 frontmatter, each value JSON. None when any part does not parse or the frontmatter is cut."""
    try:
        lines = data.decode().splitlines()
        if not lines or lines[0] != "---":
            return None
        fields = {}
        for line in lines[1:lines.index("---", 1)]:
            key, sep, value = line.partition(":")
            if not sep:
                return None
            fields[key.strip()] = json.loads(value.strip())
        return fields
    except (UnicodeDecodeError, ValueError):
        return None


class Index:
    """D21: finds the notes at most once per WALK_EVERY seconds, reads them on every request."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots, self.paths, self.walked = roots, [], float("-inf")
        self.lock = threading.Lock()

    def walk(self) -> list[Path]:
        found: list[Path] = []
        for root in self.roots:
            for folder, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(folder, d))]
                if os.path.basename(folder) != FOLDER:
                    continue
                for name in sorted(files):
                    path = Path(folder) / name
                    if name.endswith(".md") and not name.startswith(".") and not path.is_symlink():
                        found.append(path)
                        if len(found) >= MAX_NOTES:
                            return found
        return found

    def status(self) -> tuple[list[dict], int]:
        with self.lock:
            if time.monotonic() - self.walked > WALK_EVERY:
                self.paths, self.walked = self.walk(), time.monotonic()
            paths = list(self.paths)
        features, newer = [], 0
        for path in paths:
            try:
                # O_NOFOLLOW: a note swapped for a link after the walk is not read (B2).
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as handle:
                    fields = parse(handle.read(MAX_BYTES))
            except OSError:
                continue
            if not fields or fields.get("type") != "dev-loop-status":
                continue
            schema = fields.get("schema")
            if schema == 1 and not isinstance(schema, bool):
                features.append(fields)
            elif isinstance(schema, (int, float)) and not isinstance(schema, bool) and schema > 1:
                newer += 1
        return features, newer


def make_server(port: int, version: str, roots: list[Path], here: str, me: str) -> ThreadingHTTPServer:
    index = Index(roots)
    page = (HERE / "page.html").read_text().replace("/*RENDER_JS*/", (HERE / "render.js").read_text(), 1)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def send(self, code: int, ctype: str, body: str) -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.headers.get("Host") not in hosts:
                self.send(403, "text/plain", "forbidden")
            elif self.path == "/":
                self.send(200, "text/html; charset=utf-8", page)
            elif self.path == "/api/status":
                features, newer = index.status()
                self.send(200, "application/json", json.dumps(
                    {"features": features, "newer_schema": newer, "here": here, "me": me, "version": version}))
            elif self.path == "/health":
                self.send(200, "application/json", json.dumps(
                    {"name": NAME, "version": version, "roots": [str(r) for r in roots], "pid": os.getpid()}))
            else:
                self.send(404, "text/plain", "not found")

        def log_message(self, format: str, *args: object) -> None:
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 64
        allow_reuse_address = True

    return Server(("127.0.0.1", port), Handler)


def probe(port: int) -> tuple[str, dict]:
    """GET /health: `none` (refused), `stalled` (no answer in 2 s), `busy` (not a board), or `board`."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("GET", "/health", headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        body = response.read()
    except ConnectionRefusedError:
        return "none", {}
    except (TimeoutError, socket.timeout):
        return "stalled", {}
    except (OSError, http.client.HTTPException):
        return "busy", {}
    finally:
        conn.close()
    try:
        found = json.loads(body)
    except ValueError:
        return "busy", {}
    if response.status == 200 and isinstance(found, dict) and found.get("name") == NAME:
        return "board", found
    return "busy", {}


def failure(kind: str, url: str, port: int) -> dict:
    if kind == "stalled":
        return {"state": "stalled", "url": url, "message": f"port {port} accepts but does not answer in 2 s"}
    return {"state": "port-busy", "url": url, "message": f"port {port} is used by something that is not the board"}


def start(port: int, version: str, roots: list[Path], here: str, me: str, state: str) -> dict:
    url = f"http://127.0.0.1:{port}/"
    cmd = [sys.executable, str(HERE / "server.py"), "--port", str(port), "--version", version, "--here", here,
           "--me", me]
    for root in roots:
        cmd += ["--root", str(root)]
    with open(LOG, "a") as log:
        proc = subprocess.Popen(cmd, start_new_session=True, cwd="/", stdin=subprocess.DEVNULL, stdout=log,
                                stderr=log)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and proc.poll() is None:
        if probe(port)[0] == "board":
            return {"state": state, "url": url}
        time.sleep(0.1)
    return {"state": "error", "url": url, "message": f"the board server did not start on port {port}, see {LOG}"}


def ensure(port: int, version: str, roots: list[Path], here: str, me: str) -> dict:
    """D20: make sure a board server with this version or newer and these roots runs. Never raises."""
    url = f"http://127.0.0.1:{port}/"
    try:
        kind, found = probe(port)
        if kind == "none":
            return start(port, version, roots, here, me, "started")
        if kind != "board":
            return failure(kind, url, port)
        theirs = [Path(r) for r in found.get("roots") or [] if isinstance(r, str)]
        if version_key(found.get("version")) >= version_key(version) and all(r in theirs for r in roots):
            return {"state": "running", "url": url}
        stopped = stop(port)
        if stopped["state"] != "stopped":
            return stopped
        newest = max([version, str(found.get("version"))], key=version_key)
        return start(port, newest, theirs + [r for r in roots if r not in theirs], here, me, "restarted")
    except Exception as exc:  # D20: a server error never fails `board`.
        return {"state": "error", "url": url, "message": f"{type(exc).__name__}: {exc}"}


def stop(port: int) -> dict:
    """SIGTERM the board server on this port, then wait up to 5 s until /health fails."""
    url = f"http://127.0.0.1:{port}/"
    try:
        kind, found = probe(port)
        if kind == "none":
            return {"state": "stopped", "url": url}
        if kind != "board":
            return failure(kind, url, port)
        pid = found.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            return {"state": "error", "url": url, "message": f"the board on port {port} reports no valid pid"}
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while probe(port)[0] == "board":
            if time.monotonic() > deadline:
                return {"state": "error", "url": url, "message": f"the board server (pid {pid}) did not stop"}
            time.sleep(0.1)
        return {"state": "stopped", "url": url}
    except Exception as exc:
        return {"state": "error", "url": url, "message": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--here", required=True)
    parser.add_argument("--me", required=True)
    parser.add_argument("--root", type=Path, action="append", required=True)
    args = parser.parse_args()
    make_server(args.port, args.version, args.root, args.here, args.me).serve_forever()


if __name__ == "__main__":
    main()

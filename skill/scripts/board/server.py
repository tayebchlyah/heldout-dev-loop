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
import stat
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__:
    from . import store
else:  # Run as a script by `start`: the board folder is sys.path[0].
    import store

HERE = Path(__file__).resolve().parent
NAME = "dev-loop board"
FOLDER = "dev-loop-board"
MAX_BYTES = 65536
MAX_NOTES = 500
WALK_EVERY = 60
LOG = Path(tempfile.gettempdir()) / "dev-loop-board.log"


class Index:
    """D21: finds the board folders at most once per WALK_EVERY seconds, lists and reads their notes on every request."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots, self.folders, self.walked = roots, [], float("-inf")
        self.lock = threading.Lock()

    def walk(self) -> list[str]:
        found: list[str] = []
        for root in self.roots:
            for folder, dirs, _ in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(folder, d))]
                if os.path.basename(folder) == FOLDER:
                    found.append(folder)
        return found

    def notes(self, folders: list[str]) -> list[Path]:
        found: list[Path] = []
        for folder in folders:
            if os.path.islink(folder):  # B2: a cached folder swapped for a link is not listed.
                continue
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in names:
                path = Path(folder) / name
                if name.endswith(".md") and not name.startswith(".") and path.is_file() and not path.is_symlink():
                    found.append(path)
                    if len(found) >= MAX_NOTES:
                        return found
        return found

    def status(self) -> tuple[list[dict], int]:
        with self.lock:
            if time.monotonic() - self.walked > WALK_EVERY:
                self.folders, self.walked = self.walk(), time.monotonic()
            folders = list(self.folders)
        features, newer = [], 0
        for path in self.notes(folders):
            try:
                # O_NOFOLLOW, O_NONBLOCK, S_ISREG: a note swapped for a link or a FIFO after the listing is not read (B2).
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as handle:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        continue
                    fields = store.parse(handle.read(MAX_BYTES))
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


def pidfile(port: int) -> Path:
    return Path.home() / ".config" / "dev-loop" / f"board-server.{port}.pid"


def probe(port: int) -> tuple[str, dict]:
    """GET /health: `none` (refused), `stalled` (no answer in 2 s), `busy` (not a board, or a pid that is not the
    pidfile's), or `board`."""
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
    if response.status != 200 or not isinstance(found, dict) or found.get("name") != NAME:
        return "busy", {}
    try:
        recorded = pidfile(port).read_text().strip()
    except OSError:
        recorded = None
    # C4: a pid is trusted (and signalled) only when the server that holds the port recorded it at start.
    return ("board", found) if recorded == str(found.get("pid")) else ("busy", {})


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
    kind, found = probe(port)  # C3: our child lost the port to a board that another run started at the same moment.
    if kind == "board" and all(str(r) in (found.get("roots") or []) for r in roots):
        return {"state": "running", "url": url}
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
        if store.version_key(found.get("version")) >= store.version_key(version) and all(r in theirs for r in roots):
            return {"state": "running", "url": url}
        stopped = stop(port)
        if stopped["state"] != "stopped":
            return stopped
        # D3: the label names the code that runs, this kit's server.py.
        return start(port, version, theirs + [r for r in roots if r not in theirs], here, me, "restarted")
    except Exception as exc:  # D20: a server error never fails `board`.
        return {"state": "error", "url": url, "message": f"{type(exc).__name__}: {exc}"}


def stop(port: int) -> dict:
    """SIGTERM the board server on this port, then wait up to 5 s until the port refuses connections."""
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
        while probe(port)[0] != "none":
            if time.monotonic() > deadline:
                return {"state": "error", "url": url, "message": f"the board server (pid {pid}) did not stop"}
            time.sleep(0.02)
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
    httpd = make_server(args.port, args.version, args.root, args.here, args.me)
    path, pid = pidfile(args.port), str(os.getpid())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pid + "\n")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # Run the cleanup below on `stop`.
    try:
        httpd.serve_forever()
    finally:
        if path.exists() and path.read_text().strip() == pid:
            path.unlink()


if __name__ == "__main__":
    main()

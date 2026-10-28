#!/usr/bin/env python3
"""Held-out oracle tests for the server of `devloop.py board` and its hooks (spec: Progress Board).

Each test names the `## Acceptance oracle` number (O<n>) or flow (F<n>) it checks. The tests use
only the public seams: the stdout JSON of `devloop.py board`, the files it writes, the HTTP answers
of the server it starts, and the stdout of the hook scripts. Every name in here is invented (D33).
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import (MACHINE, VERSION, Board, BoardHookFixture, git, health, note_text, put, request, slow,
                            snapshot, status_note)
from feature_folders_support import NewLayout


class BoardServerTests(NewLayout, Board):
    """Seam 2: the server that `board` starts. Every test stops it again."""

    def setUp(self) -> None:
        super().setUp()
        (self.vault / ".obsidian").mkdir()
        self.board_dir.mkdir(parents=True)
        self.worktree("tiny", prefix="fix")
        self.pids: list[int] = []
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        try:
            self.board("--stop-server", timeout=30)
        except (AssertionError, subprocess.TimeoutExpired):
            pass
        self.kill_stray_server()
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def serve(self, cwd: Path | None = None, env: dict | None = None) -> dict:
        out = self.ok(cwd=cwd, env=env, serve=True, timeout=90)
        found = self.wait_health()
        if found and isinstance(found.get("pid"), int):
            self.pids.append(found["pid"])
        return out

    def wait_health(self, seconds: float = 5) -> dict | None:
        deadline = time.monotonic() + seconds
        while True:
            found = health(self.port)
            if found or time.monotonic() > deadline:
                return found
            time.sleep(0.1)

    def set_version(self, version: str, repo: Path | None = None) -> None:
        put((repo or self.repo) / ".dev-loop" / "MANIFEST.json", json.dumps({"version": version, "files": {}}) + "\n")

    def api(self) -> dict:
        status, headers, body = request(self.port, "/api/status")
        self.assertEqual(status, 200)
        return json.loads(body)

    def listener(self) -> socket.socket:
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        sock.listen(8)
        self.addCleanup(sock.close)
        return sock

    @slow
    def test_o34_server_failure_does_not_fail_board(self) -> None:
        """O34, D20: the port held by something else: exit 0, server.state error, port-busy or stalled."""

        class NotFound(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(404)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        with self.subTest("another http server"):
            other = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), NotFound)
            thread = threading.Thread(target=other.serve_forever, daemon=True)
            thread.start()
            try:
                code, out = self.board(serve=True, timeout=90)
            finally:
                other.shutdown()
                other.server_close()
            self.assertEqual(code, 0, out)
            self.assertIn(out["server"]["state"], {"error", "port-busy", "stalled"})
            self.assertTrue(out["server"].get("message"))
        with self.subTest("a socket that never answers"):
            sock = self.listener()
            code, out = self.board(serve=True, timeout=90)
            sock.close()
            self.assertEqual(code, 0, out)
            self.assertIn(out["server"]["state"], {"error", "port-busy", "stalled"})

    def test_o35_no_serve_contacts_no_server(self) -> None:
        """O35: --no-serve neither contacts nor starts a server: state `disabled`."""
        sock = self.listener()
        out = self.ok()
        self.assertEqual(out["server"]["state"], "disabled")
        sock.setblocking(False)
        with self.assertRaises(BlockingIOError):
            sock.accept()

    def test_o36_health_answers_with_the_manifest_version(self) -> None:
        """O36, D19, D20: after `board`, GET /health answers name, the MANIFEST version, roots and pid."""
        out = self.serve()
        self.assertIn(out["server"]["state"], {"started", "running"})
        self.assertEqual(out["server"]["url"], f"http://127.0.0.1:{self.port}/")
        found = health(self.port)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual((found["name"], found["version"]), ("dev-loop board", VERSION))
        self.assertIsInstance(found["pid"], int)
        self.assertIn(self.vault, [Path(r).resolve() for r in found["roots"]])

    def test_o37_f8_older_server_is_restarted(self) -> None:
        """O37, F8: a running server with an older version is replaced: `restarted`."""
        self.set_version("2.0.9")
        self.serve()
        old = health(self.port)
        assert old is not None
        self.assertEqual(old["version"], "2.0.9")
        self.set_version(VERSION)
        out = self.serve()
        self.assertEqual(out["server"]["state"], "restarted")
        new = health(self.port)
        assert new is not None
        self.assertEqual(new["version"], VERSION)
        self.assertNotEqual(new["pid"], old["pid"])

    def test_o38_newer_server_keeps_running(self) -> None:
        """O38, D20: a running server with a newer version and the root keeps running: `running`."""
        self.serve()
        old = health(self.port)
        assert old is not None
        self.set_version("2.0.9")
        out = self.serve()
        self.assertEqual(out["server"]["state"], "running")
        now = health(self.port)
        assert now is not None
        self.assertEqual((now["version"], now["pid"]), (VERSION, old["pid"]))

    @slow
    def test_o39_server_without_this_root_is_replaced_with_both(self) -> None:
        """O39, D20: a running server without the root of this project is replaced by one with both roots."""
        self.serve()
        old = health(self.port)
        assert old is not None
        notes_two = self.root / "vault-two" / "Zephyr"
        (notes_two / "Features").mkdir(parents=True)
        origin_two = self.root / "remotes" / "zephyr-app.git"
        git(self.root, "init", "-q", "--bare", str(origin_two))
        repo_two = self.root / "zephyr-app"
        self.make_repo(repo_two, origin_two)
        env_two = {**self.env, "DEV_LOOP_NOTES": str(notes_two)}
        self.worktree("tiny", prefix="fix", repo=repo_two)
        out = self.serve(cwd=repo_two, env=env_two)
        self.assertIn(out["server"]["state"], {"restarted", "started"})
        now = health(self.port)
        assert now is not None
        roots = [Path(r).resolve() for r in now["roots"]]
        self.assertIn(self.vault, roots)
        self.assertIn(notes_two, roots)
        self.assertNotEqual(now["pid"], old["pid"])

    def test_o40_stop_server_stops_it_and_writes_nothing(self) -> None:
        """O40: --stop-server stops the server; /health does not answer after it; nothing is written."""
        self.serve()
        self.assertIsNotNone(health(self.port))
        before = snapshot(self.vault)
        code, _ = self.board("--stop-server", timeout=30)
        self.assertEqual(code, 0)
        deadline = time.monotonic() + 5
        while health(self.port, timeout=1) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertIsNone(health(self.port, timeout=1))
        self.assertEqual(snapshot(self.vault), before)

    def test_o41_o44_o45_o46_http_surface(self) -> None:
        """O41 loopback only, O44 Host allowlist, O45 three paths only, O46 no CORS header."""
        put(self.vault / "dev-loop-board" / "x.md", "---\ntype: note\n---\nSECRET-MARKER-45\n")
        self.serve()
        status, headers, body = request(self.port, "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("content-type", "").startswith("text/html"))
        self.assertIn(b"renderBoard", body)
        with self.subTest("O41"):
            with self.assertRaises(OSError):
                socket.create_connection(("::1", self.port), timeout=2).close()
            lan = None
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.connect(("192.0.2.1", 9))
                    lan = probe.getsockname()[0]
            except OSError:
                pass
            if lan and not lan.startswith("127."):
                with self.assertRaises(OSError):
                    socket.create_connection((lan, self.port), timeout=2).close()
        with self.subTest("O44"):
            self.assertEqual(request(self.port, "/health", host=f"evil.example:{self.port}")[0], 403)
            self.assertEqual(request(self.port, "/api/status", host=f"evil.example:{self.port}")[0], 403)
            self.assertEqual(request(self.port, "/health", host=f"localhost:{self.port}")[0], 200)
        with self.subTest("O45"):
            for path in ("/../", "/dev-loop-board/x.md", "/api/status/../../dev-loop-board/x.md", "/index.html",
                         "/health/x", "/%2e%2e/dev-loop-board/x.md"):
                status, _, body = request(self.port, path)
                self.assertEqual(status, 404, path)
                self.assertNotIn(b"SECRET-MARKER-45", body, path)
        with self.subTest("O46"):
            for path, host in (("/", None), ("/api/status", None), ("/health", None), ("/nope", None),
                               ("/health", f"evil.example:{self.port}")):
                _, headers, _ = request(self.port, path, host=host, headers={"Origin": "http://evil.example"})
                self.assertNotIn("access-control-allow-origin", headers, path)

    def test_o42_o43_f6_f7_api_status_lists_only_status_notes_under_roots(self) -> None:
        """O42 (superseded by Board Robustness oracle 28, then by Feature Folders), O43, F6, F7: notes in every
        `Dev Loop Board` or `dev-loop-board` folder and its Features/features subfolder under the roots; links,
        dot names, schema 2 and folders outside the roots are left out; schema 2 counts in newer_schema."""
        other = self.vault / "Projects" / "Other Thing" / "dev-loop-board"
        put(other / "zephyr.md", note_text(status_note(project="other-thing", feature="zephyr-sync")))
        put(other / "features" / "zephyr-two.md", note_text(status_note(project="other-thing", feature="zephyr-two")))
        put(other / "plain.md", "---\ntype: \"meeting\"\nfeature: \"meeting-note\"\n---\nnotes\n")
        put(other / "data.json", json.dumps(status_note(feature="json-file")))
        put(other / ".hidden.md", note_text(status_note(feature="dot-file")))
        put(other / "future.md", note_text(status_note(feature="future-thing", schema=2)))
        outside = self.root / "elsewhere"
        put(outside / "real.md", note_text(status_note(feature="via-link")))
        (other / "linked.md").symlink_to(outside / "real.md")
        put(self.vault / ".trash" / "dev-loop-board" / "trashed.md", note_text(status_note(feature="in-trash")))
        put(self.vault / "Projects" / ".archive" / "dev-loop-board" / "x.md", note_text(status_note(feature="dot-folder")))
        put(outside / "linked-project" / "dev-loop-board" / "y.md", note_text(status_note(feature="linked-folder")))
        (self.vault / "Linked").symlink_to(outside / "linked-project")
        put(outside / "Hidden" / "dev-loop-board" / "h.md", note_text(status_note(feature="secret-plan")))
        self.serve()
        api = self.api()
        features = {f.get("feature") for f in api["features"]}
        self.assertLessEqual({"tiny", "zephyr-sync", "zephyr-two"}, features)
        for left_out in ("meeting-note", "json-file", "dot-file", "future-thing", "via-link", "in-trash",
                         "dot-folder", "linked-folder", "secret-plan"):
            self.assertNotIn(left_out, features)
        self.assertEqual(api["newer_schema"], 1)
        self.assertEqual((api["here"], api["me"], api["version"]), (MACHINE["id"], MACHINE["owner"], VERSION))
        for feature in api["features"]:
            self.assertEqual((feature["type"], feature["schema"]), ("dev-loop-status", 1))

    @slow
    def test_o47_read_limits(self) -> None:
        """O47, D21: the server read limits. Rows: at most 500 notes; at most the first 64 KB of a note."""

        def at_most_500_notes() -> None:
            bulk = self.vault / "Projects" / "Bulk" / "dev-loop-board"
            for n in range(510):
                put(bulk / f"n{n:03}.md", note_text(status_note(project="bulk", feature=f"bulk-{n:03}")))
            self.serve()
            self.assertLessEqual(len(self.api()["features"]), 500)

        def at_most_64_kb_of_a_note() -> None:
            folder = self.vault / "Projects" / "Big" / "dev-loop-board"
            # The title line comes after a 70 KB description, so it sits past the first 64 KB.
            fields = status_note(project="big", feature="big-note", description="x" * 70000)
            fields.pop("title")
            put(folder / "big.md", note_text({**fields, "title": "Far Title"}))
            put(folder / "small.md", note_text(status_note(project="big", feature="small-one")))
            self.serve()
            status, _, body = request(self.port, "/api/status")
            self.assertEqual(status, 200)
            self.assertIn(b"small-one", body)
            self.assertNotIn(b"Far Title", body)

        for n, (name, run) in enumerate((("at most 500 notes", at_most_500_notes),
                                         ("at most 64 KB of a note", at_most_64_kb_of_a_note))):
            with self.subTest(name):
                if n:
                    self.setUp()  # a fresh fixture per row, removed by doCleanups below
                try:
                    run()
                finally:
                    self.doCleanups()  # stops the server and removes the row fixture


class BoardHookTests(BoardHookFixture, unittest.TestCase):
    """O32, O33, F5. The hook scripts run from a kit layout copy in a temp repo; a fake devloop.py at
    `.agents/skills/dev-loop/scripts/devloop.py` logs each call and fails."""

    @slow
    def test_o32_f5_blocked_stop_runs_no_board(self) -> None:
        """O32, F5: gate.sh that blocks a stop does not run `board`, so waiting does not change. The same
        stop with a passing gate runs it: the control that the fake devloop.py is reachable."""
        for harness in ("claude", "codex"):
            with self.subTest(harness):
                self.gate(passes=False)
                self.log.unlink(missing_ok=True)
                result = self.hook(harness, "gate.sh", "Stop")
                self.assertEqual(json.loads(result.stdout)["decision"], "block")
                self.assertEqual([c for c in self.calls(wait=1.5) if "board" in c["argv"]], [])
                self.gate(passes=True)
                self.hook(harness, "gate.sh", "Stop")
                self.assert_board_event(self.calls(wait=6), "stop")

    @slow
    def test_o33_gate_runs_board_quietly_when_it_does_not_block(self) -> None:
        """O33, D15: a passing gate.sh runs `board --event stop|subagent-stop`; stdout empty, exit 0, also
        when board fails."""
        self.gate(passes=True)
        for harness in ("claude", "codex"):
            for event, name in (("Stop", "stop"), ("SubagentStop", "subagent-stop")):
                with self.subTest(harness=harness, event=event):
                    self.log.unlink(missing_ok=True)
                    result = self.hook(harness, "gate.sh", event)
                    self.assertEqual((result.returncode, result.stdout), (0, ""))
                    self.assert_board_event(self.calls(wait=6), name)

    @slow
    def test_o33_board_sh_runs_board_quietly(self) -> None:
        """O33, D15: board.sh runs `board --event session-start` and `--event prompt`; stdout empty, exit 0,
        also when board fails."""
        for harness in ("claude", "codex"):
            for event, name in (("SessionStart", "session-start"), ("UserPromptSubmit", "prompt")):
                with self.subTest(harness=harness, event=event):
                    self.log.unlink(missing_ok=True)
                    result = self.hook(harness, "board.sh", event, name)
                    self.assertEqual((result.returncode, result.stdout), (0, ""))
                    self.assert_board_event(self.calls(wait=6), name)


if __name__ == "__main__":
    unittest.main()

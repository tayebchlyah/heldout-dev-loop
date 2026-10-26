#!/usr/bin/env python3
"""Held-out oracle tests for `sandbox-run.py` (spec: Release Security Audit).

Seams, fixture choices and shared helpers: `release_security_audit_support.py`.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from release_security_audit_support import (ABSENT_IMAGES, BASE_CONFIG, base_env, docker, IMAGE, IMAGE_PATH, put,
                                            SANDBOX, sandbox_containers, SandboxCase, tools_dir)


# ---------------------------------------------------------------------------------------------------------------
# sandbox-run.py (O32 to O45, flow 4)
# ---------------------------------------------------------------------------------------------------------------

class SandboxTests(SandboxCase):

    def test_o32_command_io_and_exit(self) -> None:
        result = self.run_sandbox("echo", "hi")
        self.assertEqual((result.returncode, result.stdout), (0, "hi\n"), result.stderr)
        result = self.run_sandbox("sh", "-c", "exit 3")
        self.assertEqual(result.returncode, 3, result.stderr)
        result = self.run_sandbox("cat", stdin="abc")
        self.assertEqual((result.returncode, result.stdout), (0, "abc"), result.stderr)

    def test_o33_no_network_no_dns(self) -> None:
        control = self.run_sandbox("echo", "runs")
        self.assertEqual((control.returncode, control.stdout), (0, "runs\n"), control.stderr)
        result = self.run_sandbox("wget", "-T", "3", "-q", "-O-", "http://1.1.1.1")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_sandbox("nslookup", "example.com")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_o34_host_loopback_unreachable_own_loopback_works(self) -> None:
        site = self.root / "site"
        site.mkdir()
        put(site / "index.html", "host-marker-quartz-7731\n")
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **k)  # noqa: E731
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        result = self.run_sandbox("wget", "-T", "3", "-q", "-O-", f"http://127.0.0.1:{port}/index.html")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("host-marker-quartz-7731", result.stdout)
        inner = ("mkdir -p /scratch/www && echo inner-marker > /scratch/www/index.html && "
                 "httpd -p 127.0.0.1:8080 -h /scratch/www && sleep 1 && "
                 "wget -T 3 -q -O- http://127.0.0.1:8080/index.html")
        result = self.run_sandbox("sh", "-c", inner)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("inner-marker", result.stdout)

    def test_o35_writes_only_to_scratch(self) -> None:
        for path in ("/target/x", "/x", "/etc/x", "/tmp/x"):
            with self.subTest(path=path):
                result = self.run_sandbox("touch", path)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.target / "x").exists())
        result = self.run_sandbox("sh", "-c", "echo ok > /scratch/a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.scratch / "a").read_text(), "ok\n")

    def test_o36_memory_cap(self) -> None:
        result = self.run_sandbox("dd", "if=/dev/zero", "of=/dev/null", "bs=600M", "count=1",
                                  opts=("--memory", "256m"))
        self.assertEqual(result.returncode, 137, result.stdout + result.stderr)

    def test_o37_process_cap(self) -> None:
        bomb = "i=0; while [ $i -lt 200 ]; do sleep 3 & i=$((i+1)); done; wait"
        result = self.run_sandbox("sh", "-c", bomb, opts=("--pids", "64"))
        named = re.search(r"fork|resource temporarily unavailable", result.stderr, re.I)
        self.assertTrue(result.returncode != 0 or named, result.stdout + result.stderr)
        after = self.run_sandbox("echo", "still-here")
        self.assertEqual((after.returncode, after.stdout), (0, "still-here\n"), after.stderr)

    def test_o38_scratch_size(self) -> None:
        result = self.run_sandbox("dd", "if=/dev/zero", "of=/scratch/big", "bs=1M", "count=100",
                                  opts=("--scratch-size", "16m"))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stderr, re.compile(r"no space", re.I))

    def test_o38a_file_size_limit(self) -> None:
        control = self.run_sandbox("dd", "if=/dev/zero", "of=/scratch/small", "bs=1M", "count=1",
                                   opts=("--file-size", "4194304"))
        self.assertEqual(control.returncode, 0, control.stdout + control.stderr)
        self.assertEqual((self.scratch / "small").stat().st_size, 1048576)
        result = self.run_sandbox("dd", "if=/dev/zero", "of=/scratch/f", "bs=1M", "count=8",
                                  opts=("--file-size", "4194304"))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        copied = self.scratch / "f"
        if copied.exists():
            self.assertLessEqual(copied.stat().st_size, 4194304)

    def test_o39_scratch_byte_cap(self) -> None:
        result = self.run_sandbox("dd", "if=/dev/zero", "of=/scratch/f", "bs=1M", "count=2",
                                  opts=("--scratch-max-bytes", "1048576"))
        self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
        self.assertIn("sandbox-run: scratch over 1048576 bytes", result.stderr.splitlines())
        self.assertEqual(os.listdir(self.scratch), [])

    def test_o40_tar_escape(self) -> None:
        def snapshot() -> dict[str, tuple[int, int]]:
            out = {}
            for folder, dirs, names in os.walk(self.root):
                if Path(folder) == self.scratch or self.scratch in Path(folder).parents:
                    continue
                for name in dirs + names:
                    path = Path(folder) / name
                    if path == self.scratch:
                        continue
                    info = path.lstat()
                    out[str(path)] = (info.st_size, info.st_mtime_ns)
            return out

        before = snapshot()
        script = "ln -s /etc/passwd /scratch/l && ln -s / /scratch/d && mkfifo /scratch/p && echo f > /scratch/f"
        result = self.run_sandbox("sh", "-c", script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        kinds = self.scratch_files()
        self.assertEqual({k: v for k, v in kinds.items() if v != "dir"}, {"f": "file"}, kinds)
        self.assertEqual((self.scratch / "f").read_text(), "f\n")
        self.assertEqual(snapshot(), before)

    def test_o41_empty_environment(self) -> None:
        probe = f"devloop-acceptance-probe:{uuid.uuid4().hex[:12]}"
        made = docker("create", "--pull", "never", IMAGE, "true").stdout.strip()
        try:
            docker("commit", "--change", "ENV IMAGE_PROBE=1", made, probe)
        finally:
            docker("rm", "-f", made, check=False)
        self.addCleanup(docker, "rmi", "-f", probe, check=False)
        target = self.make_target({"security_audit": {"images": {"t": IMAGE, "p": probe}}}, "probe-target")
        expected = {"PATH": IMAGE_PATH, "HOME": "/scratch/home", "TMPDIR": "/scratch/tmp",
                    "XDG_CACHE_HOME": "/scratch/cache", "LANG": "C.UTF-8"}

        def env_of(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

        result = self.run_sandbox("env", image="p", target=target, env={"DEVLOOP_PROBE": "secret"})
        self.assertEqual(env_of(result), expected, result.stdout)
        result = self.run_sandbox("env", image="p", target=target, opts=("--env", "A=1"),
                                  env={"DEVLOOP_PROBE": "secret"})
        self.assertEqual(env_of(result), {**expected, "A": "1"}, result.stdout)

    def test_o42_user_and_capabilities(self) -> None:
        result = self.run_sandbox("id", "-u")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "65534"), result.stderr)
        result = self.run_sandbox("grep", "CapEff", "/proc/self/status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"CapEff:\s*0000000000000000")

    def test_o43_timeout(self) -> None:
        start = time.monotonic()
        result = self.run_sandbox("sleep", "30", opts=("--timeout", "2"))
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        self.assertLess(time.monotonic() - start, 15)

    def test_o44_no_container_left(self) -> None:
        """O44 is checked after every Docker case (`no_container_left`); this case adds a failing command."""
        result = self.run_sandbox("sh", "-c", "exit 9")
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertEqual(sandbox_containers(), [])

    def test_o45_runner_errors(self) -> None:
        with self.subTest(case="name not in the config"):
            result = self.run_sandbox("echo", "hi", image="nope")
            self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
            self.assertIn("sandbox-run: unknown image nope", result.stderr)
        with self.subTest(case="no security_audit key"):
            target = self.make_target({}, "bare-target")
            result = self.run_sandbox("echo", "hi", target=target)
            self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
            self.assertIn("sandbox-run: unknown image t", result.stderr)
        with self.subTest(case="image not present"):
            absent = next((name for name in ABSENT_IMAGES
                           if docker("image", "inspect", name, check=False).returncode != 0), None)
            self.assertIsNotNone(absent, "every candidate absent image is present locally")
            target = self.make_target({"security_audit": {"images": {"t": absent}}}, "absent-target")
            images = docker("image", "ls", "-a", "-q", "--no-trunc").stdout.split()
            result = self.run_sandbox("echo", "hi", target=target)
            self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
            self.assertIn(f"sandbox-run: image missing {absent}", result.stderr)
            self.assertEqual(sorted(docker("image", "ls", "-a", "-q", "--no-trunc").stdout.split()), sorted(images))
            self.assertNotEqual(docker("image", "inspect", absent, check=False).returncode, 0)

    def test_f4_sandbox_flow(self) -> None:
        """F4: no network or host loopback, no write outside /scratch, limits, files back within the cap."""
        script = ("wget -T 3 -q -O- http://1.1.1.1 && echo NET-REACHED; "
                  "touch /target/escape 2>/dev/null && echo TARGET-WRITTEN; "
                  "echo report > /scratch/report.txt; echo done")
        result = self.run_sandbox("sh", "-c", script, opts=("--scratch-max-bytes", "1048576"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("NET-REACHED", result.stdout)
        self.assertNotIn("TARGET-WRITTEN", result.stdout)
        self.assertFalse((self.target / "escape").exists())
        self.assertEqual((self.scratch / "report.txt").read_text(), "report\n")
        memory = self.run_sandbox("dd", "if=/dev/zero", "of=/dev/null", "bs=600M", "count=1",
                                  opts=("--memory", "256m"))
        self.assertEqual(memory.returncode, 137, memory.stderr)
        slow = self.run_sandbox("sleep", "30", opts=("--timeout", "2"))
        self.assertEqual(slow.returncode, 124, slow.stderr)


class SandboxNoDockerTests(unittest.TestCase):

    def test_o45_no_docker_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target, scratch = root / "target", root / "scratch"
            scratch.mkdir()
            put(target / "dev-loop.json", json.dumps({**BASE_CONFIG, "security_audit": {"images": {"t": IMAGE}}}))
            env = base_env(root, root)
            env["PATH"] = str(tools_dir(root, "git"))
            result = subprocess.run([sys.executable, str(SANDBOX), "--image", "t", "--scratch", str(scratch),
                                     "--target", str(target), "--", "echo", "hi"], cwd=target, env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 125, result.stdout + result.stderr)
            self.assertTrue(result.stderr.startswith("sandbox-run: unavailable"), result.stderr)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for sandbox-run.py against a real Docker engine and the pinned busybox image."""

from __future__ import annotations

import http.server
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parents[2] / "skill" / "scripts" / "sandbox-run.py"
IMAGE = "busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
IMAGE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


class SandboxRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("docker") is None or docker("info").returncode != 0:
            if os.environ.get("DEVLOOP_REQUIRE_DOCKER") == "1":
                raise AssertionError("DEVLOOP_REQUIRE_DOCKER=1 and docker info fails")
            raise unittest.SkipTest("no Docker engine")
        if docker("image", "inspect", IMAGE).returncode != 0:
            pulled = docker("pull", "-q", IMAGE)
            if pulled.returncode != 0:
                raise AssertionError(f"docker pull failed: {pulled.stderr}")
        cls.tmp = Path(tempfile.mkdtemp(prefix="devloop-sandbox-test-")).resolve()
        cls.probe = f"devloop-sandbox-probe:{uuid.uuid4().hex[:12]}"
        cls.missing = f"devloop-sandbox-never-pulled:{uuid.uuid4().hex[:12]}"
        created = docker("create", IMAGE).stdout.strip()
        docker("commit", "--change", "ENV IMAGE_PROBE=1", created, cls.probe)
        docker("rm", "-f", created)
        cls.target = cls.tmp / "target"
        cls.target.mkdir(mode=0o755)  # user 65534 must read /target on a Linux engine
        cls.target.chmod(0o755)
        subprocess.run(["git", "init", "-q"], cwd=cls.target, check=True)
        images = {"t": IMAGE, "probe": cls.probe, "gone": cls.missing}
        (cls.target / "dev-loop.json").write_text(json.dumps({"security_audit": {"images": images}}))

    @classmethod
    def tearDownClass(cls) -> None:
        for cid in cls.leftovers():
            docker("rm", "-f", cid)
        docker("rmi", "-f", cls.probe)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def leftovers(cls) -> list[str]:
        ids = docker("ps", "-a", "-q", "--filter", "label=devloop.sandbox=1").stdout.split()
        mine = []
        for cid in ids:
            mounts = docker("inspect", "--format", "{{range .Mounts}}{{.Source}}\n{{end}}", cid).stdout.split("\n")
            if any(m and Path(m).resolve() == cls.target for m in mounts):
                mine.append(cid)
        return mine

    def run_sandbox(self, *cmd: str, opts: tuple[str, ...] = (), image: str = "t", stdin: str = "",
                    env: dict | None = None, target: Path | None = None, limit: float = 90) -> tuple[subprocess.CompletedProcess, Path]:
        scratch = Path(tempfile.mkdtemp(dir=self.tmp))
        extra = ("--target", str(target)) if target else ()
        args = [sys.executable, str(RUNNER), "--image", image, "--scratch", str(scratch), *extra, *opts, "--", *cmd]
        done = subprocess.run(args, cwd=self.target, input=stdin, capture_output=True, text=True,
                              env=env if env is not None else os.environ, timeout=limit)
        self.assertEqual(self.leftovers(), [], "container left behind")
        return done, scratch

    def test_command_io(self) -> None:
        cases = [
            (("echo", "hi"), "", 0, "hi\n"),
            (("sh", "-c", "exit 3"), "", 3, ""),
            (("cat",), "abc", 0, "abc"),
        ]
        for cmd, stdin, code, out in cases:
            with self.subTest(cmd=cmd):
                done, _ = self.run_sandbox(*cmd, stdin=stdin)
                self.assertEqual((done.returncode, done.stdout), (code, out), done.stderr)

    def test_no_network_and_own_loopback(self) -> None:
        for cmd in (("wget", "-T", "3", "-q", "-O-", "http://1.1.1.1"), ("nslookup", "example.com")):
            with self.subTest(cmd=cmd):
                done, _ = self.run_sandbox(*cmd)
                self.assertNotEqual(done.returncode, 0, done.stdout)

        class Hello(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"host-secret")

            def log_message(self, *args: object) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Hello)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            done, _ = self.run_sandbox("wget", "-T", "3", "-q", "-O-", f"http://127.0.0.1:{port}/")
            self.assertNotEqual(done.returncode, 0)
            self.assertNotIn("host-secret", done.stdout)
        finally:
            server.shutdown()
            server.server_close()
        script = ("mkdir -p /scratch/www && echo inside > /scratch/www/index.html && "
                  "httpd -p 127.0.0.1:8080 -h /scratch/www && sleep 1 && wget -T 3 -q -O- http://127.0.0.1:8080/")
        done, _ = self.run_sandbox("sh", "-c", script)
        self.assertEqual((done.returncode, done.stdout), (0, "inside\n"), done.stderr)

    def test_writes_only_reach_scratch(self) -> None:
        script = "for p in /target/x /x /etc/x /tmp/x; do touch $p 2>/dev/null && echo WROTE $p; done; echo ok > /scratch/a"
        done, scratch = self.run_sandbox("sh", "-c", script)
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual((scratch / "a").read_text(), "ok\n")
        self.assertFalse((self.target / "x").exists())

    def test_resource_limits(self) -> None:
        for hold in (629145600, 200000000):  # 200 MB fits in 256m plus swap: only the swap cap stops it
            with self.subTest(hold=hold):
                done, _ = self.run_sandbox("sh", "-c", f"x=$(head -c {hold} /dev/zero | tr '\\0' a); echo ${{#x}}",
                                           opts=("--memory", "256m"))
                self.assertEqual(done.returncode, 137, done.stderr)

        forks = "for i in $(seq 200); do sleep 30 >/dev/null 2>&1 & done"
        done, _ = self.run_sandbox("sh", "-c", forks, opts=("--pids", "64", "--timeout", "20"))
        self.assertTrue(done.returncode != 0 or "fork" in done.stderr, done.stderr)

        fill = "dd if=/dev/zero of=/scratch/big bs=1048576 count=100; s=$?; rm -f /scratch/big; exit $s"
        done, _ = self.run_sandbox("sh", "-c", fill, opts=("--scratch-size", "16m"))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("No space left", done.stderr)

        big = "dd if=/dev/zero of=/scratch/f bs=1048576 count=8 2>/dev/null; echo rc=$?"
        done, scratch = self.run_sandbox("sh", "-c", big, opts=("--file-size", "4194304"))
        self.assertNotEqual(done.stdout.strip(), "rc=0", done.stderr)
        self.assertLessEqual((scratch / "f").stat().st_size, 4194304)

    def test_scratch_copy_rules(self) -> None:
        done, scratch = self.run_sandbox("sh", "-c", "head -c 2097152 /dev/zero > /scratch/big",
                                         opts=("--scratch-max-bytes", "1048576"))
        self.assertEqual(done.returncode, 125)
        self.assertEqual(done.stderr, "sandbox-run: scratch over 1048576 bytes\n")
        self.assertEqual(list(scratch.iterdir()), [])

        plant = "ln -s /etc/passwd /scratch/l && ln -s / /scratch/d && mkfifo /scratch/p && echo x > /scratch/f"
        before = sorted(p.name for p in self.tmp.iterdir())
        done, scratch = self.run_sandbox("sh", "-c", plant)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([p.name for p in scratch.iterdir()], ["f"])
        self.assertEqual((scratch / "f").read_text(), "x\n")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), sorted([*before, scratch.name]))

    def test_environment_is_empty(self) -> None:
        env = {**os.environ, "DEVLOOP_PROBE": "secret"}
        expected = {f"PATH={IMAGE_PATH}", "HOME=/scratch/home", "TMPDIR=/scratch/tmp",
                    "XDG_CACHE_HOME=/scratch/cache", "LANG=C.UTF-8"}
        for opts, extra in (((), set()), (("--env", "A=1"), {"A=1"})):
            with self.subTest(opts=opts):
                done, _ = self.run_sandbox("env", image="probe", opts=opts, env=env)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(set(done.stdout.splitlines()), expected | extra)
                self.assertEqual(len(done.stdout.splitlines()), len(expected | extra))

    def test_user_and_capabilities(self) -> None:
        done, _ = self.run_sandbox("id", "-u")
        self.assertEqual(done.stdout, "65534\n")
        done, _ = self.run_sandbox("grep", "-E", "CapEff|CapBnd", "/proc/self/status")
        self.assertEqual(done.stdout.split(), ["CapEff:", "0000000000000000", "CapBnd:", "0000000000000000"])

    def test_timeout(self) -> None:
        start = time.monotonic()
        done, _ = self.run_sandbox("sleep", "30", opts=("--timeout", "2"))
        self.assertEqual(done.returncode, 124, done.stderr)
        self.assertLess(time.monotonic() - start, 15)

    def test_runner_errors(self) -> None:
        empty = self.tmp / "empty-path"
        empty.mkdir(exist_ok=True)
        nokey = self.tmp / "nokey"
        nokey.mkdir(exist_ok=True)
        (nokey / "dev-loop.json").write_text("{}")
        cases = [
            ("t", None, {**os.environ, "PATH": str(empty)}, "sandbox-run: unavailable"),
            ("nope", None, None, "sandbox-run: unknown image nope\n"),
            ("t", nokey, None, "sandbox-run: unknown image t\n"),
            ("gone", None, None, f"sandbox-run: image missing {self.missing}\n"),
        ]
        for image, target, env, err in cases:
            with self.subTest(image=image, target=target):
                done, _ = self.run_sandbox("true", image=image, target=target, env=env)
                self.assertEqual(done.returncode, 125)
                self.assertTrue(done.stderr.startswith(err), done.stderr)
        self.assertNotEqual(docker("image", "inspect", self.missing).returncode, 0)
        done, _ = self.run_sandbox("true", opts=("--timeout", "zero"))
        self.assertEqual(done.returncode, 125)
        self.assertTrue(done.stderr.startswith("sandbox-run: "), done.stderr)
        self.assertEqual(len(done.stderr.splitlines()), 1, done.stderr)

    def test_leftover_processes_cannot_reach_the_copy(self) -> None:
        info = tarfile.TarInfo("big")  # PAX sparse member: 10 bytes stored, 50 MB on extraction
        info.size = 10
        info.pax_headers = {"GNU.sparse.map": "52428800,10", "GNU.sparse.size": "52428810"}
        evil = io.BytesIO()
        with tarfile.open(fileobj=evil, mode="w", format=tarfile.PAX_FORMAT) as tar:
            tar.addfile(info, io.BytesIO(b"x" * 10))
        (self.target / "evil.tar").write_bytes(evil.getvalue())
        inject = ("(until p=$(pidof tar); do :; done; kill -STOP $p; "
                  "cat /target/evil.tar > /proc/$p/fd/1; kill -CONT $p) >/dev/null 2>&1 & echo real > /scratch/real")
        done, scratch = self.run_sandbox("sh", "-c", inject, limit=60)
        files = {p.name: p.stat().st_size for p in scratch.rglob("*") if p.is_file()}
        self.assertTrue(done.returncode == 125 or files == {"real": 5}, (done.returncode, files, done.stderr))
        self.assertLessEqual(sum(files.values()), 5)

        stopper = "(while :; do p=$(pidof tar) && kill -STOP $p; done) >/dev/null 2>&1 & echo real > /scratch/real"
        start = time.monotonic()
        done, scratch = self.run_sandbox("sh", "-c", stopper, opts=("--timeout", "5"), limit=40)
        self.assertLess(time.monotonic() - start, 30)
        self.assertEqual([p.name for p in scratch.iterdir()], ["real"], done.stderr)

    def test_signal_removes_container(self) -> None:
        scratch = Path(tempfile.mkdtemp(dir=self.tmp))
        cmd = [sys.executable, str(RUNNER), "--image", "t", "--scratch", str(scratch), "--", "sleep", "30"]
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig):
                proc = subprocess.Popen(cmd, cwd=self.target, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 20
                while not self.leftovers() and time.monotonic() < deadline:
                    time.sleep(0.2)
                time.sleep(1)
                proc.send_signal(sig)
                proc.wait(timeout=20)
                self.assertEqual(self.leftovers(), [])
        for delay in (0.3, 0.5, 0.7, 0.9):  # a signal near `docker run -d` must not let the container outlive the runner
            with self.subTest(delay=delay):
                proc = subprocess.Popen(cmd, cwd=self.target, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(delay)
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=20)
                time.sleep(1)
                self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()

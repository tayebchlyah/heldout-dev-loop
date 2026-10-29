#!/usr/bin/env python3
"""Tests for sandbox-run.py against a real Docker engine and the pinned busybox image."""

from __future__ import annotations

import http.server
import importlib.util
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
import unittest.mock
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parents[2] / "skill" / "scripts" / "sandbox-run.py"
IMAGE = "busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
IMAGE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

_runner_spec = importlib.util.spec_from_file_location("sandbox_run", RUNNER)
if _runner_spec is None or _runner_spec.loader is None:
    raise ImportError("cannot load sandbox-run.py")
sandbox_run = importlib.util.module_from_spec(_runner_spec)
_runner_spec.loader.exec_module(sandbox_run)


def sparse_tar() -> bytes:
    info = tarfile.TarInfo("big")  # PAX sparse member: 10 bytes stored, 50 MB on extraction
    info.size = 10
    info.pax_headers = {"GNU.sparse.map": "52428800,10", "GNU.sparse.size": "52428810"}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(info, io.BytesIO(b"x" * 10))
    return out.getvalue()


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
            (("sh", "-c", "cp /bin/busybox /scratch/busybox && /scratch/busybox echo built"), "", 0, "built\n"),
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
        self.assertEqual((scratch / "a.txt").read_text(), "ok\n")
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
        self.assertLessEqual((scratch / "f.txt").stat().st_size, 4194304)

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
        self.assertEqual([p.name for p in scratch.iterdir()], ["f.txt"])
        self.assertEqual((scratch / "f.txt").read_text(), "x\n")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), sorted([*before, scratch.name]))

        private = "mkdir -m 700 /scratch/d && echo s > /scratch/d/f && chmod 600 /scratch/d/f"
        done, scratch = self.run_sandbox("sh", "-c", private)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((scratch / "d" / "f.txt").read_text(), "s\n")

        layouts = ("mkdir -p /scratch/r/.git /scratch/b/objects /scratch/b/refs && echo x > /scratch/r/.git/HEAD"
                   " && echo x > /scratch/r/.git/config && echo x > /scratch/b/HEAD && echo x > /scratch/b/config"
                   " && echo x > /scratch/b/objects/k && echo x > /scratch/b/refs/k")
        done, scratch = self.run_sandbox("sh", "-c", layouts)
        self.assertEqual(done.returncode, 0, done.stderr)
        files = [p for p in scratch.rglob("*") if p.is_file()]
        self.assertEqual(len(files), 4)
        for p in files:
            self.assertNotIn(".git", p.relative_to(scratch).parts)
            self.assertTrue(p.name.endswith(".txt"), p)

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
        (self.target / "evil.tar").write_bytes(sparse_tar())
        inject = ("(until p=$(pidof tar); do :; done; kill -STOP $p; "
                  "cat /target/evil.tar > /proc/$p/fd/1; kill -CONT $p) >/dev/null 2>&1 & echo real > /scratch/real")
        done, scratch = self.run_sandbox("sh", "-c", inject, limit=60)
        files = {p.name: p.stat().st_size for p in scratch.rglob("*") if p.is_file()}
        self.assertTrue(done.returncode == 125 or files == {"real.txt": 5}, (done.returncode, files, done.stderr))
        self.assertLessEqual(sum(files.values()), 5)

        stopper = "(while :; do p=$(pidof tar) && kill -STOP $p; done) >/dev/null 2>&1 & echo real > /scratch/real"
        start = time.monotonic()
        done, scratch = self.run_sandbox("sh", "-c", stopper, opts=("--timeout", "5"), limit=40)
        self.assertLess(time.monotonic() - start, 30)
        self.assertEqual([p.name for p in scratch.iterdir()], ["real.txt"], done.stderr)

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


class SandboxExtractTests(unittest.TestCase):
    """The copy-back filter alone, no Docker: in the CLI the earlier layers keep hostile tars from reaching it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.scratch = self.tmp / "scratch"
        self.scratch.mkdir()

    def written(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.tmp)): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file() or p.is_symlink()}

    def test_only_safe_regular_files_are_written(self) -> None:
        def member(name: str, kind: bytes = tarfile.REGTYPE, data: bytes = b"", link: str = "") -> tuple:
            info = tarfile.TarInfo(name)
            info.type, info.linkname, info.size = kind, link, len(data)
            return info, io.BytesIO(data)

        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for info, data in (member("ok/f", data=b"safe"), member("l", tarfile.SYMTYPE, link="/etc/passwd"),
                               member("h", tarfile.LNKTYPE, link="ok/f"), member("../x", data=b"up"),
                               member("/abs", data=b"abs"), member("p", tarfile.FIFOTYPE)):
                tar.addfile(info, data)
        cases = [
            ("mixed", out.getvalue(), {"scratch/ok/f.txt": b"safe"}),
            ("sparse", sparse_tar(), {}),
        ]
        for label, data, expected in cases:
            with self.subTest(label):
                shutil.rmtree(self.scratch)
                self.scratch.mkdir()
                sandbox_run.extract(data, self.scratch, 1 << 20)
                self.assertEqual(self.written(), expected)

    def test_copy_back_makes_no_repository_and_only_inert_names(self) -> None:
        out = io.BytesIO()
        long = "a" * 252
        names = ["r/.git/HEAD", "r/.git/config", "c/.GIT/hooks/x", ".git", ".gitattributes", "g/.gitignore",
                 "b/HEAD", "b/config", "b/objects/k", "b/refs/k", "d/head", "h/.hg/hgrc",
                 "n/x.md", "n/Y.MD", "n/x.svg", "evidence/check-01.txt", "evidence/UP.TXT", f"l/{long}", "l/after"]
        with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
        sandbox_run.extract(out.getvalue(), self.scratch, 1 << 20)
        self.assertEqual(sorted(self.written()), [
            "scratch/.gitattributes.txt", "scratch/b/HEAD.txt", "scratch/b/config.txt", "scratch/b/objects/k.txt",
            "scratch/b/refs/k.txt", "scratch/d/head.txt", "scratch/evidence/UP.TXT",
            "scratch/evidence/check-01.txt", "scratch/g/.gitignore.txt", "scratch/h/.hg/hgrc.txt",
            "scratch/l/after.txt", "scratch/n/Y.MD.txt", "scratch/n/x.md.txt", "scratch/n/x.svg.txt"])
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_CEILING_DIRECTORIES=str(self.tmp))
        for folder in (self.scratch, self.scratch / "b", self.scratch / "d", self.scratch / "h"):
            found = subprocess.run(["git", "-C", str(folder), "rev-parse", "--git-dir"], capture_output=True,
                                   text=True, env=env)
            self.assertNotEqual(found.returncode, 0, found.stdout)

    def test_size_headers_over_the_cap_write_nothing(self) -> None:
        small = tarfile.TarInfo("a")
        small.size = 3
        huge = tarfile.TarInfo("b")
        huge.size = 10 ** 9  # header claims 1 GB, the stream holds one block
        data = (small.tobuf(tarfile.GNU_FORMAT) + b"abc".ljust(512, b"\0")
                + huge.tobuf(tarfile.GNU_FORMAT) + b"\0" * 512)
        with self.assertRaises(sandbox_run.RunnerError) as err:
            sandbox_run.extract(data, self.scratch, 1 << 20)
        self.assertEqual(str(err.exception), f"scratch over {1 << 20} bytes")
        self.assertEqual(self.written(), {})


class KillLeftoversTests(unittest.TestCase):
    """The leftover wait alone, on a fake clock and a stub docker: no Docker."""

    def wait(self, exits_at: float) -> list[str]:
        now = [0.0]
        calls = []

        def docker(*args: str) -> subprocess.CompletedProcess:
            calls.append(args[0])
            out = "PID STAT\n1 S\n" + ("" if now[0] >= exits_at else "7 D\n")
            return subprocess.CompletedProcess(args, 0 if args[0] == "top" else 1, out if args[0] == "top" else "", "")

        def sleep(seconds: float) -> None:
            now[0] += seconds

        stub = unittest.mock.patch.multiple(sandbox_run, docker=docker)
        timing = unittest.mock.patch.multiple(sandbox_run.time, monotonic=lambda: now[0], sleep=sleep)
        with stub, timing:
            sandbox_run.kill_leftovers("c")
        return calls

    def test_a_slow_exit_inside_the_deadline_is_waited_for(self) -> None:
        self.assertIn("top", self.wait(exits_at=5.0))

    def test_a_process_past_the_deadline_is_named(self) -> None:
        with self.assertRaisesRegex(sandbox_run.RunnerError, "leftover processes: 7 D"):
            self.wait(exits_at=60.0)


if __name__ == "__main__":
    unittest.main()

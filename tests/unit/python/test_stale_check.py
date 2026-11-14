#!/usr/bin/env python3
"""Tests for the stale check of feature_gate.py acceptance."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from test_feature_gate import RepoCase, git, junit

from feature_gate import created_seconds, stale_check

FAKE_DOCKER = """#!/usr/bin/env python3
import json, os, sys
spec = json.load(open(os.environ["FAKE_DOCKER"]))
args = sys.argv[1:]
key = "config" if "config" in args else "ps" if "ps" in args else "inspect"
if spec.get("fail") == key:
    sys.exit(1)
if key == "config":
    print(json.dumps({"services": spec["services"]}))
elif key == "ps":
    print("\\n".join(spec["ps"].get(args[-1], [])))
else:
    print(json.dumps([c for c in spec["inspect"] if any(c["Id"].startswith(i) for i in args[1:])]))
"""
T0, T1, T2 = "2026-10-01T10:00:00Z", "2026-10-02T10:00:00Z", "2026-10-03T10:00:00Z"
API_ID, DB_ID = "a" * 12 + "1" * 52, "b" * 12 + "2" * 52


class StaleCheckTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo({"acceptance": "./run.sh"}, when=T0)
        (self.repo / "compose.yaml").write_text("services: {}\n")
        (self.repo / "app.py").write_text("x = 2\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "code", when=T1)
        self.code_sha = git(self.repo, "rev-parse", "HEAD")
        bins = self.tmp / "bin"
        bins.mkdir()
        (bins / "docker").write_text(FAKE_DOCKER)
        (bins / "docker").chmod(0o755)
        self.spec_file = self.tmp / "docker.json"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")},
                    "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER": str(self.spec_file)}

    def docker(self, api: str | None, db: str | None = None, **extra: object) -> None:
        services = {"api": {"build": {"context": "."}}, "db": {"image": "postgres"}}
        ps = {"api": [API_ID[:12]] if api else [], "db": [DB_ID[:12]] if db else []}
        inspect = [{"Id": i, "Created": t} for i, t in ((API_ID, api), (DB_ID, db)) if t]
        self.spec_file.write_text(json.dumps({"services": services, "ps": ps, "inspect": inspect, **extra}))

    def test_no_compose_skipped(self) -> None:
        (self.repo / "compose.yaml").unlink()
        self.assertEqual(stale_check(self.repo, self.env, None),
                         {"state": "skipped", "reason": "no-compose", "project": None})

    def test_compose_file_env_counts(self) -> None:
        (self.repo / "compose.yaml").unlink()
        self.docker(api="2026-10-02T11:00:00Z")
        self.assertEqual(stale_check(self.repo, {**self.env, "COMPOSE_FILE": "x.yaml"}, None)["state"], "fresh")

    def test_no_build_service(self) -> None:
        self.spec_file.write_text(json.dumps({"services": {"db": {"image": "x"}}, "ps": {}, "inspect": []}))
        self.assertEqual(stale_check(self.repo, self.env, None),
                         {"state": "skipped", "reason": "no-build-service", "project": None})

    def test_no_container(self) -> None:
        self.docker(api=None)
        self.assertEqual(stale_check(self.repo, self.env, None),
                         {"state": "skipped", "reason": "no-container", "project": None})

    def test_old_build_container_is_stale(self) -> None:
        self.docker(api="2026-10-02T09:59:59.900Z")
        self.assertEqual(stale_check(self.repo, self.env, None), {"state": "stale", "checked": ["api"],
                                                                  "services": ["api"], "reference": self.code_sha,
                                                                  "project": None})
        (self.repo / "fixture.xml").write_text(junit([]))
        code, out, _ = self.devloop("acceptance", cwd=self.repo, env=self.env)
        self.assertEqual((code, out["counted"], out["guard"], out["stale"]["state"]), (1, False, None, "stale"))
        runs = json.loads(self.record(self.repo).read_text())["runs"]
        self.assertEqual((runs[-1]["stale"], runs[-1]["counted"]), ("stale", False))

    def test_same_second_is_fresh(self) -> None:
        self.docker(api="2026-10-02T10:00:00.100Z")
        self.assertEqual(stale_check(self.repo, self.env, None)["state"], "fresh")

    def test_old_image_only_container_fresh(self) -> None:
        self.docker(api="2026-10-02T11:00:00Z", db="2026-09-01T00:00:00Z")
        self.assertEqual(stale_check(self.repo, self.env, None), {"state": "fresh", "checked": ["api"],
                                                                  "services": [], "reference": self.code_sha,
                                                                  "project": None})

    def test_test_and_doc_commit_not_reference(self) -> None:
        (self.repo / "tests" / "acceptance").mkdir(parents=True)
        (self.repo / "tests" / "acceptance" / "x_test.py").write_text("")
        (self.repo / "README.md").write_text("r\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "tests", when=T2)
        self.docker(api="2026-10-02T11:00:00Z")
        self.assertEqual(stale_check(self.repo, self.env, None)["state"], "fresh")
        self.assertEqual(stale_check(self.repo, self.env, None)["reference"], self.code_sha)

    def test_ci_commit_is_reference(self) -> None:
        (self.repo / ".github" / "workflows").mkdir(parents=True)
        (self.repo / ".github" / "workflows" / "x.test.yml").write_text("on: push\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "ci", when=T2)
        self.docker(api="2026-10-02T11:00:00Z")
        out = stale_check(self.repo, self.env, None)
        self.assertEqual((out["state"], out["reference"]), ("stale", git(self.repo, "rev-parse", "HEAD")))

    def test_non_ascii_test_path_not_reference(self) -> None:
        (self.repo / "tests" / "acceptance").mkdir(parents=True)
        (self.repo / "tests" / "acceptance" / "café_test.py").write_text("")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "tests", when=T2)
        self.docker(api="2026-10-02T11:00:00Z")
        out = stale_check(self.repo, self.env, None)
        self.assertEqual((out["state"], out["reference"]), ("fresh", self.code_sha))

    def test_docker_fails_error(self) -> None:
        for step in ("config", "ps", "inspect"):
            self.docker(api="2026-10-02T11:00:00Z", fail=step)
            out = stale_check(self.repo, self.env, None)
            self.assertEqual(out["state"], "error", step)
            self.assertTrue(out["message"])
        self.docker(api="2026-10-02T11:00:00Z", fail="inspect")
        (self.repo / "fixture.xml").write_text(junit([]))
        _, out, _ = self.devloop("acceptance", cwd=self.repo, env=self.env)
        self.assertEqual((out["stale"]["state"], out["counted"]), ("error", False))

    def test_bad_time_error(self) -> None:
        self.docker(api="yesterday")
        self.assertEqual(stale_check(self.repo, self.env, None)["state"], "error")

    def test_short_ids_match_full_ids(self) -> None:
        self.docker(api="2026-10-02T11:00:00Z", db="2026-10-02T11:00:00Z")
        self.assertEqual(stale_check(self.repo, self.env, None)["checked"], ["api"])

    def test_nine_digit_fraction_with_offset(self) -> None:
        self.assertEqual(created_seconds("2026-10-26T10:00:00.123456789+02:00"),
                         int(datetime(2026, 10, 26, 8, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(created_seconds("2026-10-26T08:00:00Z"),
                         int(datetime(2026, 10, 26, 8, tzinfo=timezone.utc).timestamp()))
        with self.assertRaises(ValueError):
            created_seconds("2026-10-26 08:00:00")


FAKE_PROJECT_DOCKER = """#!/usr/bin/env python3
import json, os, sys
spec = json.load(open(os.environ["FAKE_DOCKER"]))
args = sys.argv[1:]
with open(spec["log"], "a") as log:
    log.write(json.dumps(args) + "\\n")
key = "config" if "config" in args else "ps" if "ps" in args else "inspect"
if spec.get("fail") == key:
    sys.exit(1)
labels = [(c["Id"], c["Config"]["Labels"]) for c in spec["containers"]]
if key == "config":
    print(json.dumps({"services": spec["services"]}))
elif key == "ps" and "--filter" in args:
    name = args[-1].removeprefix("label=com.docker.compose.project=")
    print("\\n".join(i for i, label in labels if label.get("com.docker.compose.project") == name))
elif key == "ps":
    print("\\n".join(i[:12] for i, label in labels if label.get("com.docker.compose.service") == args[-1]))
else:
    print(json.dumps([c for c in spec["containers"] if any(c["Id"].startswith(i) for i in args[1:])]))
"""
BEFORE, AFTER = "2026-10-02T09:00:00Z", "2026-10-02T11:00:00Z"
KEY = "echo acme-wt"


class ProjectStaleCheckTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo({"acceptance": "./run.sh", "compose_project": KEY}, when=T0)
        (self.repo / "app.py").write_text("x = 2\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "code", when=T1)
        self.code_sha = git(self.repo, "rev-parse", "HEAD")
        bins = self.tmp / "bin"
        bins.mkdir()
        (bins / "docker").write_text(FAKE_PROJECT_DOCKER)
        (bins / "docker").chmod(0o755)
        self.spec_file = self.tmp / "docker.json"
        self.log = self.tmp / "docker.log"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")},
                    "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER": str(self.spec_file)}

    def box(self, n: int, service: str, created: str, project: str = "acme-wt", wd: str | None = "{top}/stack",
            files: str | None = "{top}/stack/compose.yml", oneoff: str = "False") -> dict:
        labels = {"com.docker.compose.project": project, "com.docker.compose.project.working_dir": wd,
                  "com.docker.compose.project.config_files": files, "com.docker.compose.service": service,
                  "com.docker.compose.oneoff": oneoff}
        return {"Id": f"{n:x}" * 64, "Created": created,
                "Config": {"Labels": {k: v.format(top=self.repo) for k, v in labels.items() if v is not None}}}

    def spec(self, containers: list[dict], services: dict | None = None, **extra: object) -> None:
        services = services or {"api": {"build": {"context": "."}}, "db": {"image": "postgres"}}
        self.log.unlink(missing_ok=True)
        self.spec_file.write_text(json.dumps({"containers": containers, "services": services, "log": str(self.log),
                                              **extra}))

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_states(self) -> None:
        fresh = {"state": "fresh", "checked": ["api"], "services": [], "reference": self.code_sha,
                 "project": "acme-wt"}
        stale = {**fresh, "state": "stale", "services": ["api"]}
        none = {"state": "skipped", "reason": "no-container", "project": "acme-wt"}
        folder = self.repo.name
        rows = [
            ("old api", KEY, [self.box(1, "api", BEFORE)], None, stale),
            ("new api, no compose file at the top", KEY, [self.box(1, "api", AFTER)], None, fresh),
            ("old db", KEY, [self.box(1, "api", AFTER), self.box(2, "db", BEFORE)], None, fresh),
            ("old oneoff api", KEY, [self.box(1, "api", AFTER), self.box(3, "api", BEFORE, oneoff="True")], None,
             fresh),
            ("old and new api", KEY, [self.box(1, "api", AFTER), self.box(2, "api", BEFORE)], None, stale),
            ("old api of another project", KEY,
             [self.box(1, "api", AFTER), self.box(4, "api", BEFORE, project="other")], None, fresh),
            ("no container", KEY, [], None, none),
            ("only db", KEY, [self.box(2, "db", BEFORE)], None, none),
            ("only oneoff", KEY, [self.box(3, "api", BEFORE, oneoff="True")], None, none),
            ("no build service", KEY, [self.box(1, "api", AFTER)], {"db": {"image": "x"}},
             {"state": "skipped", "reason": "no-build-service", "project": "acme-wt"}),
            ("padded output", "printf ' acme-wt \\n\\n'", [self.box(1, "api", AFTER)], None, fresh),
            ("folder name", 'basename "$PWD"', [self.box(1, "api", AFTER, project=folder)], None,
             {**fresh, "project": folder}),
        ]
        for name, key, containers, services, expected in rows:
            with self.subTest(name):
                self.spec(containers, services)
                self.assertEqual(stale_check(self.repo, self.env, key), expected)

    def test_calls(self) -> None:
        top = str(self.repo)
        self.spec([self.box(1, "api", AFTER, files="{top}/stack/compose.yml,{top}/e2e.yml")])
        self.assertEqual(stale_check(self.repo, self.env, KEY)["state"], "fresh")
        self.assertIn(["ps", "-a", "-q", "--no-trunc", "--filter", "label=com.docker.compose.project=acme-wt"],
                      self.calls())
        self.assertIn(["compose", "-p", "acme-wt", "--project-directory", f"{top}/stack", "-f",
                       f"{top}/stack/compose.yml", "-f", f"{top}/e2e.yml", "--profile", "test", "config", "--format",
                       "json"], self.calls())
        self.spec([self.box(1, "api", AFTER, project="acme wt")])
        self.assertEqual(stale_check(self.repo, self.env, "printf 'acme wt'")["project"], "acme wt")
        self.assertIn("label=com.docker.compose.project=acme wt", self.calls()[0])
        self.assertEqual(self.calls()[-1][:3], ["compose", "-p", "acme wt"])

    def test_errors(self) -> None:
        top = str(self.repo)
        new = self.box(1, "api", AFTER)
        rows = [
            ("two worktrees", KEY, [new, self.box(2, "api", AFTER, wd="/elsewhere/stack")], {},
             [f"/elsewhere/stack, {top}/stack", "one name for each worktree"], True),
            ("two file sets", KEY, [new, self.box(2, "api", AFTER, files="{top}/other.yml")], {},
             ["reset the stack"], True),
            ("label absent", KEY, [self.box(1, "api", AFTER, files=None)], {}, ["reset the stack"], True),
            ("config fails", KEY, [new], {"fail": "config"}, ["reset the stack"], True),
            ("inspect fails", KEY, [new], {"fail": "inspect"}, ["docker inspect"], True),
            ("key exits 3", "echo boom >&2; exit 3", [new], {}, ["exit 3", "boom"], False),
            ("key prints nothing", "true", [new], {}, ["one line"], False),
            ("key prints two lines", "printf 'a\\nb\\n'", [new], {}, ["one line"], False),
            ("key reads stdin", "cat", [new], {}, ["one line"], False),
        ]
        for name, key, containers, extra, needles, called in rows:
            with self.subTest(name):
                self.spec(containers, **extra)
                out = stale_check(self.repo, self.env, key)
                self.assertEqual((out["state"], out["project"]), ("error", None))
                for needle in needles:
                    self.assertIn(needle, out["message"])
                self.assertEqual(bool(self.calls()), called)

    def test_name_source(self) -> None:
        (self.repo / "compose.yaml").write_text("services: {}\n")
        self.spec([self.box(1, "api", BEFORE)])
        out = stale_check(self.repo, {**self.env, "COMPOSE_PROJECT_NAME": "acme-env"}, "touch key.marker; " + KEY)
        self.assertEqual((out["state"], out["project"]), ("stale", "acme-env"))
        self.assertFalse((self.repo / "key.marker").exists())
        self.assertIn(["compose", "--profile", "test", "ps", "-a", "-q", "api"], self.calls())
        self.assertFalse(any(word.startswith("label=") for call in self.calls() for word in call))
        self.spec([self.box(1, "api", BEFORE)])
        out = stale_check(self.repo, {**self.env, "COMPOSE_PROJECT_NAME": ""}, KEY)
        self.assertEqual((out["state"], out["project"]), ("stale", "acme-wt"))
        self.assertIn("label=com.docker.compose.project=acme-wt", self.calls()[0])
        self.spec([self.box(1, "api", BEFORE)])
        self.assertEqual(stale_check(self.repo, self.env, None)["project"], None)
        self.assertIn(["compose", "--profile", "test", "ps", "-a", "-q", "api"], self.calls())
        self.spec([self.box(1, "api", BEFORE)], fail="config")
        out = stale_check(self.repo, {**self.env, "COMPOSE_PROJECT_NAME": "acme-env"}, None)
        self.assertEqual((out["state"], out["project"]), ("error", None))

    def test_cli(self) -> None:
        (self.repo / "fixture.xml").write_text(junit([]))
        self.spec([self.box(1, "api", BEFORE)])
        code, out, _ = self.devloop("acceptance", cwd=self.repo, env=self.env)
        self.assertEqual((code, out["counted"], out["guard"], out["stale"]["state"], out["stale"]["project"]),
                         (1, False, None, "stale", "acme-wt"))
        runs = json.loads(self.record(self.repo).read_text())["runs"]
        self.assertEqual((runs[-1]["stale"], runs[-1]["counted"]), ("stale", False))
        self.spec([self.box(1, "api", AFTER)])
        code, out, _ = self.devloop("acceptance", cwd=self.repo, env=self.env)
        self.assertEqual((code, out["counted"], out["stale"]),
                         (0, True, {"state": "fresh", "checked": ["api"], "services": [],
                                    "reference": self.code_sha, "project": "acme-wt"}))
        self.spec([self.box(1, "api", AFTER), self.box(2, "api", AFTER, wd="/elsewhere/stack")])
        code, out, _ = self.devloop("acceptance", cwd=self.repo, env=self.env)
        self.assertEqual((code, out["counted"], out["guard"], out["stale"]["state"], out["stale"]["project"]),
                         (1, False, None, "error", None))

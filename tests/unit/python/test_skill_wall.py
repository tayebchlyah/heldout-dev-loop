"""`skill-wall`: the project skill rule of `implementer`, on `Skill` hook calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_devloop import DEVLOOP, git, put
from test_search_wall import search_repo

from seal import skills  # noqa: E402

ALLOW = {"deny": False, "reason": None}
PLAIN = "---\nname: pg-guide\ndescription: x\n---\nUse the index.\n"
SKILL = ".claude/skills/pg-guide/SKILL.md"
CONFIG = '{"version": "3.0.0", "walled_skills": ["pg-guide"]}\n'


def write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def commit(top: Path) -> None:
    git(top, "add", "-A")
    git(top, "commit", "-qm", "x")


def skill_repo(root: Path, *, config: str = CONFIG, text: str | bytes | None = PLAIN, cli: str | None = "link") -> Path:
    top = search_repo(root, suite=False, cli=cli)
    git(top, "config", "core.autocrlf", "false")
    put(top / "dev-loop.json", config)
    if text is not None:
        write(top / SKILL, text)
    commit(top)
    git(top, "checkout", "-q", "-B", "main")
    return top


def hook(top: Path | None, skill: object = "pg-guide") -> dict:
    tool_input = {} if skill is None else {"skill": skill}
    place = {} if top is None else {"cwd": str(top)}
    return {"agent_id": "a", "agent_type": "implementer", "tool_name": "Skill", "tool_input": tool_input, **place}


def verdict(top: Path | None, skill: object = "pg-guide", env: dict[str, str] | None = None) -> dict:
    return skills.decide(hook(top, skill), env or {})


def denied(reason: str) -> dict:
    return {"deny": True, "reason": reason}


class SkillWallTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.count = 0

    def repo(self, **kwargs) -> Path:
        self.count += 1
        return skill_repo(self.tmp / f"repo{self.count}", **kwargs)

    def linked(self, target: str = "../../.agents/guides/pg-guide") -> Path:
        top = self.repo(text=None)
        write(top / target.removeprefix("../../") / "SKILL.md", PLAIN)
        (top / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (top / ".claude" / "skills" / "pg-guide").symlink_to(target)
        commit(top)
        return top

    def test_allows_an_equal_plain_skill(self) -> None:
        self.assertEqual(verdict(self.repo()), ALLOW)

    def test_allows_a_link_with_the_same_target(self) -> None:
        self.assertEqual(verdict(self.linked()), ALLOW)

    def test_prefers_the_origin_ref(self) -> None:
        top = self.repo(config='{"version": "3.0.0"}\n')
        put(top / "dev-loop.json", CONFIG)
        commit(top)
        git(top, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(top, "update-ref", "refs/heads/main", "HEAD~1")
        self.assertEqual(verdict(top), ALLOW)
        other = self.repo()
        put(other / "dev-loop.json", '{"version": "3.0.0"}\n')
        commit(other)
        git(other, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(other, "update-ref", "refs/heads/main", "HEAD~1")
        self.assertEqual(verdict(other), denied("not in walled_skills"))

    def test_first_failed_condition_of_the_list(self) -> None:
        none = self.repo(config='{"version": "3.0.0"}\n')
        branch = self.repo(config='{"version": "3.0.0"}\n')
        git(branch, "checkout", "-q", "-b", "feat/x")
        put(branch / "dev-loop.json", CONFIG)
        commit(branch)
        text = self.repo(config='{"walled_skills": "pg-guide"}\n')
        plugin = self.repo(config='{"walled_skills": ["plugin:pg-guide"]}\n')
        broken = self.repo(config="not json\n")
        empty = self.tmp / "empty"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        plain = self.repo()
        rows = [(hook(branch), "branch list"), (hook(none), "no key"), (hook(text), "text value"),
                (hook(plugin, "plugin:pg-guide"), "plugin name"), (hook(broken), "not JSON"), (hook(empty), "no main ref"),
                (hook(plain, "pg-guide\n"), "line end"), (hook(plain, 5), "number"), (hook(plain, ["pg-guide"]), "list"),
                (hook(plain, None), "no skill"), ([], "not an object")]
        for payload, label in rows:
            with self.subTest(label):
                self.assertEqual(skills.decide(payload, {}), denied("not in walled_skills"))

    def test_first_failed_condition_of_the_copy(self) -> None:
        only_worktree = self.repo(text=None)
        write(only_worktree / SKILL, PLAIN)
        nested = self.repo(text=None)
        write(nested / "sub" / SKILL, PLAIN)
        commit(nested)
        file_link = self.repo(text=None)
        write(file_link / "docs" / "guide.md", PLAIN)
        (file_link / SKILL).parent.mkdir(parents=True)
        (file_link / SKILL).symlink_to("../../../docs/guide.md")
        commit(file_link)
        rows = [only_worktree, nested, file_link]
        for target in ("../../../outside", "/etc", "../.."):
            top = self.repo(text=None)
            (top / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
            (top / ".claude" / "skills" / "pg-guide").symlink_to(target)
            commit(top)
            rows.append(top)
        chain = self.linked("../../docs/next")
        (chain / ".claude" / "skills" / "other").symlink_to("../../docs/next")
        (chain / ".claude" / "skills" / "pg-guide").unlink()
        (chain / ".claude" / "skills" / "pg-guide").symlink_to("other")
        commit(chain)
        rows.append(chain)
        for top in rows:
            with self.subTest(top=top.name):
                self.assertEqual(verdict(top), denied("no copy at the main ref"))

    def test_a_checkout_that_differs(self) -> None:
        def committed(top: Path) -> None:
            git(top, "checkout", "-q", "-b", "feat/x")
            write(top / SKILL, PLAIN + "x")
            commit(top)

        def uncommitted(top: Path) -> None:
            write(top / SKILL, PLAIN + "x")

        def missing(top: Path) -> None:
            (top / SKILL).unlink()

        def folder(top: Path) -> None:
            (top / SKILL).unlink()
            (top / SKILL).mkdir()

        def fifo(top: Path) -> None:
            (top / SKILL).unlink()
            os.mkfifo(top / SKILL)

        def folder_to_link(top: Path) -> None:
            write(top / "docs" / "copy" / "SKILL.md", PLAIN)
            target = top / ".claude" / "skills" / "pg-guide"
            (target / "SKILL.md").unlink()
            target.rmdir()
            target.symlink_to("../../docs/copy")

        for change in (committed, uncommitted, missing, folder, fifo, folder_to_link):
            with self.subTest(change.__name__):
                top = self.repo()
                change(top)
                self.assertEqual(verdict(top), denied("differs from the main ref"))
        top = self.linked("../../docs/skills/pg-guide")
        write(top / "docs" / "skills" / "other" / "SKILL.md", PLAIN)
        link = top / ".claude" / "skills" / "pg-guide"
        link.unlink()
        link.symlink_to("../../docs/skills/other")
        self.assertEqual(verdict(top), denied("differs from the main ref"), "another target")
        link.unlink()
        write(link / "SKILL.md", PLAIN)
        self.assertEqual(verdict(top), denied("differs from the main ref"), "a plain folder")

    def test_two_checkouts(self) -> None:
        top = self.repo()
        linked = self.tmp / "linked"
        git(top, "worktree", "add", "-q", "-b", "feat/x", str(linked), "main")
        linked = linked.resolve()
        (linked / "src" / "deep").mkdir()
        self.assertEqual(verdict(linked), ALLOW)
        self.assertEqual(verdict(linked / "src" / "deep"), ALLOW)
        write(linked / SKILL, PLAIN + "x")
        self.assertEqual(verdict(linked), denied("differs from the main ref"), "worktree copy")
        write(linked / SKILL, PLAIN)
        write(top / SKILL, PLAIN + "x")
        self.assertEqual(verdict(linked), denied("differs from the main ref"), "main checkout copy")
        (top / SKILL).unlink()
        self.assertEqual(verdict(linked), denied("differs from the main ref"), "no main checkout copy")
        write(top / SKILL, PLAIN)
        start = self.tmp / "start"
        git(top, "worktree", "add", "-q", "-b", "feat/y", str(start), "main")
        env = {"CLAUDE_PROJECT_DIR": str(start.resolve())}
        self.assertEqual(verdict(linked, env=env), ALLOW, "session folder equal")
        write(start / SKILL, PLAIN + "x")
        self.assertEqual(verdict(linked, env=env), denied("differs from the main ref"), "session folder copy")

    def test_cwd_falls_back_to_the_project_dir(self) -> None:
        top = self.repo()
        self.assertEqual(verdict(None, env={"CLAUDE_PROJECT_DIR": str(top)}), ALLOW)
        self.assertEqual(verdict(None), denied("not in walled_skills"))

    def test_scan(self) -> None:
        front = lambda line: f"---\nname: x\n{line}\n---\nBody.\n"  # noqa: E731
        rows: list[tuple[str | bytes, str | None]] = [
            (front("context: fork"), "context: fork"), (front('context: "fork"'), "context: fork"),
            (front("context: fork # note"), "context: fork"), (front('"context": fork'), "context: fork"),
            (front("'context': fork"), "context: fork"), (front("{context: x}"), "context: fork"),
            (front("a: 1, context: x"), "context: fork"), (front("context:\n  fork"), "context: fork"),
            (b"\xef\xbb\xbf---\r\ncontext: fork\r\n---\r\n", "context: fork"),
            (front("hooks:"), "hooks key"), (front('"hooks": x'), "hooks key"), (front("{hooks: x}"), "hooks key"),
            ("Body !`date`\n", "shell line"), ("Body\n```!\ndate\n```\n", "shell line"), ("Body ```! date\n", "shell line"),
            (front("description: !`date`"), "shell line"), (b"\xff\xfe Body !`date`\n", "shell line"),
            ("Body !$ARGUMENTS\n", "shell line"), ("Body !$0\n", "shell line"), ("Body !${1}\n", "shell line"),
            ("Body ! $ARGUMENTS\n", None),
            ("Say hello to $0! Then welcome $1.\n", "shell line"), ("Fix $ARGUMENTS!\n", "shell line"),
            ("Fix $ARGUMENTS[0]! now\n", "shell line"), ("Hi $name!\n", "shell line"), ("Hi ${name}!\n", "shell line"),
            ("Run `$ARGUMENTS` now\n", None), ("Costs $5 ! no\n", "shell line"),
            (b"Say $0`!\n$1\n```\n", "shell line"), (b"Say $0``!\n$1\n```\n", "shell line"),
            (b"Say $ARGUMENTS[0]`!\n$1\n```\n", "shell line"), (b"Say $name`!\n$1\n```\n", "shell line"),
            (b"Check `$0`! Then run $1.\n```bash\nls\n```\n", "shell line"),
            ("---\narguments: [my-arg, cmd]\n---\nRun $my-arg!\n$cmd\n```\n", "shell line"),
            ("Run $é!\n", "shell line"), ("Run $arg.x!\n", "shell line"),
            ("Costs $5 and " + "a" * 259 + "!\n", None),
            ("---\ncontext: fork\n---x\nBody\n", "context: fork"), ("---\ncontext: fork\n--- end\n", "context: fork"),
            ("---\ncontext: fork\na: b---\n", "context: fork"), ("---\ncontext: fork\n----\n", "context: fork"),
            ("---\ncontext: fork\n  ---\n", "context: fork"), ("---\nhooks: x\n---x\n", "hooks key"),
            ("---﻿\ncontext: fork\n---\n", "context: fork"),
            (front("!!str context: fork"), "frontmatter shape"), (front("&a context: fork"), "frontmatter shape"),
            (front("? context\n: fork"), "frontmatter shape"), (front('"\\x63ontext": fork'), "frontmatter shape"),
            (front("<<: {context: fork}"), "context: fork"), (front("[context]: fork"), "frontmatter shape"),
            ("---\n  a: 1\n---\nBody\n", "frontmatter shape"),
            (front("# note\ndescription: a\n  more"), None),
            ("Body see @notes.md\n", "at mention"), (front("description: see @notes.md"), "at mention"),
            ("\n---\ncontext: fork\n---\nBody\n", None),
            ("Body\ncontext: fork\n", None), (front("name2: y") + "hooks: x\n", None), (b"Plain \xff text\n", None)]
        for text, reason in rows:
            with self.subTest(text=text):
                self.assertEqual(verdict(self.repo(text=text)), denied(reason) if reason else ALLOW)

    def test_cli(self) -> None:
        top = self.repo()
        run = lambda *args, stdin="": subprocess.run(  # noqa: E731
            [sys.executable, str(DEVLOOP), "skill-wall", *args], input=stdin, capture_output=True, text=True)
        rows = [("[]", denied("not in walled_skills")), (json.dumps(hook(top)), ALLOW),
                (json.dumps(hook(top, "other")), denied("not in walled_skills"))]
        for stdin, expected in rows:
            with self.subTest(stdin=stdin):
                result = run(stdin=stdin)
                self.assertEqual((result.returncode, json.loads(result.stdout)), (0, expected))
        self.assertEqual(run("extra").returncode, 2)

    def test_names_ok(self) -> None:
        for value in ("pg-guide", [""], ["a:b"], ["A"], [1], ["-a"], ["a" * 65], ["a\n"]):
            with self.subTest(value=value):
                self.assertFalse(skills.names_ok(value))
        for value in ([], ["pg-guide"], ["a" * 64]):
            with self.subTest(value=value):
                self.assertTrue(skills.names_ok(value))

    def test_setup_lines(self) -> None:
        self.assertEqual(skills.setup_lines(self.repo()), [])
        for top in (self.repo(text=None), self.repo(text="Body !`date`\n")):
            lines = skills.setup_lines(top)
            self.assertEqual(len(lines), 1)
            self.assertIn("pg-guide", lines[0])
        on_disk = self.repo(config='{"version": "3.0.0"}\n', text=None)
        put(on_disk / "dev-loop.json", CONFIG)
        self.assertEqual(skills.setup_lines(on_disk), [])
        empty = self.tmp / "empty"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        self.assertEqual(skills.setup_lines(empty), [])
        stale = self.repo()
        write(stale / SKILL, PLAIN + "x")
        self.assertEqual(skills.setup_lines(stale), [])

    def test_skill_roots(self) -> None:
        plain = self.repo()
        self.assertEqual(skills.skill_roots(plain, "main", "pg-guide"), [".claude/skills/pg-guide"])
        top = self.repo(text=None)
        (top / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (top / ".claude" / "skills" / "pg-guide").symlink_to("../../docs/skills/pg-guide")
        commit(top)
        self.assertEqual(skills.skill_roots(top, "HEAD", "pg-guide"), [".claude/skills/pg-guide", "docs/skills/pg-guide"])
        outside = self.repo(text=None)
        (outside / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (outside / ".claude" / "skills" / "pg-guide").symlink_to("../../../outside")
        commit(outside)
        self.assertEqual(skills.skill_roots(outside, "HEAD", "pg-guide"), [".claude/skills/pg-guide"])


if __name__ == "__main__":
    unittest.main()

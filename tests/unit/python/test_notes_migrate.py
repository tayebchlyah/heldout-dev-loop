"""notes_migrate: the links and the move plan of the notes layout migration."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from notes_migrate import links, plan
from support import git_in, make_git_repo, write

NOTES = Path("/v/Notes")
SPEC_OLD = NOTES / "specs/20261020 Demo Work.md"
SPEC_NEW = NOTES / "Features/20261019 Demo Work/Spec.md"
PLAN_OLD = NOTES / "plans/20261021 Demo Work.md"
PLAN_NEW = NOTES / "Features/20261019 Demo Work/Plan.md"
PROJECT = NOTES / "Project Note.md"
MOVED = {SPEC_OLD: SPEC_NEW, PLAN_OLD: PLAN_NEW}


def files_of(root: Path, paths: list[Path]) -> dict[str, Path]:
    keys = {}
    for path in paths:
        rel = unicodedata.normalize("NFC", path.relative_to(root).as_posix()).lower()
        keys[rel[:-3] if rel.endswith(".md") else rel] = path
    return keys


class LinkTests(unittest.TestCase):
    def rewrite(self, text, note_old=PROJECT, note_new=PROJECT, moved=MOVED, vault=None, extra=()):
        paths = [PROJECT, SPEC_OLD, PLAN_OLD, *extra]
        return links.rewrite(text, note_old, note_new, moved, NOTES, vault, files_of(vault or NOTES, paths))

    def test_short_and_alias_and_markdown(self) -> None:
        text = (
            "[[specs/20261020 Demo Work]]\n"
            "[[20261020 Demo Work#Goal|the spec]]\n"
            "[plan](<plans/20261021 Demo Work.md>)\n"
        )
        self.assertEqual(self.rewrite(text), (
            "[[Features/20261019 Demo Work/Spec|specs/20261020 Demo Work]]\n"
            "[[Features/20261019 Demo Work/Spec#Goal|the spec]]\n"
            "[plan](<Features/20261019 Demo Work/Plan.md>)\n",
            3,
        ))

    def test_vault_root_form_kept(self) -> None:
        self.assertEqual(
            self.rewrite("[[Notes/specs/20261020 Demo Work|Demo]]", vault=Path("/v")),
            ("[[Notes/Features/20261019 Demo Work/Spec|Demo]]", 1),
        )

    def test_relative_markdown_from_new_place(self) -> None:
        other_old, other_new = NOTES / "specs/20261001 Other.md", NOTES / "Features/20261001 Other/Spec.md"
        moved = {**MOVED, other_old: other_new}
        text = "[note](<../Project Note.md>) [x](<20261001 Other.md>)"
        self.assertEqual(
            self.rewrite(text, SPEC_OLD, SPEC_NEW, moved, extra=[other_old]),
            ("[note](<../../Project Note.md>) [x](<../20261001 Other/Spec.md>)", 2),
        )

    def test_relative_wikilinks(self) -> None:
        img_old, img_new = NOTES / "specs/img/a.png", NOTES / "Features/20261019 Demo Work/img/a.png"
        moved = {**MOVED, img_old: img_new}
        rows = [
            ("[[../specs/20261020 Demo Work]]", PLAN_OLD, PLAN_NEW, "[[Spec|../specs/20261020 Demo Work]]"),
            ("[[../Project Note]]", SPEC_OLD, SPEC_NEW, "[[../../Project Note]]"),
            ("![[./img/a.png]]", SPEC_OLD, SPEC_NEW, "![[./img/a.png|./img/a.png]]"),
            ("[[../specs/img/a.png]]", PLAN_OLD, PLAN_NEW, "[[./img/a.png|../specs/img/a.png]]"),
            ("[[../plans/20261021 Demo Work]]", PROJECT.parent / "answers/Q.md", NOTES / "Answers/Q.md",
             "[[../Features/20261019 Demo Work/Plan|../plans/20261021 Demo Work]]"),
        ]
        for text, note_old, note_new, expected in rows:
            with self.subTest(text=text):
                self.assertEqual(self.rewrite(text, note_old, note_new, moved, extra=[img_old]), (expected, 1))

    def test_table_row_escape(self) -> None:
        for prefix in ("", "> "):
            new, count = self.rewrite(f"{prefix}| a | [[specs/20261020 Demo Work]] |\n")
            self.assertEqual(new, f"{prefix}| a | [[Features/20261019 Demo Work/Spec\\|specs/20261020 Demo Work]] |\n")
            self.assertEqual((count, new.replace("\\|", "").count("|")), (1, 3))

    def test_code_untouched(self) -> None:
        link = "[[specs/20261020 Demo Work]]"
        text = (
            f"`{link}`\n```\n{link}\n```\n~~~\n[x](plans/demo.md)\n~~~\n"
            f"````\n```\n{link}\n```\n{link}\n````\n"
            f"> ```\n> {link}\n> ```\n"
        )
        self.assertEqual(self.rewrite(text), (text, 0))

    def test_unmoved_target_untouched(self) -> None:
        text = "[[Project Note]] [p](<Project Note.md>) [[../Project Note]]"
        self.assertEqual(self.rewrite(text, moved={}), (text, 0))

    def test_bare_path_gets_angle(self) -> None:
        demo = NOTES / "plans/demo.md"
        new, count = self.rewrite("[x](plans/demo.md)", moved={demo: PLAN_NEW}, extra=[demo])
        self.assertEqual((new, count), ("[x](<Features/20261019 Demo Work/Plan.md>)", 1))

    def test_spec_wins_name_clash(self) -> None:
        plan = NOTES / "plans/20261020 Demo Work.md"
        link = links.find_links("[[20261020 Demo Work]]")[0]
        files = files_of(NOTES, [plan, SPEC_OLD])
        self.assertEqual(links.resolve(link, PROJECT, NOTES, None, files), SPEC_OLD)

    def test_nfd_file_name_matches_nfc_link(self) -> None:
        nfd_old = NOTES / unicodedata.normalize("NFD", "specs/20261020 Café.md")
        nfd_new = NOTES / unicodedata.normalize("NFD", "Features/20261020 Café/Spec.md")
        text = unicodedata.normalize("NFC", "[[20261020 Café]]")
        new, count = self.rewrite(text, moved={nfd_old: nfd_new}, extra=[nfd_old])
        self.assertEqual((new, count), (unicodedata.normalize("NFC", "[[Features/20261020 Café/Spec|20261020 Café]]"), 1))

    def test_crlf_kept(self) -> None:
        text = "a\r\n```\r\n[[specs/20261020 Demo Work]]\r\n```\r\n[[specs/20261020 Demo Work|s]]\r\n"
        self.assertEqual(self.rewrite(text), (
            "a\r\n```\r\n[[specs/20261020 Demo Work]]\r\n```\r\n[[Features/20261019 Demo Work/Spec|s]]\r\n",
            1,
        ))

    def test_dot_md_inside_title(self) -> None:
        old, new = NOTES / "specs/20261020 Read.md Files.md", NOTES / "Features/20261020 Read.md Files/Spec.md"
        text = "[[20261020 Read.md Files]] [[specs/20261020 Read.md Files.md|r]]"
        self.assertEqual(self.rewrite(text, moved={old: new}, extra=[old]), (
            "[[Features/20261020 Read.md Files/Spec|20261020 Read.md Files]] "
            "[[Features/20261020 Read.md Files/Spec.md|r]]",
            2,
        ))


DEMO = {
    "specs/20261020 Demo Work.md": "---\nfeature: demo-work\n---\n# Demo\n",
    "plans/20261021 Demo Work.md": "---\nspec: 20261020 Demo Work.md\n---\n",
    "framing/20261019 demo-work.md": "---\ntype: note\n---\n",
}
DEMO_MOVES = [
    ("specs/20261020 Demo Work.md", "Features/20261019 Demo Work/Spec.md", "spec", "demo-work"),
    ("plans/20261021 Demo Work.md", "Features/20261019 Demo Work/Plan.md", "plan", "demo-work"),
    ("framing/20261019 demo-work.md", "Features/20261019 Demo Work/Framing.md", "framing", "demo-work"),
]


class MovePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tmp = tmp.resolve()
        self.repo = make_git_repo(self.tmp, {"dev-loop.json": "{}\n"})
        self.notes = self.tmp / "notes"
        self.notes.mkdir()

    def put(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            write(self.notes / rel, text)

    def tree(self) -> dict[str, bytes]:
        return {str(p): p.read_bytes() for p in sorted(self.tmp.rglob("*"))
                if p.is_file() and not p.is_symlink() and ".git" not in p.parts}

    def plan(self, kit: str = "2.0.0") -> tuple[list[tuple], list[tuple], list[str]]:
        before = self.tree()
        moves, blocked, ambiguous = plan.plan_moves(self.repo, self.notes, kit)
        self.assertEqual(self.tree(), before)
        rel = lambda p: p.relative_to(self.notes).as_posix()
        return [(rel(m.src), rel(m.dst), m.role, m.slug) for m in moves], [tuple(b) for b in blocked], ambiguous

    def reasons(self, kit: str = "2.0.0") -> list[tuple]:
        return self.plan(kit)[1]

    def test_three_notes_pair(self) -> None:
        self.put(DEMO)
        self.assertEqual(self.plan(), (DEMO_MOVES, [], []))

    def test_two_plans(self) -> None:
        self.put({
            "specs/20261020 Demo Work.md": "---\nfeature: demo-work\n---\n",
            "plans/20261021 First.md": "---\nspec: 20261020 Demo Work.md\n---\n",
            "plans/20261022 Second.md": "---\nspec: 20261020 Demo Work\n---\n",
        })
        self.assertEqual(self.plan()[0], [
            ("specs/20261020 Demo Work.md", "Features/20261020 Demo Work/Spec.md", "spec", "demo-work"),
            ("plans/20261021 First.md", "Features/20261020 Demo Work/20261021 First.md", "plan", "demo-work"),
            ("plans/20261022 Second.md", "Features/20261020 Demo Work/Plan.md", "plan", "demo-work"),
        ])

    def test_plan_pairing_rules(self) -> None:
        rows = [
            ("20261013 Release Tooling Plan.md", "---\ntype: plan\n---\n"),
            ("20261015 Tooling.md", '---\nspec: "[[20261013 Release Tooling]]"\n---\n'),
            ("20261015 Tooling.md", '---\nspec: "[[specs/20261013 Release Tooling|the spec]]"\n---\n'),
            ("20261016 Other.md", "---\nfeature: release-tooling\n---\n"),
        ]
        for name, text in rows:
            with self.subTest(name=name, text=text):
                shutil.rmtree(self.notes)
                self.put({"specs/20261013 Release Tooling.md": "# Release\n", f"plans/{name}": text})
                self.assertIn(
                    (f"plans/{name}", "Features/20261013 Release Tooling/Plan.md", "plan", "release-tooling"),
                    self.plan()[0],
                )

    def test_orphans_get_own_folder(self) -> None:
        self.put({"plans/20261013 Orphan Work.md": "# x\n", "framing/20261014 lone-idea.md": "# y\n"})
        self.assertEqual(self.plan(), ([
            ("plans/20261013 Orphan Work.md", "Features/20261013 Orphan Work/Plan.md", "plan", "orphan-work"),
            ("framing/20261014 lone-idea.md", "Features/20261014 Lone Idea/Framing.md", "framing", "lone-idea"),
        ], [], []))

    def test_ambiguous_framing(self) -> None:
        self.put({
            "specs/20261020 Twin A.md": "---\nfeature: twin\n---\n",
            "specs/20261021 Twin B.md": "---\nfeature: twin\n---\n",
            "framing/20261019 twin.md": "# twin\n",
        })
        moves, blocked, ambiguous = self.plan()
        self.assertIn(("framing/20261019 twin.md", "Features/20261019 Twin/Framing.md", "framing", "twin"), moves)
        self.assertEqual((blocked, ambiguous), ([], ["framing/20261019 twin.md"]))

    def test_linked_file_moves_unlinked_blocks(self) -> None:
        self.put({
            **DEMO,
            "specs/20261020 Demo Work.md": "---\nfeature: demo-work\n---\n[m](mock.html) ![[shared.png]]\n",
            "specs/20261001 Other.md": "![[shared.png]]\n",
            "specs/.DS_Store": "x",
            "specs/mock.html": "<p>",
            "specs/img.png": "png",
            "specs/shared.png": "png",
        })
        moves, blocked, _ = self.plan()
        self.assertIn(("specs/mock.html", "Features/20261019 Demo Work/mock.html", "file", "demo-work"), moves)
        self.assertFalse([m for m in moves if ".DS_Store" in m[0]])
        self.assertEqual(blocked, [("specs/img.png", "unknown-entry"), ("specs/shared.png", "unknown-entry")])

    def test_folder_renames(self) -> None:
        self.put({
            "answers/20261020 Q.md": "a",
            "maps/release-cycle/map.md": "m",
            "security-audit/run-3/REPORT.md": "r",
            "dev-loop-board/features/x.md": "x",
        })
        self.assertEqual(self.plan(), ([
            ("answers", "Answers", "folder", None),
            ("maps", "Maps", "folder", None),
            ("maps/release-cycle", "Maps/Release Cycle", "folder", None),
            ("security-audit", "Security Audit", "folder", None),
            ("security-audit/run-3", "Security Audit/Run 3", "folder", None),
            ("dev-loop-board", "Dev Loop Board", "folder", None),
            ("dev-loop-board/features", "Dev Loop Board/Features", "folder", None),
        ], [], []))

    def test_board_merge_newer_wins(self) -> None:
        status = "---\ntype: dev-loop-status\n---\n"
        self.put({
            "dev-loop-board/features/a.md": status,
            "dev-loop-board/features/d.md": status,
            "dev-loop-board/features/e.md": status,
            "dev-loop-board/c.md": status,
            "Dev Loop Board/Features/b.md": status,
            "Dev Loop Board/Features/d.md": status,
            "Dev Loop Board/Features/e.md": status,
        })
        for rel, when in [("dev-loop-board/features/d.md", 200), ("Dev Loop Board/Features/d.md", 100),
                          ("dev-loop-board/features/e.md", 100), ("Dev Loop Board/Features/e.md", 200)]:
            os.utime(self.notes / rel, (when, when))
        self.assertEqual(self.plan(), ([
            ("dev-loop-board/features/a.md", "Dev Loop Board/Features/a.md", "board-merge", None),
            ("dev-loop-board/features/d.md", "Dev Loop Board/Features/d.md", "board-merge", None),
            ("dev-loop-board/features/e.md", "dev-loop-board/features/e.md", "board-merge", None),
            ("dev-loop-board/c.md", "Dev Loop Board/Features/c.md", "board-merge", None),
        ], [], []))

    def test_board_merge_inside_rename(self) -> None:
        status = "---\ntype: dev-loop-status\n---\n"
        renames = [
            ("dev-loop-board", "Dev Loop Board", "folder", None),
            ("dev-loop-board/features", "Dev Loop Board/Features", "folder", None),
        ]
        rows = [(100, 200, ("dev-loop-board/c.md", "dev-loop-board/c.md")),
                (200, 100, ("dev-loop-board/c.md", "Dev Loop Board/Features/c.md"))]
        for root_time, features_time, pick in rows:
            with self.subTest(root_time=root_time):
                shutil.rmtree(self.notes)
                self.put({"dev-loop-board/c.md": status, "dev-loop-board/features/c.md": status})
                os.utime(self.notes / "dev-loop-board/c.md", (root_time, root_time))
                os.utime(self.notes / "dev-loop-board/features/c.md", (features_time, features_time))
                self.assertEqual(self.plan(), ([*renames, (*pick, "board-merge", None)], [], []))

    def test_target_exists(self) -> None:
        self.put({**DEMO, "Features/20261019 Demo Work/Spec.md": "x",
                  "security-audit/run-3/REPORT.md": "old", "Security Audit/Run 3/REPORT.md": "new"})
        self.assertEqual(self.reasons(), [
            ("Security Audit/Run 3/REPORT.md", "target-exists"),
            ("Features/20261019 Demo Work/Spec.md", "target-exists"),
        ])

    def test_link_blocks(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        write(elsewhere / "20261020 Demo Work.md", "x")
        rows = [
            ("specs", elsewhere, {}),
            ("specs/20261020 Demo Work.md", elsewhere / "20261020 Demo Work.md", {}),
            ("Features", elsewhere, {"specs/20261020 Demo Work.md": "x"}),
        ]
        for rel, target, files in rows:
            with self.subTest(rel=rel):
                shutil.rmtree(self.notes)
                self.notes.mkdir()
                self.put(files)
                (self.notes / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.notes / rel).symlink_to(target)
                self.assertEqual(self.reasons(), [(rel, "link")])

    def test_placeholder_blocks(self) -> None:
        self.put({**DEMO, "specs/.20261019 Gone.md.icloud": "", "Project Note.md": "x", "specs/20261023 Evicted.md": ""})
        (self.notes / "plans/20261022 Latin.md").write_bytes(b"caf\xe9 [[x]]\n")
        real = os.stat
        reads = []

        def stat(path, *args, **kwargs):
            found = real(path, *args, **kwargs)
            if str(path).endswith(("Project Note.md", "Evicted.md")):
                return SimpleNamespace(st_flags=0x40000000, st_mode=found.st_mode, st_mtime=found.st_mtime)
            return found

        def read_text(path, *args, **kwargs):
            reads.append(path.name)
            return Path.read_bytes(path).decode(*args, **kwargs)

        with mock.patch("os.stat", stat), mock.patch.object(Path, "read_text", read_text):
            moves, blocked, _ = self.plan()
        self.assertEqual(sorted(blocked), [("Project Note.md", "placeholder"),
                                           ("specs/.20261019 Gone.md.icloud", "placeholder"),
                                           ("specs/20261023 Evicted.md", "placeholder")])
        self.assertNotIn("20261023 Evicted.md", reads)
        self.assertIn(("plans/20261022 Latin.md", "Features/20261022 Latin/Plan.md", "plan", "latin"), moves)

    def test_old_kit_worktree(self) -> None:
        self.put(DEMO)
        version = lambda v: json.dumps({"version": v})
        rows = [(version("1.9.0"), True), (version("2.0.0-rc.1"), True), (version("2.0.0"), False),
                (version("2.1.0"), False), (None, False), ("[1]", False), ('"1.0.0"', False)]
        for n, (manifest, blocked) in enumerate(rows):
            with self.subTest(manifest=manifest):
                other = self.tmp / f"wt-{n}"
                git_in(self.repo, "worktree", "add", "-q", str(other), "-b", f"feat/{n}")
                if manifest:
                    write(other / ".dev-loop/MANIFEST.json", manifest)
                expected = [(str(other), "old-kit-worktree")] if blocked else []
                self.assertEqual(self.reasons(), expected)
                git_in(self.repo, "worktree", "remove", "--force", str(other))

    def test_open_ledger(self) -> None:
        self.put(DEMO)
        link = self.tmp / "link"
        link.symlink_to(self.notes)
        sdd = self.repo / ".superpowers/sdd"
        rows = [
            (None, True),
            (str(link / "plans/20261021 Demo Work.md"), True),
            ("../notes/plans/20261021 Demo Work.md", True),
            (str(self.notes / "Features/20261019 Demo Work/Plan.md"), False),
        ]
        for marker, blocked in rows:
            with self.subTest(marker=marker):
                shutil.rmtree(sdd, ignore_errors=True)
                write(sdd / "20261020 Demo Work/progress.md", "# progress\n")
                if marker:
                    write(sdd / "20261020 Demo Work/plan-path", marker + "\n")
                expected = [(str(sdd / "20261020 Demo Work"), "open-ledger")] if blocked else []
                self.assertEqual(self.reasons(), expected)

    def test_gitignore(self) -> None:
        vault = self.tmp / "vault"
        self.notes = vault / "sub/notes"
        write(vault / "README.md", "v")
        git_in(vault, "init", "-q")
        self.put({"security-audit/run-1/REPORT.md": "r", "dev-loop-board/x.md": "x"})
        rows = [
            ("sub/.gitignore", "security-audit/\n", [("Security Audit/", "gitignore")]),
            ("sub/.gitignore", "security-audit/\nSecurity Audit/\n", []),
            ("sub/notes/.gitignore", "security-audit/*\n", [("Security Audit/", "gitignore")]),
            ("sub/notes/.gitignore", "security-audit/run-*/\n", [("Security Audit/Run 1/", "gitignore")]),
            ("sub/notes/.gitignore", "security-audit/run-*/\nSecurity Audit/Run */\n", []),
        ]
        for where, lines, expected in rows:
            with self.subTest(where=where, lines=lines):
                for other in ("sub/.gitignore", "sub/notes/.gitignore"):
                    (vault / other).unlink(missing_ok=True)
                write(vault / where, lines)
                self.assertEqual(self.reasons(), expected)

    def test_bad_name_no_date_collision(self) -> None:
        self.put({
            "specs/20261020 A#B.md": "x",
            "plans/20261021 C[1].md": "x",
            "specs/No Date.md": "x",
            "plans/20261013 Orphan.md": "x",
            "plans/20261013 Orphan Plan.md": "x",
            "specs/20261022 Demo Work.md": "x",
            "Features/20261022 demo work/Notes.md": "x",
        })
        self.assertEqual(sorted(self.reasons()), [
            ("Features/20261013 Orphan/Plan.md", "collision"),
            ("Features/20261020 A#B", "bad-name"),
            ("Features/20261021 C[1]", "bad-name"),
            ("Features/20261022 Demo Work", "collision"),
            ("specs/No Date.md", "no-date"),
        ])

    def test_leaf_case_variant_collision(self) -> None:
        self.put({
            "specs/20261022 Demo Work.md": "x",
            "Features/20261022 Demo Work/spec.md": "keep",
            "security-audit/run-1/REPORT.md": "r",
            "Security Audit/Run 1/report.md": "keep",
        })
        self.assertEqual(sorted(self.reasons()), [
            ("Features/20261022 Demo Work/Spec.md", "collision"),
            ("Security Audit/Run 1/REPORT.md", "collision"),
        ])

    def test_full_case_fold_collision(self) -> None:
        self.put({
            "security-audit/run-1/straße.md": "r",
            "Security Audit/Run 1/STRASSE.md": "keep",
            "security-audit/run-2/spec.md": "r",
            "Security Audit/Run 2/ſpec.md": "keep",
            "security-audit/run-3/\u0399\u0308\u0301 notes.md": "r",
            "Security Audit/Run 3/\u0390 notes.md": "keep",
        })
        self.assertEqual(sorted(self.reasons()), [
            ("Security Audit/Run 1/straße.md", "collision"),
            ("Security Audit/Run 2/spec.md", "collision"),
            ("Security Audit/Run 3/\u0399\u0308\u0301 notes.md", "collision"),
        ])

    def test_folder_name_keeps_inner_dot_md(self) -> None:
        self.put({"specs/20261020 Read.md Files.md": "x",
                  "plans/20261021 Read.md Files.md": "---\nspec: 20261020 Read.md Files\n---\n"})
        self.assertEqual(self.plan()[0], [
            ("specs/20261020 Read.md Files.md", "Features/20261020 Read.md Files/Spec.md", "spec", "read-md-files"),
            ("plans/20261021 Read.md Files.md", "Features/20261020 Read.md Files/Plan.md", "plan", "read-md-files"),
        ])

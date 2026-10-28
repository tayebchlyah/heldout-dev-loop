"""notes_migrate: the notes layout migration."""

from __future__ import annotations

import unicodedata
import unittest
from pathlib import Path

from notes_migrate import links

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
        self.assertEqual(
            self.rewrite("[[../specs/20261020 Demo Work]]", PLAN_OLD, PLAN_NEW),
            ("[[Spec|../specs/20261020 Demo Work]]", 1),
        )
        self.assertEqual(self.rewrite("[[../Project Note]]", SPEC_OLD, SPEC_NEW), ("[[../../Project Note]]", 1))

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


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Shared fixtures of the Feature Folders notes layout (spec: Feature Folders), for the held-out tests.

The layout: a spec at `<notes>/Features/<YYYYMMDD> <Title>/Spec.md`, its plan at `Plan.md` beside it with
`spec: Spec.md`, the ledger at `.superpowers/sdd/Plan/progress.md` with a `plan-path` file that names the plan,
and the board notes in `<notes>/Dev Loop Board/Features/`. The Feature Folders `## Supersedes` list moves every
earlier `specs/`, `plans/` and `dev-loop-board` fixture to this layout.

Fixture choices the spec leaves open:
- `plan-path` holds the real absolute path of the plan, with no newline (superpowers writes the physical path).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import MACHINE, note_text, put, read_note, status_note

FEATURES = "Features"
BOARD = "Dev Loop Board"
BOARD_NOTE = "Dev Loop Board.md"


def spec_file(notes: Path, folder: str) -> Path:
    return notes / FEATURES / folder / "Spec.md"


def plan_file(notes: Path, folder: str) -> Path:
    return notes / FEATURES / folder / "Plan.md"


def write_ledger(wt: Path, plan: Path, text: str) -> Path:
    """The SDD workspace of `plan` in worktree `wt`: `progress.md` and the `plan-path` marker."""
    folder = wt / ".superpowers" / "sdd" / plan.stem
    put(folder / "progress.md", text)
    marker = folder / "plan-path"
    if not marker.exists():
        put(marker, os.path.realpath(plan))
    return folder / "progress.md"


class NewLayout:
    """Mixin for `board_fixtures.Board` and `RobustBoard`: the same sandbox on the Feature Folders layout."""

    def setUp(self) -> None:
        super().setUp()
        for name in ("specs", "plans"):
            folder = self.notes / name
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        (self.notes / FEATURES).mkdir(parents=True, exist_ok=True)

    def spec_path(self, title: str) -> Path:
        return spec_file(self.notes, f"20261001 {title}")

    def spec(self, slug: str, title: str, review: str = "approved", track: str = "M",
             assumptions: tuple[str, ...] = ("Widgets are blue.",), shipped: str = "") -> Path:
        path = self.spec_path(title)
        put(path, f"---\ntype: spec\nreview_status: {review}\nfeature: {slug}\ntrack: {track}\nshipped: {shipped}\n"
                  f"---\n\n# {title}\n\n## Goal\n\n{title} for the widget shop.\n\n## Assumptions\n\n"
                  + "".join(f"- {a}\n" for a in assumptions))
        return path

    def plan(self, spec: Path, title: str, tasks: int) -> Path:
        path = spec.parent / "Plan.md"
        put(path, "---\ntype: plan\nspec: Spec.md\n---\n\n# Plan\n\n"
                  + "".join(f"### Task {n}: Step {n}\n\n" for n in range(1, tasks + 1)))
        return path

    def ledger(self, wt: Path, plan: Path, *lines: str) -> None:
        write_ledger(wt, plan, "# Ledger\n" + "".join(f"{line}\n" for line in lines))

    @property
    def board_dir(self) -> Path:
        return self.notes / BOARD

    @property
    def features_dir(self) -> Path:
        return self.board_dir / "Features"

    def own_notes(self, machine_id: str = MACHINE["id"]) -> dict[str, Path]:
        """Status notes of machine_id in `Dev Loop Board/Features/` and `Dev Loop Board/`."""
        found: dict[str, Path] = {}
        if not self.board_dir.is_dir():
            return found
        paths = [*sorted(self.features_dir.glob("*.md")), *sorted(self.board_dir.glob("*.md"))]
        for path in paths:
            if path.name == BOARD_NOTE or path.name.startswith(".") or path.is_symlink():
                continue
            fields = read_note(path)
            if fields.get("machine_id") == machine_id:
                self.assertNotIn(fields["feature"], found, f"two own notes for {fields['feature']}")
                found[fields["feature"]] = path
        return found

    def old_note(self, slug: str, **over) -> dict:
        """An own note from an earlier kit, where `notes-migrate` puts it: `Dev Loop Board/Features/<own name>`."""
        fields = status_note(feature=slug, machine_id=MACHINE["id"], owner=MACHINE["owner"], machine=MACHINE["machine"],
                             **over)
        put(self.features_dir / self.own_name(slug), note_text(fields))
        return fields

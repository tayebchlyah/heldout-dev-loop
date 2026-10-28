#!/usr/bin/env python3
"""Held-out flow tests for the board ledger layout (spec: Board Ledger), flows 1, 2 and 4. Flow 3 is in
board_ledger.test.mjs.

Seams: `devloop.py board` as a process on a temporary repo and notes folder (the Progress Board sandbox), and
the `dataviewjs` block of the `Dev Loop Board.md` it writes, run under Node with the stubs the spec names: a
`dv` with `pages().where(fn)` and `el("div", "")`, a `require` limited to `os.homedir()` and
`fs.readFileSync`, and a `localStorage`. A Dataview redraw is one more run of the block on a new element.
Every name in here is invented.

Markers this file relies on (the spec's words): each project fold is a `<details>` with
`data-key="project:<project>"`, each row a `<details>` with `data-key="<project>/<feature>"`, the shipped fold
a `<details>` with `data-key="shipped:<project>"`; an open fold carries the `open` attribute.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import (BOARD_NOTE, MACHINE, OTHER, PROJECT, Board, note_name, note_text, put, read_note, slow,
                            status_note)
from feature_folders_support import NewLayout

# Runs the block once per `pages` step, on a new element each time (a Dataview redraw); a `toggle` step
# calls the toggle listeners of the last element. Prints the HTML of each run and the final `dlb-open`.
HARNESS = r"""
const fs = require('fs')
const vm = require('vm')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const store = new Map()
if (input.storage !== null) store.set('dlb-open', input.storage)
const localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)) },
}
function stubRequire(name) {
  if (name === 'os') return { homedir: () => '/h' }
  if (name === 'fs') return {
    readFileSync: (path) => {
      if (path === '/h/.config/dev-loop/machine.json' && input.machine !== null) return input.machine
      const error = new Error('ENOENT: ' + path)
      error.code = 'ENOENT'
      throw error
    },
  }
  throw new Error('module not in the stub: ' + name)
}
async function main() {
  const html = []
  let last = null
  for (const step of input.steps) {
    if (step.toggle) {
      for (const l of last.listeners.filter((l) => l.type === 'toggle')) {
        l.fn({ type: 'toggle', target: { tagName: 'DETAILS', nodeName: 'DETAILS', dataset: { key: step.toggle.key }, open: step.toggle.open } })
      }
      continue
    }
    const pages = step.pages.map((fm) => ({ ...fm, file: { frontmatter: fm } }))
    const el = { innerHTML: '', listeners: [], addEventListener(type, fn, capture) { this.listeners.push({ type, fn, capture }) } }
    const dv = { pages: () => ({ where: (fn) => pages.filter((p) => fn(p)) }), el: () => el }
    const ctx = vm.createContext({ dv, require: stubRequire, localStorage, console })
    await vm.runInContext('(async () => {\n' + input.code + '\n})()', ctx)
    html.push(String(el.innerHTML))
    last = el
  }
  process.stdout.write(JSON.stringify({ html, storage: store.has('dlb-open') ? store.get('dlb-open') : null }))
}
main().catch((e) => { console.error(e && e.stack || e); process.exit(1) })
"""

VIEWER = json.dumps({"id": MACHINE["id"], "owner": MACHINE["owner"]})


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tag(html: str, key: str) -> str:
    match = re.search(rf'<details\b[^>]*\bdata-key="{re.escape(key)}"[^>]*>', html)
    assert match, f'no <details data-key="{key}">'
    return match.group(0)


def is_open(html: str, key: str) -> bool:
    return re.search(r'\sopen(\s|=|>|/)', re.sub(r'"[^"]*"', '""', tag(html, key))) is not None


def part(html: str, key: str) -> str:
    """From the `<details>` of key to the next `data-key` attribute, or the end."""
    start = html.index(tag(html, key))
    nxt = html.find('data-key="', start + len(tag(html, key)))
    return html[start:] if nxt < 0 else html[start:nxt]


def summary(html: str, key: str) -> str:
    start = html.index(tag(html, key))
    return html[start:html.index("</summary>", start)]


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html))


class BoardLedgerFlows(NewLayout, Board):
    def block(self) -> str:
        note = (self.board_dir / BOARD_NOTE).read_text()
        match = re.search(r"```dataviewjs\n(.*?)\n```", note, re.DOTALL)
        self.assertIsNotNone(match, "no dataviewjs block in Dev Loop Board.md")
        assert match
        return match.group(1)

    def pages(self) -> list[dict]:
        paths = [*self.features_dir.glob("*.md"), *self.board_dir.glob("*.md")]
        return [read_note(p) for p in sorted(paths) if p.name != BOARD_NOTE]

    def obsidian(self, steps: list[dict], storage: list[str] | None = None, machine: str | None = VIEWER) -> dict:
        harness = self.root / "dv-harness.cjs"
        harness.write_text(HARNESS)
        payload = {"code": self.block(), "steps": steps, "machine": machine,
                   "storage": None if storage is None else json.dumps(storage)}
        result = subprocess.run(["node", str(harness)], input=json.dumps(payload), capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @slow
    def test_f1_desktop_obsidian_viewer_machine_first_in_a_closed_project(self) -> None:
        """F1, D1, D2, D6, D7, D8, D10: machine A writes its Stage 5 note, machine B has one in the same
        project. The block shows the project closed with its summary line; opened, the feature of machine A
        comes first with "this machine", and its open row shows the stage chips, the task list and times."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        time.sleep(1.1)
        now = time.time()
        put(self.features_dir / note_name(OTHER["id"], PROJECT, "bench-thing"),
            note_text(status_note(feature="bench-thing", title="Bench Thing", stage=5,
                                  stage_times={"1": iso(now - 7200), "5": iso(now - 3600)}, updated=iso(now))))
        project, row_a, row_b = f"project:{PROJECT}", f"{PROJECT}/gadget", f"{PROJECT}/bench-thing"

        closed = self.obsidian([{"pages": self.pages()}], storage=[])["html"][0]
        self.assertFalse(is_open(closed, project), "the project fold is open by default")
        line = text(summary(closed, project))
        self.assertIn(PROJECT, line)
        self.assertIn("2 in progress", line)
        self.assertIn("5. Build", line)

        html = self.obsidian([{"pages": self.pages()}], storage=[project, row_a])["html"][0]
        self.assertTrue(is_open(html, project))
        self.assertLess(html.index(tag(html, row_a)), html.index(tag(html, row_b)), "machine A is not first")
        self.assertIn("this machine", text(summary(html, row_a)))
        self.assertNotIn("this machine", text(summary(html, row_b)))
        self.assertIn("bench-pc", text(summary(html, row_b)))
        self.assertIn("Track M", text(summary(html, row_a)))
        self.assertTrue(is_open(html, row_a))
        panel = text(part(html, row_a))
        for words in ("Time per stage", "so far", "Step 1", "Step 2", "Step 3", "running"):
            self.assertIn(words, panel)

    @slow
    def test_f2_ship_keeps_five_shipped_notes_and_the_fold_shows_them(self) -> None:
        """F2, D4, D5, D6, D8: 5 own shipped notes, then a sixth feature ships. The next run sets its
        shipped_at, keeps 5 own shipped notes and deletes the oldest; the "Recently shipped" fold shows the 5
        latest, and the new shipped row opens on its tasks, with no running time."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete", "Task 2: complete")
        self.ok(cwd=wt)
        old = {f"old-{n}": f"2026-10-0{n}T10:00:00Z" for n in range(1, 6)}
        for slug, at in old.items():
            put(self.features_dir / note_name(MACHINE["id"], PROJECT, slug),
                note_text(status_note(feature=slug, title=f"Old {slug[-1]}", machine_id=MACHINE["id"],
                                      owner=MACHINE["owner"], machine=MACHINE["machine"], stage=7,
                                      stage_times={"1": at[:11] + "08:00:00Z", "7": at[:11] + "09:00:00Z"},
                                      shipped_at=at, updated=at)))
        self.set_field(self.spec_path("Gadget Export"), "shipped", "2026-10-15")
        self.ok(cwd=wt)

        own = self.own_notes()
        self.assertEqual(set(own), {"old-2", "old-3", "old-4", "old-5", "gadget"})
        shipped_at = read_note(own["gadget"])["shipped_at"]
        self.assertTrue(shipped_at)

        row = f"{PROJECT}/gadget"
        html = self.obsidian([{"pages": self.pages()}],
                             storage=[f"project:{PROJECT}", f"shipped:{PROJECT}", row])["html"][0]
        fold = html.index(tag(html, f"shipped:{PROJECT}"))
        self.assertIn("Recently shipped", text(summary(html, f"shipped:{PROJECT}")))
        self.assertNotIn("Old 1", html)
        for title in ("Old 2", "Old 3", "Old 4", "Old 5", "Gadget Export"):
            self.assertGreater(html.find(title), fold, f"{title} is not in the shipped fold")
        head = text(summary(html, row))
        self.assertIn("Shipped", head)
        self.assertIn(shipped_at[:10], head)
        self.assertTrue(is_open(html, row))
        panel = text(part(html, row))
        for title in ("Step 1", "Step 2", "Step 3"):
            self.assertIn(title, panel)
        self.assertNotIn("running", panel)

    @slow
    def test_f4_open_project_stays_open_after_a_redraw(self) -> None:
        """F4, D11: the user opens the project fold in the note; a status note changes and Dataview redraws
        the block; the project is still open and shows the new note."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ok(cwd=wt)
        project = f"project:{PROJECT}"
        first = self.obsidian([{"pages": self.pages()}, {"toggle": {"key": project, "open": True}}])
        self.assertFalse(is_open(first["html"][0], project))
        self.assertIn(project, json.loads(first["storage"]))

        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        again = self.obsidian([{"pages": self.pages()}], storage=json.loads(first["storage"]))["html"][0]
        self.assertTrue(is_open(again, project), "the redraw closed the project")
        self.assertIn("1 of 3 tasks", text(summary(again, f"{PROJECT}/gadget")))


if __name__ == "__main__":
    unittest.main()

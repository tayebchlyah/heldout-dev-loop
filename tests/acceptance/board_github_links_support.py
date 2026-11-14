#!/usr/bin/env python3
"""Shared fixtures of the held-out tests for the board GitHub links (spec: 20261114 Board GitHub Links).

No test lives here. It holds: the board sandbox of `board_fixtures.Board` on the Feature Folders layout with a
spec `issue:` writer, a `pr.json` writer and the network shut for git; a stub `gh`; a Node runner of
`renderBoard` and of the `dataviewjs` block of `Dev Loop Board.md`; the link and row readers.

Fixture choices the spec leaves open:
- Every board and `ci wait` run gets `GIT_ALLOW_PROTOCOL=file`, so an origin on github.com is never reached.
- A link is an `<a>` element; its attributes are read from the tag, its visible text is the text of its content
  with the tags removed (the inline SVG icon has no text).
- The icon colour of oracle 16 is read in the markup of the link element, from `<a` to `</a>`.
- A row is the `<details data-key="<project>/<feature>">` of the Board Ledger spec, its summary runs to the next
  `</summary>`; the links of a row are the links in that summary.
"""

from __future__ import annotations

import html as htmllib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import BOARD_NOTE, MACHINE, PROJECT, Board, git, put, read_note
from feature_folders_support import NewLayout

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
RENDER = REPO_ROOT / "skill" / "scripts" / "board" / "render.js"
NO_NETWORK = {"GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "false"}

RENDER_HARNESS = r"""
const fs = require('fs')
const vm = require('vm')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const ctx = { console }
vm.createContext(ctx)
vm.runInContext(fs.readFileSync(input.render, 'utf8'), ctx, { filename: input.render })
if (typeof ctx.renderBoard !== 'function') { console.error('render.js defines no global renderBoard'); process.exit(1) }
const AllOpen = vm.runInContext('(class extends Set { has() { return true } })', ctx)
process.stdout.write(String(ctx.renderBoard(input.notes, Date.now(), { open: new AllOpen(), here: input.here, me: input.me })))
"""

# The stubs of Board Ledger oracle 23: `dv.pages().where(fn)`, `dv.el("div", "")`, a `require` limited to
# `os.homedir()` and `fs.readFileSync`, and a `localStorage` that holds `dlb-open`.
DATAVIEW_HARNESS = r"""
const fs = require('fs')
const vm = require('vm')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const store = new Map([['dlb-open', JSON.stringify(input.open)]])
const localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => { store.set(k, String(v)) } }
function stubRequire(name) {
  if (name === 'os') return { homedir: () => '/h' }
  if (name === 'fs') return {
    readFileSync: (path) => {
      if (path === '/h/.config/dev-loop/machine.json') return input.machine
      const error = new Error('ENOENT: ' + path)
      error.code = 'ENOENT'
      throw error
    },
  }
  throw new Error('module not in the stub: ' + name)
}
async function main() {
  const pages = input.pages.map((fm) => ({ ...fm, file: { frontmatter: fm } }))
  const el = { innerHTML: '', listeners: [], addEventListener(type, fn, capture) { this.listeners.push({ type, fn, capture }) } }
  const dv = { pages: () => ({ where: (fn) => pages.filter((p) => fn(p)) }), el: () => el }
  const ctx = vm.createContext({ dv, require: stubRequire, localStorage, console })
  await vm.runInContext('(async () => {\n' + input.code + '\n})()', ctx)
  process.stdout.write(String(el.innerHTML))
}
main().catch((e) => { console.error(e && e.stack || e); process.exit(1) })
"""


def node(script_dir: Path, name: str, source: str, payload: dict) -> str:
    script = script_dir / name
    script.write_text(source)
    result = subprocess.run(["node", str(script)], input=json.dumps(payload), capture_output=True, text=True,
                            timeout=60)
    if result.returncode != 0:
        raise AssertionError(f"node {name} failed: {result.stderr[-2000:]}")
    return result.stdout


def render(script_dir: Path, notes: list[dict]) -> str:
    """The HTML of `renderBoard(notes, now, opts)` with every fold open."""
    return node(script_dir, "render-harness.cjs", RENDER_HARNESS,
                {"render": str(RENDER), "notes": notes, "here": MACHINE["id"], "me": MACHINE["owner"]})


def row_summary(html: str, project: str, feature: str) -> str:
    key = htmllib.escape(f"{project}/{feature}", quote=True)
    match = re.search(rf'<details\b[^>]*\bdata-key="{re.escape(key)}"[^>]*>', html)
    if match is None:
        raise AssertionError(f'no <details data-key="{project}/{feature}"> in the board')
    end = html.find("</summary>", match.start())
    return html[match.start():] if end < 0 else html[match.start():end]


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]*>", " ", fragment))).strip()


def links(fragment: str) -> list[dict]:
    """Each `<a>` of fragment: its attributes (`attrs`), visible text (`text`) and whole markup (`markup`)."""
    found = []
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a\s*>", fragment, re.DOTALL | re.IGNORECASE):
        attrs = {}
        for name, double, single in re.findall(r"""([A-Za-z_:][-\w:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""", match.group(1)):
            attrs[name.lower()] = htmllib.unescape(double or single)
        found.append({"attrs": attrs, "text": text(match.group(2)), "markup": match.group(0)})
    return found


def github_links(fragment: str) -> list[dict]:
    return [link for link in links(fragment) if link["attrs"].get("href", "").startswith("https://github.com")]


def names(fragment: str) -> list[str]:
    return [link["attrs"].get("aria-label", "") for link in github_links(fragment)]


class LinkBoard(NewLayout, Board):
    """The board sandbox, git network shut, with the spec `issue:` key, `pr.json` and origin helpers."""

    def setUp(self) -> None:
        super().setUp()
        self.env.update(NO_NETWORK)

    def issue_spec(self, slug: str, title: str, issue: str | None) -> Path:
        path = self.spec(slug, title)
        if issue is not None:
            path.write_text(re.sub(r"^(review_status:.*)$", rf"\1\nissue: {issue}", path.read_text(), count=1,
                                   flags=re.MULTILINE))
        return path

    def git_dir(self, wt: Path) -> Path:
        return Path(git(wt, "rev-parse", "--absolute-git-dir").strip())

    def pr_file(self, wt: Path) -> Path:
        return self.git_dir(wt) / "dev-loop" / "pr.json"

    def write_pr(self, wt: Path, content: dict | str) -> Path:
        path = self.pr_file(wt)
        put(path, content if isinstance(content, str) else json.dumps(content) + "\n")
        return path

    def set_origin(self, url: str | None) -> None:
        if url is None:
            git(self.repo, "remote", "remove", "origin")
        else:
            git(self.repo, "remote", "set-url", "origin", url)

    def written(self, out: dict, slug: str) -> dict:
        """The note of slug among the notes the run lists."""
        found = [read_note(Path(p)) for p in out.get("notes", []) if Path(p).is_file()]
        mine = [n for n in found if n.get("feature") == slug and n.get("machine_id") == MACHINE["id"]]
        self.assertEqual(len(mine), 1, f"notes of {slug} in the run: {[n.get('feature') for n in found]}")
        return mine[0]

    def dataview(self, pages: list[dict], open_keys: list[str]) -> str:
        note = (self.board_dir / BOARD_NOTE).read_text()
        match = re.search(r"```dataviewjs\n(.*?)\n```", note, re.DOTALL)
        self.assertIsNotNone(match, "no dataviewjs block in Dev Loop Board.md")
        assert match
        machine = json.dumps({"id": MACHINE["id"], "owner": MACHINE["owner"]})
        return node(self.root, "dv-harness.cjs", DATAVIEW_HARNESS,
                    {"code": match.group(1), "pages": pages, "machine": machine, "open": open_keys})


STUB_GH = r'''
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGV = sys.argv[1:]


def kind(argv):
    if argv[:2] == ["pr", "view"]:
        return "pr view"
    if argv[:2] == ["api", "graphql"]:
        return "api graphql"
    if argv[:2] == ["run", "view"]:
        return "run view"
    if len(argv) > 1 and argv[0] == "api" and argv[1].startswith("repos/"):
        return "compare" if "/compare/" in argv[1] else "repo"
    return "other"


log = os.path.join(HERE, "calls.jsonl")
earlier = []
if os.path.exists(log):
    with open(log) as handle:
        earlier = [json.loads(line) for line in handle if line.strip()]
with open(log, "a") as handle:
    handle.write(json.dumps(ARGV) + "\n")
key = kind(ARGV)
with open(os.path.join(HERE, "answers.json")) as handle:
    answers = json.load(handle).get(key) or [{"exit": 1, "stderr": "stub gh: no answer for " + " ".join(ARGV)}]
answer = answers[min(sum(1 for argv in earlier if kind(argv) == key), len(answers) - 1)]
sys.stdout.write(answer.get("stdout", ""))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("exit", 0))
'''


class StubGh:
    """A `gh` outside the repo: logs its argv, answers by kind (`pr view`, `api graphql`, `compare`,
    `run view`), the n-th call of a kind the n-th answer (the last one repeats); anything else exits 1."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True)
        script = folder / "gh"
        script.write_text(f"#!{sys.executable}\n{STUB_GH}")
        script.chmod(0o755)
        self.set()

    def set(self, **answers: list[dict]) -> None:
        (self.folder / "answers.json").write_text(json.dumps({k.replace("_", " "): v for k, v in answers.items()}))
        (self.folder / "calls.jsonl").unlink(missing_ok=True)

    def path(self, env: dict[str, str]) -> str:
        return f"{self.folder}{os.pathsep}{env.get('PATH', '')}"


def ok(value: object) -> dict:
    return {"exit": 0, "stdout": value if isinstance(value, str) else json.dumps(value) + "\n"}


def fails(stderr: str) -> dict:
    return {"exit": 1, "stdout": "", "stderr": stderr + "\n"}


def check(name: str, status: str = "COMPLETED", conclusion: str = "SUCCESS") -> dict:
    return {"__typename": "CheckRun", "name": name, "status": status,
            "conclusion": conclusion if status == "COMPLETED" else "",
            "detailsUrl": f"https://github.com/o/r/actions/runs/99/job/{len(name)}"}


def view(head: str, number: int, url: str, branch: str, **changes: object) -> dict:
    """A `gh pr view --json` answer of a green PR, with `headRefName`, and the changes given."""
    answer = {"number": number, "url": url, "headRefName": branch, "headRefOid": head, "baseRefName": "main",
              "isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "",
              "latestReviews": [], "statusCheckRollup": [check("test"), check("acceptance")]}
    answer.update(changes)
    return answer


def threads_doc(*nodes: dict) -> dict:
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(nodes)}}}}}


def open_thread() -> dict:
    comment = {"author": {"login": "casey"}, "body": "Rename this.", "url": "https://github.com/o/r/pull/318#c1",
               "authorAssociation": "MEMBER"}
    return {"isResolved": False, "isOutdated": False, "path": "src/ship.py", "line": 3,
            "comments": {"nodes": [comment]}}


def ci_wait(cwd: Path, env: dict[str, str], stub: StubGh, *args: str, timeout: str = "1",
            poll: str = "0") -> tuple[int, dict]:
    run_env = {**env, **NO_NETWORK, "DEVLOOP_TEST_CI_POLL": poll, "PATH": stub.path(env)}
    result = subprocess.run([sys.executable, str(DEVLOOP), "ci", "wait", "--timeout", timeout, *args], cwd=cwd,
                            env=run_env, capture_output=True, text=True, timeout=180)
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"unparsed_stdout": result.stdout[-2000:]}
    if not isinstance(payload, dict):
        payload = {"unparsed_stdout": result.stdout[-2000:]}
    payload.setdefault("_stderr", result.stderr[-2000:])
    return result.returncode, payload


__all__ = ["BOARD_NOTE", "MACHINE", "PROJECT", "LinkBoard", "StubGh", "ci_wait", "check", "fails", "git",
           "github_links", "links", "names", "ok", "open_thread", "put", "read_note", "render", "row_summary",
           "text", "threads_doc", "view"]

# Verify skill and feature map

A project with a UI holds its own verify skill, `.agents/skills/<skill>/`: how any agent launches, checks, drives and cleans up the real app. Its feature map, `features/`, tells the agent how a user reaches each feature. Stage 0 writes both. Stage 6 drives the routes the branch changed.

## When

- `ui` in `capabilities` of `dev-loop.json`: required. `check_setup.py` fails until `dev-loop.json` has a `verify` key.
- No `ui`: none, or opt in with the `verify` key. `"verify": false` opts out.

`verify` in `dev-loop.json`:

- `skill`: the skill folder name, lower case words joined by `-`.
- `base_url`: `http://` or `https://`. A route `path` is joined to it.
- `setup` (optional): shell commands `drive` runs once, in order, in the repo root, before the browser opens. A CLI login goes here.
- `storage_state` (optional): repo-relative path to the Playwright storage state file `setup` writes. `drive` loads it into the browser context.
- `playwright` (optional): the folder, from the repo root (`.` the root), whose `node_modules` holds `playwright` or `@playwright/test`. `verify-init` writes it; `drive` then looks only there.

## Stage 0: write the skill

1. `python3 .agents/skills/dev-loop/scripts/devloop.py verify-init --base-url <url>` (`--name <skill>`, default `verify-<repo folder>`). It writes `SKILL.md`, `features/README.md`, the `.claude/skills/<skill>` link when `harnesses` has `claude`, and the `verify` key. Each part to fill holds `<!-- dev-loop: fill -->`. `check_setup.py` fails while a marker is left or the map has no entry.
2. Interview the repo, then fill the sections, then seed the map, then prove it once. Steps below.

Project CLI first. The project has its own driver CLI (a `dev` command, a Makefile): Launch, Doctor and `verify.setup` call it. A missing drive or login command goes into that CLI, not into a one-off script.

### 1. Interview the repo, not the user

Answer these from the codebase and only ask the user what you cannot observe:

- **Surface:** what does a user actually touch? A web UI, a CLI/TUI, a desktop app, an API, a mobile app, a library? A repo can have several; pick the primary one and note the rest.
- **Run:** how does the app start locally? Prefer the repo's own documented dev command (package scripts, Makefile, README quickstart). Note ports, env vars, seed data, auth.
- **Drive:** how can an agent interact with it programmatically? Existing harnesses first: Playwright/Cypress specs, expect scripts, PTY helpers, curl-able endpoints, a debug port. Only then pick a generic recipe: browser/CDP for web and Electron, a tmux/PTY harness for CLI/TUI, plain HTTP for services.
- **Observe:** what evidence can be captured? Screenshots, terminal transcripts, response bodies, logs, exit codes, DB state.
- **Isolate:** can two instances run side by side (ports, data dirs, profiles)? If not, say so in the generated skill: refusing to double-drive a shared instance beats corrupting the user's session.

If the checkout doesn't build or start as-is, fix that first (or report it precisely) before generating; a skill written against a broken base teaches wrong steps. When an irrelevant missing asset blocks startup (a static dir the API never serves, a sample config), the generated skill may create it, clearly marked as verification scaffolding, and remove it in cleanup.

### 2. Fill the skill

`SKILL.md` keeps its frontmatter (`name: <skill>` and a `description` that names the app, the surface, and when to reach for it) and these sections, each grounded in what the interview actually found (no placeholders left):

- **Launch:** the exact command that starts the app for verification, and how to tell it's ready (a log line, a prompt). Launch must reach a usable state from an empty data dir, including any migrations and seed data. Ready means usable, not just that a process answers. A database health check can pass while its init scripts are still running. Include teardown.
- **Doctor:** one read-only check that answers "is this instance worth driving?": process up, right version/build, port owned by us, auth valid. An agent runs this first whenever anything looks off.
- **Drive:** the harness recipe with real selectors/commands from this repo, not examples. Prefer stable handles (ARIA labels, data attributes, prompt strings, route paths) over coordinates and tab order. Name `devloop.py drive` for the mapped routes.
- **Evidence:** what to capture for a proof and where it goes. State the proof standards: exercise the real user path, not internal setters or test-only endpoints; capture the action and the resulting state, not just the final screen; verify side effects (files written, rows inserted, messages sent) alongside what's visible; mocks only where a production boundary already isolates the external system. `drive` writes to `<git-dir>/dev-loop/evidence/<run>/`.
- **Cleanup:** how to tear down instances the run created. Never kill by process name; kill what you started. Cleanup removes instances and scratch state, never the evidence: proof artifacts survive the teardown, in a location the skill names.

Any helper script the skill ships is executable and its invocation is shown in the skill body.

### 3. Seed the feature map

One file per user-facing feature you can identify, the top 3 to 5 to start, from routes, commands, menus, or docs. `features/README.md` is the index, not an entry. Each file answers, from the user's point of view: what the feature is, how to reach it, how to drive it with the harness, and what observable end state proves it works. If the seed data lacks a precondition that a journey needs, the journey reports a failure and the reason goes in that feature's `Gotchas`. Never weaken the journey to make it pass.

Entry frontmatter is for `drive`, one line of JSON per key:

- `routes`: non-empty list of `{"path", "names"}`. `path` starts with `/`. `names`: list of `{"role", "name"}`, an ARIA role and an exact accessible name, each visible within 10 s. Can be empty.
- `paths`: non-empty list of patterns (`fnmatchcase` on the repo-relative path, `*` matches `/`, no trailing `/`). A branch that changes a matching path, or the entry file itself, selects the entry.
- `script` (optional): repo-relative Node script that replaces the kit runner for this entry. Same input on stdin, same output on stdout.

Body: an H1, one paragraph, then `## Sub-features`, `## How to get to it (user POV)`, `## Driving it with <harness>`, `## Gotchas`.

```markdown
---
routes: [{"path": "/orders", "names": [{"role": "heading", "name": "Orders"}, {"role": "button", "name": "New order"}]}]
paths: ["frontend/src/orders/*", "src/app/api/orders.py"]
---

# Orders

The list of orders, with their state.

## Sub-features
- New order. Cancel an order.

## How to get to it (user POV)
Log in, then the Orders item in the side menu.

## Driving it with Playwright
`devloop.py drive` covers the list. New order: the button `New order`, then the dialog fields by label.

## Gotchas
The list is empty on a fresh stack: `verify.setup` seeds one order.
```

### 4. Prove it once

Run the skill's own instructions end to end: Launch, Doctor, `devloop.py drive --all` with `ok` true, Cleanup. After Cleanup, confirm the evidence folder still exists: a cleanup that eats the proof fails this step. Fix what fails, and run Cleanup after every failed attempt too, so broken attempts don't strand processes and ports. A skill never executed is a draft. If you proved it in a stand-in for the real target (a container in place of a fresh VM), list every setup step the stand-in did that the target will not.

## Stage 6: map pass, drive, layout check

Runs after the fix wave, before the acceptance loop. `verify` absent or `false`: skipped, report line `drive: skipped: no-verify`.

1. Map pass. Add or update the entry of each route the spec declares (Track S: each route the diff changes). Commit it on the feature branch.
2. The stack is up (Stage 6 start). Run the skill's Launch and Doctor.
3. `python3 .agents/skills/dev-loop/scripts/devloop.py drive`. It selects each entry whose `paths` match `git diff --name-only <base>...HEAD` (or whose file changed), checks the readiness of `base_url` for 30 s, runs `setup`, then per route: HTTP status below 400, every name visible, no console error or uncaught page error, one full-page JPEG. JSON `ok`, `selected`, `routes`, `evidence` (`drive.json` inside). Exit 1 with `error`: an unreachable `base_url`, a failed `setup` command, no Playwright in the repo (`verify.playwright` set: only that folder; else `node_modules/playwright`, `node_modules/@playwright/test`, the same under `frontend/`), a bad config or entry.
4. Layout check. One fresh subagent (the route names it) gets the `drive.json` path and this prompt: "Read <path>. For each route with `ok` true, open its `screenshot` and judge the layout only: overlapping or clipped elements, broken or empty regions, unreadable text, a layout that breaks at 1280 by 800. Return a table: entry, path, pass or fail, one-line reason. No other output."
5. Each failure, drive or layout: product (a fix round through `implementer`), map (fix the entry), or environment (fix it once at its root). Rerun with `drive --since <sha of the fix round start>`: only what the fix touched. A rerun that selects nothing (the fix touched no mapped path, for example seed data) is not a pass: drive the failed entry again (`drive --all`, or rerun the full `drive`), and the report line stays `drive: fail` until that entry passes. The layout subagent then reads only the new screenshots. The Stage 6 progress guard and the stale image rule apply to each rerun.
6. After the last drive: Cleanup. Report line `drive: pass` or `drive: fail`.
7. Closing drive, after the final full acceptance run. A commit after the `head` of the last drive's `drive.json` changes a path an entry maps (a fix of the acceptance loop): Launch and Doctor, `drive --since <head of the last drive>`, the layout check, Cleanup. A failure: a fix round, the final full acceptance run again, then this step again. The ledger line `Feature gate: green at <sha>` comes after this step. No such commit: no closing drive.

## Stage 7: PR captures

The PR shows a before and an after screen of each route with `ok` true in the Stage 6 drives. A journey (an entry with its own `script` that records a video) shows a before and an after WebM. `capture` uploads the files with `gh pr edit --attach` (gh 2.99.0 or later): GitHub stores them as `user-attachments`, shown inline on desktop and mobile, only to repo readers. The files stay as long as the repo exists, also after a recapture.

`verify` absent or `false`, or no route with `ok` true: skip this section.

1. After set. In the feature worktree: `python3 .agents/skills/dev-loop/scripts/devloop.py capture --write-after <tmp>/after.json`. It reads the evidence folder: the newest drive with `mode` `diff` gives the entries, each newer `since` or `all` drive replaces the routes of an entry of that set it ran, and never adds one. The merged set is `<tmp>/after.json`; its `head` is `<after head>`. It calls no gh.
2. Before run, right after the PR opens. `git fetch origin`. Temporary folder: `mktemp -d`, worktree folder `<main worktree basename>-before` inside it. `git worktree add --detach <folder> $(git merge-base <after head> origin/main)`. No `verify` object in its `dev-loop.json`, or no verify skill there: no before run. Else, in it: the base verify skill's Launch (it installs what a fresh checkout needs, Playwright too), then this feature worktree's CLI from the base worktree: `python3 <feature worktree>/.agents/skills/dev-loop/scripts/devloop.py drive --routes-from <tmp>/after.json`, then the base Cleanup. Its `drive.json` is `<before drive.json>`. A route entry runs with the kit runner and no accessible names; a journey runs its own script when the base has it.
3. From the feature worktree: `python3 .agents/skills/dev-loop/scripts/devloop.py capture --pr <n> --after <tmp>/after.json [--before <before drive.json>]`. It writes `<git-dir>/dev-loop/evidence/<timestamp>-capture/` (`body.md`, `before.json`, the copied files) and replaces the section between `<!-- dev-loop: captures -->` and `<!-- /dev-loop: captures -->` in the PR body. At most 50 files: the rest is one line that names the evidence folder. A file over 10 MB is left out; a journey video over 10 MB falls back to the screenshots.
4. `git worktree remove --force <folder>`, then remove the temporary folder.
5. Recapture. A push after the capture (a CI fix, a review fix) that changes a path of a captured entry: stack up, Launch, `drive --since <sha of the capture>` (the after head of the capture: the `after:` sha in the section, the `head` of the after file), Cleanup, then `capture --pr <n> --before <capture folder>/before.json`. `before.json` keeps the before files, so the temporary worktree is not needed again.
6. Report line `captures: <n> routes`, or `captures: skipped: <reason>` when the before run or `capture` fails (`capture` exits 1 with `error`; the PR body is unchanged). Captures are evidence for a person, not a gate: the merge continues.

## Journey script contract

A journey script (an entry's `script`) reads the runner input on stdin: `base_url`, `storage_state`, `routes`, `evidence`, `entry`, and `video` (a folder `drive` creates). It drives its journey in one context, recorded with `browser.newContext({viewport: {width: 1280, height: 800}, recordVideo: {dir: input.video, size: {width: 1280, height: 800}}})`. After `context.close()`, it puts `await page.video().path()` in the `video` key of its route results. A script that does not record gives `video` null. `drive` keeps a `video` path only when it is a file inside that folder.

## Licenses

Steps 1 to 4 and the feature map contract adapt pstack `create-verification-skill` (https://github.com/cursor/plugins/tree/main/pstack, commit `c742dd6`, pstack 0.15.36): `.cursor/skills/` became `.agents/skills/`, the Helpers section became one line, step 4 drives with `devloop.py drive --all`. Quoted under the MIT License:

Copyright (c) 2026 Lauren Tan

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

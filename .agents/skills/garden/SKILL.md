---
name: garden
description: Weekly triage of the `## Gardener` section of the project note. Gives each item a verdict, turns a finding seen two times into an automatic check, builds up to 3 items as Track S slices. User-invoked only.
disable-model-invocation: true
---

# Garden

User asked for the weekly Gardener triage. Run in the main checkout, on `main`. CLI: `python3 .agents/skills/dev-loop/scripts/devloop.py <command>`.

## Inputs

- `$NOTES`: output of `python3 .agents/skills/dev-loop/scripts/notes_path.py`.
- Project note: `$NOTES/Project Note.md`, else `$NOTES/<notes folder name>.md`. Its `## Gardener` section.
- Log: `$NOTES/Gardener Log.md`.
- The `stacks:` line of the repo `AGENTS.md`, and the lint configuration files the repo has.

No notes path, or no project note: stop, report, change nothing.

## Open PRs first

Each item with a PR URL in its prefix `<verdict> (<date>, <PR URL>):`, check the PR (`gh pr view <url> --json state`):

- Merged: write its log row (see Log), remove the item.
- Closed without a merge: prefix back to `<verdict> (<date>):`.
- Open: no change.

A run never builds an item that has an open PR.

## Verdicts

One verdict per item:

- `encode`: seen two times. It gets a check.
- `fix`: a single defect or cleanup worth a Track S slice.
- `keep`: seen one time. Stays in `## Gardener`.
- `drop`: one-line reason: nitpick, hypothetical, taste, or the code is gone.

Seen two times: the item names the same finding in two or more places (the `lint candidate` mark), or the same finding is in another `## Gardener` item or in a `drop` row of the log. You decide what "the same finding" is. Quote both places in the log row.

## Check ladder

For `encode`, take the strongest rung the project can run:

1. structure or type (make the mistake impossible to write);
2. lint rule;
3. guard test over the repo;
4. `CODING_STANDARDS.md` line, only when the rule needs judgment.

The rungs come from `stacks:` and the lint configuration. No `stacks:` line: the rungs come from the lint configuration files only. Rungs 3 and 4 are always available.

## Proof

An `encode` check of rungs 1 to 3 must fail on a real past mistake before its commit:

1. `git worktree add --detach <scratch> <sha>`, at a commit that has the mistake (`HEAD` when it is still there).
2. Put the new check in the scratch worktree, run it: it must fail. Keep the first failing line.
3. `git worktree remove <scratch>`.

One commit holds the check and the removal of each current instance of the mistake, so the commit is green: `main` gets no red commit. Its body has the line `Proof: red at <sha>: <first failing line>`, `<sha>` the commit of the scratch run.

No red run: no `encode` commit, the item goes back to `keep`.

Rung 4 runs nothing. Its proof is each quoted past instance, body line `Proof: seen at <sha>: <file>:<line>`.

## Log

`$NOTES/Gardener Log.md`. Absent: make it with frontmatter, `# Gardener Log`, and the header:

| Date | Item | Verdict | Check | Proof |
| --- | --- | --- | --- | --- |

`Date` is `YYYY-MM-DD`. Rows:

- Each triage run: one row, `Verdict` `run`, `Item` `<n> items: <e> encode, <f> fix, <k> keep, <d> drop`.
- `drop`: its row at the triage, the item leaves `## Gardener`.
- `encode`, `fix`: its row when its slice PR merges. `Check`: the test name, the lint rule or the standards line (`fix`: empty). `Proof`: `red at <sha>` (rung 4: `seen at <sha>`; `fix`: the PR URL). The item leaves `## Gardener`.
- `keep`: no row.

## Slices

After the triage, build at most 3 items, one at a time: `encode` first (oldest first), then `fix`. Each is one Track S slice of dev-loop: branch and worktree from `origin/main`, test-first, Stage 6 review wave, triage and fix wave, Stage 7, under the Track S grant.

- Not built in this run: the item stays in `## Gardener` with the prefix `<verdict> (<date>):`. The next run builds it first.
- PR waits for the user (`merge-check` gives `human`): the item stays with the prefix `<verdict> (<date>, <PR URL>):`. Go on to the next item.
- A stop item of the grant ends the run.

Report: the verdict counts, the slices built with their PR links, the items left.

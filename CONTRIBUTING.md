# Contributing

Thank you for your interest in heldout-dev-loop. This guide tells you how to propose a change and how to get it merged.

## Propose a change

1. Open an issue first. Say what is wrong or what you need, and how you found it.
2. Wait for a go from the maintainer on the issue before you open a pull request. A pull request with no issue, or with no go, can be closed with no review.
3. There is no CLA and no DCO.

## How this repository works

The repository runs dev-loop on itself. The installed kit is pinned to the latest tag, release or pre-release (`version` in `dev-loop.json`).

- **Change the source, never the installed copy.** The kit source is in `skill/`, `skills/`, `claude/`, `codex/`, `bin/` and `lib/`. The installed copy is in `.agents/skills/`, `.claude/skills/`, `.claude/agents/`, `.codex/` and `.dev-loop/`. It changes only through `upgrade` to the next tag.
- **Never edit `tests/acceptance/`.** It is the held-out suite. The maintainer's `spec-tester` agent writes it from the approved spec, and the code author never reads it. A pull request that changes it is closed.
- `AGENTS.md` is the map of the repository for agents and for people. `CODING_STANDARDS.md` holds the code standards.

## Develop

```bash
git clone https://github.com/tayebchlyah/heldout-dev-loop.git
cd heldout-dev-loop
npm ci && npx playwright install chromium   # once per clone or worktree: the node suite drives a real browser
```

To try a change in a project, run `node <path to your clone>/bin/install.mjs init` (or `upgrade`) from the top level of that project.

## Before you open a pull request

Run the gate. All of it must be green:

```bash
scripts/gate.sh                             # both suites
npm test                                    # node suite only
python3 tests/unit/python/test_dev_loop.py  # skill suite only
python3 scripts/repo_rules.py comments      # comment rules of CODING_STANDARDS.md
python3 scripts/repo_rules.py subjects      # commit subject of HEAD
```

## Commits and pull requests

- Commit subject: `<emoji>(<scope>) #<issue>: <message>`. Use a gitmoji from the list in `cliff.toml`. The scope is required. Example: `🐛(board) #230: Keep the shipped row cap`.
- Pull request body: `Closes #<issue>`, what changed, how you tested it, and the gate result.
- Rebase only. Keep the history linear: no merge commit.

## Conduct and security

- Everyone in this project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Report a vulnerability as [SECURITY.md](SECURITY.md) says, never in a public issue.

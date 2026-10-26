# Release

1. Release PR: set `package.json` `version`, run `GITHUB_TOKEN=$(gh auth token) git cliff --tag vX.Y.Z -o CHANGELOG.md`.
2. Run `scripts/canary.sh`, paste its lines into the release PR.
3. Rebase the PR, no merge commit.
4. On `main`, at the commit to tag: `/release-audit` (Codex `$release-audit`, main session), then `python3 .agents/skills/dev-loop/scripts/devloop.py release-check --sha HEAD` must give `ok` true. A confirmed finding gets a fix slice, then a new audit. The git guard runs `release-check` on the tag push and shows its reasons.
5. On `main`: `git tag -a vX.Y.Z -m "dev-loop vX.Y.Z"`, `git push origin vX.Y.Z`. One tag per push: GitHub sends no push event for more than three tags.
6. Workflow checks version, makes notes, makes the GitHub Release.

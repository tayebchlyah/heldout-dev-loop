# Security policy

## Supported versions

Only the latest release gets security fixes. Upgrade to it before you report.

## Report a vulnerability

Report it privately through GitHub private vulnerability reporting: [open a security advisory](https://github.com/tayebchlyah/heldout-dev-loop/security/advisories/new). Never report a vulnerability in a public issue, a pull request or a discussion.

Give the version, the harness (Claude Code or Codex) and its version, the steps to reproduce, and what an attacker gets.

## Threat model

- **The walls and the seal stop a careless agent, not a hostile one.** The hooks deny a subagent the read of the held-out suite, the edit of kit files and the risky git and gh commands. The seal and the transcript audit find what gets past them. They do not stop an agent, a person or a program that sets out to defeat them.
- **The hooks run on your machine, with your rights.** dev-loop adds no sandbox around them. A hook runs each time the harness calls it.
- **The known gaps are written down, not closed.** See the `Gaps` lines in [`skill/references/claude.md`](skill/references/claude.md) and [`skill/references/codex.md`](skill/references/codex.md).
- **Each release has a security audit.** `/release-audit` runs before each release tag, and an open security finding blocks the tag ([`reference/release.md`](reference/release.md)).

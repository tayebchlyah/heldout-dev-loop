# Deferred triage

Read this file before you rule on a `## Deferred` item: the Stage 1 cap check, a release, any ruling the user asks for.

- Security item: never propose to keep it open, `accept` or `close` it on a reading alone when a live check is cheap. A cheap check is a script, test or command that calls the real code and shows the hole open or closed, in minutes. Run it first, quote its output under the item, then propose the ruling. No cheap check: say so, and name the check that would settle it.
- A fix that rests on a CLI flag: quote the line of the CLI's own `--help` (or its docs) that says what the flag does, never a summary of it.

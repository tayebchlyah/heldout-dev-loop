# Maps

Work larger than one feature gets a map: the Stage 1 map trigger, then the route's `wayfinder` skill. This file is the issue tracker `wayfinder` asks for. A project `docs/agents/issue-tracker.md` wins over it: use its Wayfinding operations instead.

Root: `<notes>/Maps/<Effort Title>/`, `<notes>` the output of `scripts/notes_path.py`. Never inside the repo. `<Effort Title>`: the effort's title.

- `handoff.md`: the grilling state Stage 1 hands over. Read it first when the user says "chart the map from" its path.
- Destination (the map's first section): when you chart, write it as the list of dev-loop features, by slug, each with the map decisions it relies on. Stage 1 of each feature reads it to find its map.

## Wayfinding operations

Source: Pocock skills v1.3.2 `skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md`, section "Wayfinding operations" (https://github.com/mattpocock/skills, commit `d1478fc`). Changed: the root is the notes folder, not `.scratch/`; the opening line "Used by `/wayfinder`. ..." is dropped. MIT License, Copyright (c) 2026 Matt Pocock: full notice in [spec.md](spec.md#licenses).

- **Map**: `<notes>/Maps/<Effort Title>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `<notes>/Maps/<Effort Title>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `<notes>/Maps/<Effort Title>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.

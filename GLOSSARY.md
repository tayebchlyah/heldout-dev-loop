# dev-loop

A stage-driven development loop for coding agents, and the installer that puts it into a project.

## Install

**Project**:
A repo that runs dev-loop, marked by `dev-loop.json` at its root.
_Avoid_: target, consumer repo

**Harness**:
The agent runtime a project runs dev-loop under: Claude Code or Codex.
_Avoid_: client, IDE, agent

**Kit**:
The dev-loop files a project receives whole and never edits: the skill, its scripts, the subagents, the hooks.
_Avoid_: payload, bundle, vendored copy

**Kit file**:
One file of the kit, owned by dev-loop and changed only by an upgrade.

**Merged file**:
A project config file dev-loop adds entries to and never replaces: Claude settings, Codex hooks, Codex config.
_Avoid_: patched file

**Manifest**:
The record of which kit version a project holds and what each kit file contained at install.

**Upgrade**:
Moving a project's kit to a newer release, reviewed as its own pull request.
_Avoid_: update, sync

**Source**:
dev-loop's own editable files, from which each release builds the kit.
_Avoid_: upstream

**Installed kit**:
A kit inside a project, pinned to one release. In the dev-loop repo itself it sits beside the source and is never edited.

## Loop

**Feature**:
One unit of work, named by a slug, from framing to merge.
_Avoid_: ticket, task, story

**Track**:
The size class of a feature, S, M or L, which decides which stages it runs.

**Stage**:
One step of the loop, numbered 0 to 7, from bootstrap to ship.
_Avoid_: phase, step

**Session A**:
The session that frames a feature and ends when the user approves its spec.

**Session B**:
The session that runs a feature from acceptance tests to merge without stopping for routine decisions.

**Autonomy grant**:
The permission Session B holds to decide open questions itself and record them, stopping only for the listed exceptions.

**Spec**:
The approved description of a feature: its behaviour, interfaces and acceptance oracle.
_Avoid_: design doc, PRD

**Acceptance oracle**:
The list of statements in a spec that decide whether the feature is correct.

**Plan**:
The ordered tasks that build an approved spec, each small enough for one fresh agent.

**Held-out suite**:
The acceptance tests written from the spec alone, which no implementer reads.
_Avoid_: e2e tests, integration tests

**Gate**:
The project's fast check that must pass before a stage ends.
_Avoid_: CI, test run

**Feature gate**:
Stage 6: one review wave, the fixes it calls for, and the held-out suite run to green.

**Review wave**:
The one set of parallel reviews of a whole feature branch, run once per feature.

**Shipped**:
A feature whose pull request is merged and whose spec records the merge date.

**Deferred**:
A finding left for later, held in the project note, at most 20 open.

## Notes and board

**Notes folder**:
The folder outside the repo, in the user's vault, that holds a project's specs, plans and board.
_Avoid_: docs folder, wiki

**Board**:
The live view of open features, built from the status notes every machine writes to the notes folders it serves.
_Avoid_: dashboard, kanban

**Status note**:
One note per feature per machine that the board shows. Only board runs write it, never a person or an agent.

**Claim**:
The mark that one machine works on a feature, set when its spec is written.

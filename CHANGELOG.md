# Changelog

## [2.0.0] - 2026-10-13

### ♻️ Refactors & Cleanup

- [`a4bf4e3`](https://github.com/tchlyah/dev-loop/commit/a4bf4e374ef3bf9d597be35256a91f7e177e3e6b) - **skill**: v2 skill stack: grilling, writing-plans, native or SDD, Pocock tdd and pr

- [`95ac2d3`](https://github.com/tchlyah/dev-loop/commit/95ac2d3ce6681280e81bf0d1cf9f957a0d795c3c) - **gate**: One review wave of three reviewers, no simplify, mutation nightly

- [`d9e2ea8`](https://github.com/tchlyah/dev-loop/commit/d9e2ea820c022fc527c9b7f0a19da1c4fe5e9758) - **check**: Replace the dead global check with one init line



### ✨ New Features

- [`1185d29`](https://github.com/tchlyah/dev-loop/commit/1185d2907540ebcfdb9346ec09fc1a21a1cd6505) - **cli**: devloop status, logged, gate --summary and ci wait, commands map



### 🐛 Bug Fixes

- [`e08e5d5`](https://github.com/tchlyah/dev-loop/commit/e08e5d55b7b0ff6a12f53f6681c8a277bd212258) - **cli**: Ship a merged feature with no branch, never read a failed git cherry as empty

- [`a374e8c`](https://github.com/tchlyah/dev-loop/commit/a374e8c88f0d181a44ad60c2e40950a31514b577) - **skill**: Send Track S through the review wave, replace only known old SessionStart commands

- [`4d9c4bd`](https://github.com/tchlyah/dev-loop/commit/4d9c4bd804782cac7eb6680d614e1f4ed1be9116) - **cli**: Fix devloop status detection, ci wait retry and spec-tester naming

- [`47c2e21`](https://github.com/tchlyah/dev-loop/commit/47c2e2127b14c75e21db62eb7f6218cd492cd52f) - **hooks**: Run the mapped commands.gate in the Stop and SubagentStop gate

- [`b6e70a5`](https://github.com/tchlyah/dev-loop/commit/b6e70a5f5bda8cd5a0f1aaf8d1b3cc8a5e280a4a) - **skill**: Run the Pocock desks and every reviewer in the feature worktree



### 💥 Breaking Changes

- [`b4c4caa`](https://github.com/tchlyah/dev-loop/commit/b4c4caa3a4d2063b6c86670381dbff19eb635c02) - **skill**: Tracks, chained sessions, devloop status detection, no 1.4 fallbacks



### 📝 Documentation & Content

- [`1b3320c`](https://github.com/tchlyah/dev-loop/commit/1b3320c14e5fb4f9f02f80f37399da02f2f867e7) - **skill**: Ponytail rules, vendor scope paragraphs and test rules for implementers

- [`a2f20a2`](https://github.com/tchlyah/dev-loop/commit/a2f20a24ed33ed5b2f82e19cb3cc07b50fa63e94) - **skill**: Match vendor quote, trim test rules, fix building.md headings

- [`4bca392`](https://github.com/tchlyah/dev-loop/commit/4bca3923d8d74846cb21e5772fea14af5d7ac4ca) - **changelog**: Regenerate CHANGELOG.md for v2.0.0

- [`931439d`](https://github.com/tchlyah/dev-loop/commit/931439d29ebaa1bb2253678f87766cf14300d824) - **skill**: Describe the nightly CI workflow, the app CLI skeleton and stack_up


## [1.5.0] - 2026-10-13

### ♻️ Refactors & Cleanup

- [`6ae4e32`](https://github.com/tchlyah/dev-loop/commit/6ae4e32ae09fd4c5605bdda70198caf53487ab09) - **guard**: Remove the vault rule from the kit *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`c10b8b3`](https://github.com/tchlyah/dev-loop/commit/c10b8b36ab164b617bd99f4a4074896a7f1ae40c) - **setup**: Check the notes path through the resolver *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`21faedf`](https://github.com/tchlyah/dev-loop/commit/21faedf1cfd16923a24112409520f6da533cb8ca) - **install**: Drop the vault flag, variable and prompt *(commit by [@tchlyah](https://github.com/tchlyah))*



### ✨ New Features

- [`73b5fd2`](https://github.com/tchlyah/dev-loop/commit/73b5fd2ac3d9b670a25a3adabbeaec311d409722) - **install**: Route the init, upgrade and uninstall-global commands *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`82f143b`](https://github.com/tchlyah/dev-loop/commit/82f143b317c639d50457cb51332b7c12d8a9a2c9) - **install**: Build the project kit and its manifest *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`061a9f8`](https://github.com/tchlyah/dev-loop/commit/061a9f8cf53e9fc1161f996f528bd8f53305074e) - **install**: Add the init command *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`6877dd4`](https://github.com/tchlyah/dev-loop/commit/6877dd4f08e47c9577476be5c6cde0209975ebe5) - **install**: Add the upgrade command *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`b2fbcf8`](https://github.com/tchlyah/dev-loop/commit/b2fbcf83fa3284ff4cf986264912f218d8be557a) - **install**: Add the uninstall-global command *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`bd5a0f4`](https://github.com/tchlyah/dev-loop/commit/bd5a0f4db72efe0ac2a86aaa1041c5d4c0d363a9) - **check**: Check the project kit against its manifest *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`45a0327`](https://github.com/tchlyah/dev-loop/commit/45a03274025ea72640296ee2a3962d6974fe82fc) - **notes**: Add the notes path resolver *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`fd69719`](https://github.com/tchlyah/dev-loop/commit/fd697198d9d0901aa9764784d8b8abd244e9edcf) - **hooks**: Detect dev-loop by dev-loop.json at session start *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`7199331`](https://github.com/tchlyah/dev-loop/commit/7199331aa5bf64d59f0016e0fd2533af638f7357) - **skill**: Accept GLOSSARY.md or CONTEXT.md, and continue every Superpowers 7 size to Spec *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🐛 Bug Fixes

- [`13ea692`](https://github.com/tchlyah/dev-loop/commit/13ea6929b5f1362bd233a28dea495d9d3b8bbf4a) - **check**: Print legacy Claude failures once and keep the repo walls in project mode *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`3e8b208`](https://github.com/tchlyah/dev-loop/commit/3e8b208a24ac5681a748c55ff5e93456fdb0ea85) - **check**: Point the global remediation at the --global install command *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`19eab4a`](https://github.com/tchlyah/dev-loop/commit/19eab4ad7ca5845ce099099bb93178b094a7eaef) - **install**: Match the vault guard by its git-guard.sh path and name where to move it *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`b3eb47a`](https://github.com/tchlyah/dev-loop/commit/b3eb47a3570c37f403d86ddcb0a8d70a0cd47725) - **install**: Remove hook entries of dropped kit scripts on upgrade *(PR [#4](https://github.com/tchlyah/dev-loop/pull/4) by [@tchlyah](https://github.com/tchlyah))*

- [`e8137a3`](https://github.com/tchlyah/dev-loop/commit/e8137a36f3d6ed4aa589b457afc1b6a9ae646167) - **guard**: Follow cd through assignments and subshells, and check a push against each repo *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`0843439`](https://github.com/tchlyah/dev-loop/commit/08434390d363f6ef445a143981c5bf320fdac593) - **guard**: Ask on a push whose folder or target the guard cannot be sure of *(PR [#5](https://github.com/tchlyah/dev-loop/pull/5) by [@tchlyah](https://github.com/tchlyah))*

- [`7f28eda`](https://github.com/tchlyah/dev-loop/commit/7f28edab43e7d6a4086a249fa9b6ce4b792dc96f) - **hooks**: Check only what the shell runs, and never ask from a subagent *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`91bbd92`](https://github.com/tchlyah/dev-loop/commit/91bbd92631a22ebb92d2596b3920feccda14f5b9) - **guard**: Name an unknown repository only where the guard said detached HEAD *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`93f3dfb`](https://github.com/tchlyah/dev-loop/commit/93f3dfbafcaaa6a21f2d1c8431281f4742db733f) - **guard**: Follow a cd into a variable the line surely set, and strip heredocs before every check *(PR [#6](https://github.com/tchlyah/dev-loop/pull/6) by [@tchlyah](https://github.com/tchlyah))*

- [`f056edc`](https://github.com/tchlyah/dev-loop/commit/f056edc7ac37fe7a8dcb488ad09fd0fa42bff3ed) - **notes**: Expand a variable in the legacy vault_notes value *(PR [#7](https://github.com/tchlyah/dev-loop/pull/7) by [@tchlyah](https://github.com/tchlyah))*

- [`f90d922`](https://github.com/tchlyah/dev-loop/commit/f90d9224dcdf0a8586114c84f1a527d969d84a09) - **guard**: Follow a variable whose absolute path holds a ~ *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`48fd730`](https://github.com/tchlyah/dev-loop/commit/48fd730aa9dffa5ba236ab018eae879d8afe8ed7) - **skill**: Escape the || in the Stage 0 table row *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`d1ecfbc`](https://github.com/tchlyah/dev-loop/commit/d1ecfbc0f9aeb6cef0db0181ef3bb5214838491e) - **init**: Write the Claude repo contract into the project settings *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`ff4d421`](https://github.com/tchlyah/dev-loop/commit/ff4d42122598feb13946cc35a6bbe0a5a31e01df) - **install**: Name only the kept Codex tables that differ from dev-loop's *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`7a6d7ac`](https://github.com/tchlyah/dev-loop/commit/7a6d7ac6c1c1788f69906362aa1ecd4fb49b6a29) - **check**: Write no __pycache__ into the vendored skill folder *(commit by [@tchlyah](https://github.com/tchlyah))*



### 📝 Documentation & Content

- [`aa07838`](https://github.com/tchlyah/dev-loop/commit/aa0783808e7a50300bbaceb76055a47a15777da4) - **changelog**: Gitmoji git-cliff config and CHANGELOG.md *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`33c0dbe`](https://github.com/tchlyah/dev-loop/commit/33c0dbe07e72af5afe4b83d7fa062320382a6ec5) - **agents**: Fix the commit convention and add the release steps *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`5e55916`](https://github.com/tchlyah/dev-loop/commit/5e55916a2b0e9d732f4fcddf09bdc7f8279e5734) - **changelog**: Regenerate CHANGELOG.md *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`c5de453`](https://github.com/tchlyah/dev-loop/commit/c5de453973dc8560569875866f278aa5276750d0) - **agents**: State the CI merge rule without a ruleset *(PR [#3](https://github.com/tchlyah/dev-loop/pull/3) by [@tchlyah](https://github.com/tchlyah))*

- [`88517ee`](https://github.com/tchlyah/dev-loop/commit/88517ee40830c6a80ab306546e5163ce106f0fbe) - **docs**: Describe the project install commands *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`623b9e7`](https://github.com/tchlyah/dev-loop/commit/623b9e7e8cbef689ef7c9fc7d9ecf8132c3b9cd5) - **install**: Drop spec decision labels from the installer comments *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`70bfb59`](https://github.com/tchlyah/dev-loop/commit/70bfb59ed1f5bdad5190f980ff06535e55440ec0) - **docs**: Lead the README with init, scope --link and the top-level rule, drop em-dashes in the repo map *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`3d72336`](https://github.com/tchlyah/dev-loop/commit/3d72336cd0a0ed5a2a7361a3424e4afc976cafff) - **skill**: Use the notes folder in place of the vault *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`36691eb`](https://github.com/tchlyah/dev-loop/commit/36691ebcd9419a28e89127d4f2b9473e43858fa5) - **docs**: Document upgrade, rollback, the first init on a machine and the notes folder *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`c2af901`](https://github.com/tchlyah/dev-loop/commit/c2af901b44951bb04c5c49c85558d18008f09f2d) - **changelog**: Regenerate CHANGELOG.md for v1.5.0 *(PR [#8](https://github.com/tchlyah/dev-loop/pull/8) by [@tchlyah](https://github.com/tchlyah))*

- [`8d805a5`](https://github.com/tchlyah/dev-loop/commit/8d805a553f674838235136985f51f4b5650ee4d1) - **changelog**: Regenerate CHANGELOG.md for v1.5.0 *(PR [#9](https://github.com/tchlyah/dev-loop/pull/9) by [@tchlyah](https://github.com/tchlyah))*



### 🔒 Security

- [`6cee4f9`](https://github.com/tchlyah/dev-loop/commit/6cee4f99ab2baff51cf50044d1a6297f5ad17c86) - **release**: Let git-cliff read pull requests, pin the git-cliff version *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`e7efdc0`](https://github.com/tchlyah/dev-loop/commit/e7efdc0c75f4998a5a7e4195d53384b82ddd769b) - **install**: Refuse symlinked paths and a folder at the skill link in init *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`c667497`](https://github.com/tchlyah/dev-loop/commit/c6674973e3cb8fa7dd5e2c95791c337266488720) - **install**: Delete only kit-owned files named by the old manifest in upgrade *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`4458457`](https://github.com/tchlyah/dev-loop/commit/4458457685c33061921c41ad257d4b7b774858e4) - **install**: Back up a user file at a kit path instead of overwriting or deleting it *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`7f3fbb2`](https://github.com/tchlyah/dev-loop/commit/7f3fbb26e78471a21e93819a14418e7b023e605a) - **install**: Keep project backups beside their target *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`a836b1a`](https://github.com/tchlyah/dev-loop/commit/a836b1a05a3f4606dde0f153ce5b4e20c20e0324) - **guard**: Treat a ~ after : or = in an assignment as an expansion *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🔖 Release & Versioning

- [`255ee3c`](https://github.com/tchlyah/dev-loop/commit/255ee3c453403d668e989233f2575ff15288560e) - **release**: Version 1.5.0 *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🔧 Infrastructure & DevOps

- [`04c05dd`](https://github.com/tchlyah/dev-loop/commit/04c05ddf298d0ac037fa1ac3cfca72cc32dfc021) - **release**: Check the tag against package.json, pin actions, pass the token to git-cliff *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`b46baf7`](https://github.com/tchlyah/dev-loop/commit/b46baf7f3cee77fa4d167c309cbcbaf897ba97d6) - **ci**: Run both suites on every PR and on main *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`ab87daf`](https://github.com/tchlyah/dev-loop/commit/ab87dafb2e5a75cd88dc18a8d412b80dfc30452d) - **claude**: Turn off the commit and stop security reviews in this repository *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🧪 Tests & Quality

- [`b075b8d`](https://github.com/tchlyah/dev-loop/commit/b075b8d8e3aba72a203c5dccf33d0cd514df7c28) - **skill**: Pin the glossary and size rules *(commit by [@tchlyah](https://github.com/tchlyah))*


## [1.4.0] - 2026-10-12

### 🐛 Bug Fixes

- [`c695381`](https://github.com/tchlyah/dev-loop/commit/c6953818bc8fb312086298fcb417e89fc61346e5) - **wall**: Match a denied path on whole segments only *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`b99f8e8`](https://github.com/tchlyah/dev-loop/commit/b99f8e8f00ab71e2ff0b74e54ffade9e33e1ed2a) - **install**: Back up a replaced skill outside its skills folder *(commit by [@tchlyah](https://github.com/tchlyah))*



### 📝 Documentation & Content

- [`23499a8`](https://github.com/tchlyah/dev-loop/commit/23499a8fef4b15c9a80516ec0ffa13aab56c0d21) - **implementer**: Drop the grading sentence on both harnesses *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`8d4b227`](https://github.com/tchlyah/dev-loop/commit/8d4b2275651c9bb0ba2ba026608e900bf03fd534) - **skill**: State the one-stage and no-plan rules without caps, with the reason *(PR [#2](https://github.com/tchlyah/dev-loop/pull/2) by [@tchlyah](https://github.com/tchlyah))*



### 🔒 Security

- [`0d24f71`](https://github.com/tchlyah/dev-loop/commit/0d24f716cd2d87458e04fe3c8d71022664409f9e) - **guard**: A plain feature-branch push runs, a push that rewrites or reaches main asks *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`c5a82ea`](https://github.com/tchlyah/dev-loop/commit/c5a82ea0a409c388994a3f3d767efd583d8f9552) - **guard**: Parse a push with shlex and fail closed, so quotes, -uf, -c, aliases and remote config ask *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`6b70a02`](https://github.com/tchlyah/dev-loop/commit/6b70a02c106312d1d831a999c41010b7a2497d82) - **guard**: Find git anywhere in a segment, and ask on shell expansion, fed arguments and same-line drift *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`f38d6c7`](https://github.com/tchlyah/dev-loop/commit/f38d6c71c7768b8a6abc6c09fbe2842bd5ea793d) - **guard**: Give the Codex guard the push rule, and check vault writes per git call *(commit by [@tchlyah](https://github.com/tchlyah))*


## [1.3.0] - 2026-10-03

### ⚡️ Performance

- [`99c96fc`](https://github.com/tchlyah/dev-loop/commit/99c96fc62519b8676c986063436b7c6829b51c59) - **gate**: Hooks ask for the selective gate, skip read-only and no-write subagents, and a stage ends on a full gate *(commit by [@tchlyah](https://github.com/tchlyah))*



### 📝 Documentation & Content

- [`35bd422`](https://github.com/tchlyah/dev-loop/commit/35bd4227fdb7d67023b7a58ecdf23bf5030f63fb) - **spec-tester**: Prove each flow once, and dispatch no subagent *(PR [#1](https://github.com/tchlyah/dev-loop/pull/1) by [@tchlyah](https://github.com/tchlyah))*



### 🔒 Security

- [`c1c89a3`](https://github.com/tchlyah/dev-loop/commit/c1c89a3165f976a2afa10301daa47956d3ccc396) - **wall**: Resolve subagent paths against the working directory *(commit by [@tchlyah](https://github.com/tchlyah))*


## [1.2.0] - 2026-10-01

### ♻️ Refactors & Cleanup

- [`721e8bf`](https://github.com/tchlyah/dev-loop/commit/721e8bf1cf14775403edd6185f9c21fc856bb7a3) - **gate**: Run reviews once in parallel, acceptance last *(commit by [@tchlyah](https://github.com/tchlyah))*



### ⚡️ Performance

- [`31e79f7`](https://github.com/tchlyah/dev-loop/commit/31e79f7ffbb4593aaada8694d8eed9b8bb636e41) - **gate**: Mutate only the functions a feature changed *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`ed9b09b`](https://github.com/tchlyah/dev-loop/commit/ed9b09bdf2088f63f93e7cfca5eb61b5b920c9f4) - **gate**: Mutate critical modules only, one test per function *(commit by [@tchlyah](https://github.com/tchlyah))*



### ✨ New Features

- [`b002351`](https://github.com/tchlyah/dev-loop/commit/b002351495481e5b0daa65b47c4f144245e4897e) - **loop**: One worktree per feature, and superseded oracle lines *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🐛 Bug Fixes

- [`ab3853c`](https://github.com/tchlyah/dev-loop/commit/ab3853c8fad3efab60ca06ac88660716a920fdf1) - **gate**: A flaky test is a race to name and fix, never a rerun to green *(commit by [@tchlyah](https://github.com/tchlyah))*



### 📝 Documentation & Content

- [`696088c`](https://github.com/tchlyah/dev-loop/commit/696088cd750ea0a253792422af6e3fbe1cf016ae) - **route**: Name subagent models by alias only *(commit by [@tchlyah](https://github.com/tchlyah))*


## [1.1.0] - 2026-09-18

### 🐛 Bug Fixes

- [`453427f`](https://github.com/tchlyah/dev-loop/commit/453427fb97ff258444b21f17df78680cc251a13d) - migrate legacy Codex sandbox mode *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`8da96fb`](https://github.com/tchlyah/dev-loop/commit/8da96fb9b1c405c333cc6909958a148bbb120f03) - copy Codex agents in link mode *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`ff50d3b`](https://github.com/tchlyah/dev-loop/commit/ff50d3bf6dbf51d73d9df2b9a20f2d41d1f0ba66) - clarify handling of unplanned vs. planned deployment and infra work *(commit by [@tchlyah](https://github.com/tchlyah))*

## [1.0.0] - 2026-09-14

### ✨ New Features

- [`02f4363`](https://github.com/tchlyah/dev-loop/commit/02f4363bc5011bbcc09c32194e37937820226ead) - seed dev-loop payload from local installation *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`c6f0bef`](https://github.com/tchlyah/dev-loop/commit/c6f0befc77430e6900d255c4b42aede48b6dbe34) - add harness configuration fragments *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`477fb8c`](https://github.com/tchlyah/dev-loop/commit/477fb8cf20f88fbd6aa350962a70e9e499833c25) - parse installer flags and build the install configuration *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`52b4d5e`](https://github.com/tchlyah/dev-loop/commit/52b4d5e49b00911b26fb48590b7fe29e2f60ee7c) - place files by copy or link with timestamped backups *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`e5d7c82`](https://github.com/tchlyah/dev-loop/commit/e5d7c8287ee84718dda943b86ef440845257a5d0) - merge hook and deny entries without touching foreign entries *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`5b86b9f`](https://github.com/tchlyah/dev-loop/commit/5b86b9fd37cd2b01be58b5b25695baca39cb3778) - merge Codex permission profiles into an existing config *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`11a9275`](https://github.com/tchlyah/dev-loop/commit/11a9275ff74e0a9b7678ad6a278d93fd667c5058) - append missing Codex rule blocks without touching a user's own rules *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`b3d13e0`](https://github.com/tchlyah/dev-loop/commit/b3d13e06a500e63c0ec28565b509c2a61943b140) - build the installation plan with per-action merge routing *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`dedf1ba`](https://github.com/tchlyah/dev-loop/commit/dedf1bab246e08ee6a1bd4bfc910553da81751b2) - install the kit by copy or link from one entry point *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`86244f3`](https://github.com/tchlyah/dev-loop/commit/86244f3a1838b14661921f983f3a038bb87b7c0f) - read the vault path from the installed configuration *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`a670548`](https://github.com/tchlyah/dev-loop/commit/a67054812ae33511bbebd57a56184f1aa2ccaf1e) - link the Codex hooks in link mode *(commit by [@tchlyah](https://github.com/tchlyah))*


### 🐛 Bug Fixes

- [`6bde3f3`](https://github.com/tchlyah/dev-loop/commit/6bde3f3061032ac26179977d752dc96489ee948d) - keep backupPath pure and resolve backup collisions on disk *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`9b40aa1`](https://github.com/tchlyah/dev-loop/commit/9b40aa15894b02809fdcf8ead2d73ca1e8fa5522) - never duplicate a TOML table or drop a justification-less rule block *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`dbf7e1a`](https://github.com/tchlyah/dev-loop/commit/dbf7e1ad00babc0b78c4b7200d9d3e8a9631799b) - normalize TOML headers before comparing, and report skipped tables *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`06a79d3`](https://github.com/tchlyah/dev-loop/commit/06a79d35f95106df0e8eb6653a2b81351a42846c) - heal unreadable directory entries and name the failing path *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`5f1a4e1`](https://github.com/tchlyah/dev-loop/commit/5f1a4e12cb39dd75b65aee3bd2b6bdf05f71f4a8) - name the missing checker script instead of crashing on a null spec loader *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`1aefc70`](https://github.com/tchlyah/dev-loop/commit/1aefc701003e6c49178abc4df93643c2406c5d27) - correct check_setup output claims, commit convention, and changelog parsing *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`628c987`](https://github.com/tchlyah/dev-loop/commit/628c987e992a4211946ea26b0e1d1d16d97f566c) - dedup Codex config by key and table, not by line text *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`8942239`](https://github.com/tchlyah/dev-loop/commit/894223948b1e68cf00e18eca3b488d42521b5628) - keep the configured vault across re-runs and back the config up *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`c2a32ff`](https://github.com/tchlyah/dev-loop/commit/c2a32ff5199759018ac892295eaf6e070472e9bb) - survive a malformed hook group and stop clobbering backups *(commit by [@tchlyah](https://github.com/tchlyah))*


### 📝 Documentation & Content

- [`9009864`](https://github.com/tchlyah/dev-loop/commit/90098641ddb5358952af3ad77a6d131105639e47) - document the install, the payload and the development loop *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`6bb4f9a`](https://github.com/tchlyah/dev-loop/commit/6bb4f9a91a22d85ac2e297e81a8eee2e4e4d2008) - state the private remote in a way that stays true after publishing *(commit by [@tchlyah](https://github.com/tchlyah))*


### 🔒 Security

- [`cb83e21`](https://github.com/tchlyah/dev-loop/commit/cb83e216db328486e06ab351d4194a5521b60e75) - ship only dev-loop safety rules and repair the test script *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`978be11`](https://github.com/tchlyah/dev-loop/commit/978be11a5affaacf5d6ac66ce81b7247e37ffd60) - match deny walls on path fields, not the whole tool input *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`a8098e1`](https://github.com/tchlyah/dev-loop/commit/a8098e1c430bd8c246d2eb7ab8646ef567887b47) - close the glob hole in the deny walls and replace stale links *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`fd30d41`](https://github.com/tchlyah/dev-loop/commit/fd30d41ae4dec982e1a5b339dd0b4ca79b30c10a) - normalize deny-wall candidates and state the wall's real limits *(commit by [@tchlyah](https://github.com/tchlyah))*


### 🧪 Tests & Quality

- [`00118c6`](https://github.com/tchlyah/dev-loop/commit/00118c6546f9ec308ca4ac932da150e08b2965d2) - assert exact dev-loop rule set instead of keyword matching *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`2bd1fac`](https://github.com/tchlyah/dev-loop/commit/2bd1fac99520e3b57edffa9ace75d9ce4dd67aa3) - assert exact fragment content instead of shape *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`2eae6be`](https://github.com/tchlyah/dev-loop/commit/2eae6be39b3d662457b05427809a35f5d2b66405) - cover empty DEV_LOOP_VAULT falling through to prompt/null *(commit by [@tchlyah](https://github.com/tchlyah))*
- [`2d3a7ee`](https://github.com/tchlyah/dev-loop/commit/2d3a7ee290a775f8d239f0ffa8c21d9d264e64a1) - seed a conflicting profile and check the merged TOML parses *(commit by [@tchlyah](https://github.com/tchlyah))*


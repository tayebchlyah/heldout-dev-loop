# Changelog

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


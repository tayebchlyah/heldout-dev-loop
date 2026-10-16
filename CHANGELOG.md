# Changelog

## [2.1.0] - 2026-10-16

### ♻️ Refactors & Cleanup

- [`3aef282`](https://github.com/tchlyah/dev-loop/commit/3aef282d3bb9ab86c06068bc4ef7f9e141326699) - **board**: O2: one home in store.py for the frontmatter parse, version_key and root_of *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`e753e6e`](https://github.com/tchlyah/dev-loop/commit/e753e6e6dabb26cc6a435b681469cf9140c23bb8) - **board**: A3, A4: group the shared run values of merge and write in a Context, one-line comments *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`26500b4`](https://github.com/tchlyah/dev-loop/commit/26500b49585df7e2e04271307435aa74c0fb7570) - **board**: Oracle tests: table rows for paired cases, slow markers, log_message signature *(PR [#12](https://github.com/tchlyah/dev-loop/pull/12) by [@tchlyah](https://github.com/tchlyah))*



### ⚡️ Performance

- [`60496c2`](https://github.com/tchlyah/dev-loop/commit/60496c2e228a78307351e81d4f179cccce5b2f1e) - **board**: A2: write a status note only on a change, list it in notes either way *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`8e74f6d`](https://github.com/tchlyah/dev-loop/commit/8e74f6d0c6f9ac3c301351972638e20e55112e2b) - **tests**: E1: skill suite from about 238 s to 145 s *(commit by [@tchlyah](https://github.com/tchlyah))*



### ✨ New Features

- [`c22db3d`](https://github.com/tchlyah/dev-loop/commit/c22db3d0e15287b94def5d71b1bcd042dc004391) - **board**: Render the progress board with renderBoard *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`8d86d88`](https://github.com/tchlyah/dev-loop/commit/8d86d88f134da5281cc26706eed6c25e9f8e2e79) - **board**: Write status notes with devloop.py board *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`e6c8772`](https://github.com/tchlyah/dev-loop/commit/e6c8772df00aa5e02fd3c37f25b4de9a3e913e94) - **board**: Track waiting, ship and the Obsidian board note *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`d02b7e3`](https://github.com/tchlyah/dev-loop/commit/d02b7e3e0aeb09493c8de66e33bfdb5648d4265c) - **board**: Serve the board on 127.0.0.1 *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`017e26f`](https://github.com/tchlyah/dev-loop/commit/017e26f4a4503f3cbe2263fa09b32575af1cbd08) - **hooks**: Update the board on session start, prompt and stop *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`950186a`](https://github.com/tchlyah/dev-loop/commit/950186ae7ce64ae3bd15f91d8cb8116b4685a281) - **cli**: Skip Stage 3 for a spec with acceptance: regression or none *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`5053b28`](https://github.com/tchlyah/dev-loop/commit/5053b28ea390181abbab9ed2f8b61c5e4ec35e4e) - **board**: Leave Stage 3 out of the bar for a spec with acceptance: regression or none *(PR [#14](https://github.com/tchlyah/dev-loop/pull/14) by [@tchlyah](https://github.com/tchlyah))*

- [`234dafe`](https://github.com/tchlyah/dev-loop/commit/234dafe02623c0785bca945b451aa3febedf366b) - **skill**: Keep the stage and task checklist in the native plan tool *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`6522aac`](https://github.com/tchlyah/dev-loop/commit/6522aacecfda56ba6ec4cb8d2122e330d7c5f315) - **install**: Set CLAUDE_CODE_ENABLE_TODO_TOOLS=1 in the project Claude settings *(PR [#15](https://github.com/tchlyah/dev-loop/pull/15) by [@tchlyah](https://github.com/tchlyah))*

- [`b076017`](https://github.com/tchlyah/dev-loop/commit/b076017ee7cd5c767856062d365e7b320ca44e9f) - **mutation**: Commit a fix-wave test only when a rerun on the listed functions shows it kills a listed mutant *(PR [#16](https://github.com/tchlyah/dev-loop/pull/16) by [@tchlyah](https://github.com/tchlyah))*



### 🐛 Bug Fixes

- [`16abf56`](https://github.com/tchlyah/dev-loop/commit/16abf56d87b2601d840262960337a8cb5cb30853) - **board**: D4, D9, O4: drop the Waiting duration from Needs you, count zero-length tasks in the time left mean, guard inline embeds *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`171cff5`](https://github.com/tchlyah/dev-loop/commit/171cff5cd0e539d185f2fccbc94ee10f73d2a220) - **hooks**: D1, D12, O1: detach the board run, take the Claude CLI from the project, gate.sh calls board.sh *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`94ebb6f`](https://github.com/tchlyah/dev-loop/commit/94ebb6f2fd85bfbed693cafd39371e6f1d8683d1) - **board**: D2, D5, D6, D7, D10, O5: queue busy events, skip on spec placeholders, wait for the lock on a claim *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`d75191f`](https://github.com/tchlyah/dev-loop/commit/d75191f93e96d7e568cf715a9162fa1f33a8b3a6) - **board**: D2 drain race: rename board-events.jsonl to a unique temp name before reading it *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`57dfb13`](https://github.com/tchlyah/dev-loop/commit/57dfb13b16765f96b5fd468af1ab69578f6e2dd8) - **board**: D3, D8: a restarted server carries this kit's version, list the notes of the cached folders on every request *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`d0419bb`](https://github.com/tchlyah/dev-loop/commit/d0419bb17ee160e34708a6571795895f1a08d38e) - **board**: A1: ship a Track S note by its recorded head once its branch is gone *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`0f3ca2c`](https://github.com/tchlyah/dev-loop/commit/0f3ca2ceffe521442c4545f20c7e69ce2b7f47bb) - **check**: B1 to B3: board vault opt-outs, no git or fake .git skips the ignore line, prerelease version_key *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`78c6fb7`](https://github.com/tchlyah/dev-loop/commit/78c6fb72a4cd18ef1229ad49d42931931023a86f) - **board**: C1 to C4: skip a FIFO note, stop waits for a refused port, a racing run reports running, trust a pid only from the pidfile *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`e9b264c`](https://github.com/tchlyah/dev-loop/commit/e9b264c21e3965e8efd2b2bbdc4773cd01f33fb7) - **board**: Fix D1 to D3: group by owner and machine, split task segments at Stage 5 only, guard Codex upgrade dedupe *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`56bc023`](https://github.com/tchlyah/dev-loop/commit/56bc0233d0e9553cc5f21a3882cc375a2cdb6fe1) - **board**: C5: a run whose own child is not the board ends it before it returns *(commit by [@tchlyah](https://github.com/tchlyah))*



### 📝 Documentation & Content

- [`d0eeb00`](https://github.com/tchlyah/dev-loop/commit/d0eeb00684ff88d5d30c86d563c2021f30892867) - **board**: Document the board and check its Obsidian setup *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`1fa2f92`](https://github.com/tchlyah/dev-loop/commit/1fa2f9266f85d1e38ca6f391128c93091ca3162a) - **board**: Fetch after the merge on every track *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`564e641`](https://github.com/tchlyah/dev-loop/commit/564e641153049a9b11997c584ea178dc4e63b9e8) - **board**: O3: Align AGENTS.md board line and repo map with the code *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`c3eb06f`](https://github.com/tchlyah/dev-loop/commit/c3eb06fc4439f21a9854c80964f929ddc1caccc0) - **board**: F1: Document head, write-on-change, pidfile and the local.json opt-outs in AGENTS.md *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🔒 Security

- [`29db98f`](https://github.com/tchlyah/dev-loop/commit/29db98f169c5a0057a3a7f9462895c55bad6954f) - **setup**: D11: report a board note at the kit version whose body is not the kit template *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🧪 Tests & Quality

- [`023dec5`](https://github.com/tchlyah/dev-loop/commit/023dec5cf647ce80c1bd7ebd8a492b24b08bec19) - **board**: Add progress board oracle tests from the spec *(commit by [@tchlyah](https://github.com/tchlyah))*


## [2.0.0] - 2026-10-14

### ♻️ Refactors & Cleanup

- [`338a1d8`](https://github.com/tchlyah/dev-loop/commit/338a1d8eba0066fd8b870431523334c7856a1283) - **skill**: v2 skill stack: grilling, writing-plans, native or SDD, Pocock tdd and pr *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`c2277f9`](https://github.com/tchlyah/dev-loop/commit/c2277f973e357483a5dfa2259a6e5b355a23a624) - **gate**: One review wave of three reviewers, no simplify, mutation nightly *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`08b98ba`](https://github.com/tchlyah/dev-loop/commit/08b98bac76de98dddca25665d41590903c09dd7d) - **check**: Replace the dead global check with one init line *(commit by [@tchlyah](https://github.com/tchlyah))*



### ✨ New Features

- [`86b6ec6`](https://github.com/tchlyah/dev-loop/commit/86b6ec62207cb4f2162f181dc7994a7411ecd4c0) - **cli**: devloop status, logged, gate --summary and ci wait, commands map *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`8529967`](https://github.com/tchlyah/dev-loop/commit/85299673295ef634d107dd41917a96374487fa81) - **cli**: Mark a spec shipped by its shipped: frontmatter field, written at Stage 7 *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🎨 UI/UX & Accessibility

- [`f41be64`](https://github.com/tchlyah/dev-loop/commit/f41be647b8d91fb5a283084afeb6f89ad73b6ab9) - **cli**: Narrow optional values so devloop.py and its tests pass pyright basic *(commit by [@tchlyah](https://github.com/tchlyah))*



### 🐛 Bug Fixes

- [`8f88540`](https://github.com/tchlyah/dev-loop/commit/8f8854004af3be2e4af319eda026c6cd0c1487ba) - **cli**: Ship a merged feature with no branch, never read a failed git cherry as empty *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`d8413ed`](https://github.com/tchlyah/dev-loop/commit/d8413eda3970949a30a717accefee92b2c2488f7) - **skill**: Send Track S through the review wave, replace only known old SessionStart commands *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`da952df`](https://github.com/tchlyah/dev-loop/commit/da952df17c92bb3aa96e898cabb61bb649260e37) - **cli**: Fix devloop status detection, ci wait retry and spec-tester naming *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`877a787`](https://github.com/tchlyah/dev-loop/commit/877a787e778553842506978a5f43a97f98b4274f) - **hooks**: Run the mapped commands.gate in the Stop and SubagentStop gate *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`5748491`](https://github.com/tchlyah/dev-loop/commit/5748491e4514867e67090d1af3dbd0e7d7bd45ad) - **skill**: Run the Pocock desks and every reviewer in the feature worktree *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`056c336`](https://github.com/tchlyah/dev-loop/commit/056c33696b6330387a60349b54de4da69e69d123) - **skill**: Fix every defect, write the Track S review marker after the fix wave *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`aa7e62a`](https://github.com/tchlyah/dev-loop/commit/aa7e62aed4aefd15d6489d393e08ef261639d0ad) - **mutation**: Fall back to the root commit when main has no commit 24 hours old *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`294623f`](https://github.com/tchlyah/dev-loop/commit/294623f7cdb71daf20dd6d26bbd82fa6f00672ff) - **cli**: List only open specs on main: unapproved, or with a branch or worktree *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`83d2111`](https://github.com/tchlyah/dev-loop/commit/83d2111b02fdbb6f2c07c69c7b2fa7d2c6899449) - **cli**: Compare feature slugs without case: spec, branch, --feature, acceptance file *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`1bb0f30`](https://github.com/tchlyah/dev-loop/commit/1bb0f30b97c27043b4be9ff0865be4a079f345ec) - **mutation**: Take one root commit when main has several *(commit by [@tchlyah](https://github.com/tchlyah))*



### 💥 Breaking Changes

- [`497d90f`](https://github.com/tchlyah/dev-loop/commit/497d90f2f081b67d4fb9c220b4d255111821246b) - **skill**: Tracks, chained sessions, devloop status detection, no 1.4 fallbacks *(commit by [@tchlyah](https://github.com/tchlyah))*



### 📝 Documentation & Content

- [`73bb685`](https://github.com/tchlyah/dev-loop/commit/73bb6854e1037f744501162b248a6c5c4911130d) - **skill**: Ponytail rules, vendor scope paragraphs and test rules for implementers *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`359c812`](https://github.com/tchlyah/dev-loop/commit/359c8121622557394b4eec0d1c9bf98e8b002192) - **skill**: Match vendor quote, trim test rules, fix building.md headings *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`16658bb`](https://github.com/tchlyah/dev-loop/commit/16658bbe1411f0ad0350761e419f8fe8f5f5e32c) - **changelog**: Regenerate CHANGELOG.md for v2.0.0 *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`f2d76c3`](https://github.com/tchlyah/dev-loop/commit/f2d76c399e300cf7aafb6a8eace435605b8924a0) - **skill**: Describe the nightly CI workflow, the app CLI skeleton and stack_up *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`4214e1e`](https://github.com/tchlyah/dev-loop/commit/4214e1e8515006f9f0ad2822cbfac241944788b4) - **changelog**: Regenerate CHANGELOG.md for v2.0.0 after the review wave fixes *(commit by [@tchlyah](https://github.com/tchlyah))*

- [`0d663b8`](https://github.com/tchlyah/dev-loop/commit/0d663b884262c0e3505505bf9b306d1c6fc4f379) - **changelog**: Regenerate CHANGELOG.md for v2.0.0 *(PR [#10](https://github.com/tchlyah/dev-loop/pull/10) by [@tchlyah](https://github.com/tchlyah))*



### 🧪 Tests & Quality

- [`38f69c4`](https://github.com/tchlyah/dev-loop/commit/38f69c442a85d3d25cd87d11feb834dde5a3d73d) - **check**: Cover the live Codex and Claude checks on project-mode fixtures *(commit by [@tchlyah](https://github.com/tchlyah))*


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


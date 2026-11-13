// tests/unit/node/payload.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, existsSync, lstatSync, readlinkSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { packageRoot } from './helpers.mjs'
import { buildKit } from '../../../lib/kit.mjs'

const REQUIRED = [
  'skill/SKILL.md',
  'skill/references/claude.md',
  'skill/references/codex.md',
  'skill/references/mutation.md',
  'skill/references/benchmark.md',
  'skill/references/maps.md',
  'skill/scripts/check_setup.py',
  'skill/scripts/mutation/run.py',
  'skill/scripts/notes_path.py',
  'skill/scripts/devloop.py',
  'claude/agents/implementer.md',
  'claude/agents/spec-tester.md',
  'claude/hooks/gate.sh',
  'claude/hooks/deny-path.sh',
  'claude/hooks/deny-acceptance-to-subagents.sh',
  'claude/hooks/git-guard.sh',
  'codex/hooks/gate.sh',
  'codex/hooks/deny-path.sh',
  'codex/hooks/git-guard.sh',
  'codex/hooks/session-start.sh',
  'codex/rules/default.rules',
]

const REQUIRED_CODEX_AGENTS = [
  'implementer.toml',
  'spec-tester.toml',
  'task-reviewer.toml',
  'final-reviewer.toml',
  'security-reviewer.toml',
  'planner.toml',
]

test('every payload file is present', () => {
  for (const relative of REQUIRED) {
    assert.ok(existsSync(join(packageRoot, relative)), `missing ${relative}`)
  }
})

test('every Codex agent is present', () => {
  const found = readdirSync(join(packageRoot, 'codex/agents'))
  for (const name of REQUIRED_CODEX_AGENTS) {
    assert.ok(found.includes(name), `missing codex/agents/${name}`)
  }
})

test('codex default.rules ships only dev-loop git/gh/rm safety rules', () => {
  const rulesPath = join(packageRoot, 'codex/rules/default.rules')
  const text = readFileSync(rulesPath, 'utf8')

  for (const token of ['wacli', 'whatsapp', '@g.us']) {
    assert.ok(!text.toLowerCase().includes(token), `default.rules must not contain "${token}"`)
  }

  // Every rule call starts a fresh block at "prefix_rule(" or "command_rule(";
  // split on that boundary and require each block to be about git or rm, per
  // controller ruling R6a (no unrelated personal allowlist entries).
  const blocks = text
    .split(/\n(?=(?:prefix_rule|command_rule)\()/)
    .map((block) => block.trim())
    .filter((block) => block.length > 0)

  assert.equal(blocks.length, 6, 'default.rules must contain exactly six rule blocks')

  // Extract the justification string from each block
  const justifications = blocks.map((block) => {
    const match = block.match(/justification="([^"]+)"/)
    return match ? match[1] : null
  })

  // Assert the exact set of dev-loop justifications
  const expectedJustifications = [
    'Global rule: git push needs explicit permission each time.',
    'Git with a directory override needs review because later arguments can hide a push or destructive write.',
    'Hard reset can destroy uncommitted work.',
    'Recursive deletion of filesystem root is never allowed.',
    'Global rule: gh api can write refs, releases and branch protection, so it needs explicit permission each time.',
    'Global rule: gh release can create, edit or delete a release, so it needs explicit permission each time.',
  ]
  assert.deepEqual(
    new Set(justifications),
    new Set(expectedJustifications),
    'default.rules must contain exactly the six dev-loop git/gh/rm safety justifications',
  )
})

test('the gh prompt rules name their pattern and examples', () => {
  const blocks = readFileSync(join(packageRoot, 'codex/rules/default.rules'), 'utf8').split(/\n(?=prefix_rule\()/)
  for (const [word, example] of [['api', 'gh api -X POST repos/o/r/releases'], ['release', 'gh release delete v1.0.0']]) {
    const block = blocks.find((b) => b.includes(`"${word}"]`))
    assert.ok(block, word)
    assert.ok(block.includes('decision="prompt"'), word)
    assert.ok(block.includes(`pattern=[["gh", "/usr/bin/gh", "/opt/homebrew/bin/gh"], "${word}"]`), word)
    assert.ok(block.match(/\n    match=\[[^\n]*/)[0].includes(example), word)
    assert.ok(block.match(/not_match=\[[^\n]*/)[0].includes('gh pr view 7'), word)
  }
})

test('LICENSE is MIT for tayebchlyah and the kit ships the same text', () => {
  const license = readFileSync(join(packageRoot, 'LICENSE'), 'utf8')
  assert.ok(license.startsWith('MIT License\n\nCopyright (c) 2026 tayebchlyah\n\n'))
  assert.ok(license.includes('Permission is hereby granted, free of charge, to any person obtaining a copy'))
  assert.ok(license.includes('THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND'))
  assert.equal(readFileSync(join(packageRoot, 'skill/LICENSE.txt'), 'utf8'), license)
  const paths = buildKit({ packageRoot, harnesses: ['claude'] }).files.map((file) => file.path)
  assert.ok(paths.includes('.agents/skills/dev-loop/LICENSE.txt'))
  const pkg = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8'))
  assert.equal(pkg.license, 'MIT')
  assert.equal(pkg.private, true)
  assert.equal(pkg.name, 'dev-loop')
  assert.deepEqual(pkg.repository, { type: 'git', url: 'git+https://github.com/tayebchlyah/heldout-dev-loop.git' })
  assert.equal(pkg.homepage, 'https://github.com/tayebchlyah/heldout-dev-loop#readme')
  assert.deepEqual(pkg.bugs, { url: 'https://github.com/tayebchlyah/heldout-dev-loop/issues' })
})

function trackedTexts(keep = () => true) {
  const tracked = execFileSync('git', ['ls-files'], { cwd: packageRoot, encoding: 'utf8' })
    .split('\n')
    .filter((line) => line.length > 0 && keep(line))
  return tracked.map((relative) => {
    const path = join(packageRoot, relative)
    return [relative, lstatSync(path).isSymbolicLink() ? readlinkSync(path) : readFileSync(path, 'utf8')]
  })
}

test('no tracked file holds an absolute home path or a personal name', () => {
  // Built from parts so this check's own source text never contains the
  // forbidden substrings verbatim, which would otherwise make this test
  // fail against itself once it is a tracked file.
  const forbiddenPathPrefix = '/' + 'Users/'
  const forbiddenNames = ['Tay' + 'eb', 'Chl' + 'yah']
  for (const [relative, text] of trackedTexts()) {
    assert.ok(!text.includes(forbiddenPathPrefix), `absolute path in ${relative}`)
    for (const name of forbiddenNames) assert.ok(!text.includes(name), `personal name in ${relative}`)
  }
})

const OLD_OWNER = 'tch' + 'lyah'
const INSTALLED_KIT = ['.agents/', '.claude/skills/', '.claude/agents/', '.codex/', '.dev-loop/']
const outsideInstalledKit = (path) => !INSTALLED_KIT.some((prefix) => path.startsWith(prefix))
const oldUrls = (text) => [`${OLD_OWNER}/dev-loop`, `github:${OLD_OWNER}/`, `github.com/${OLD_OWNER}/`].filter((url) => text.includes(url))
const PRIVATE_TEXT = [
  new RegExp('\\b(?:repo|repository) is ' + 'private', 'i'),
  new RegExp('\\bRepo ' + 'private\\b'),
  new RegExp('Git or SSH ' + 'credentials', 'i'),
]
const privateText = (text) => PRIVATE_TEXT.map((pattern) => text.match(pattern)?.[0].toLowerCase()).filter(Boolean)

test('oldUrls finds the old owner and not the new handle', () => {
  assert.deepEqual(oldUrls('npx github:tayebchlyah/heldout-dev-loop#v1 init, https://github.com/tayebchlyah/heldout-dev-loop'), [])
  assert.equal(oldUrls(`npx github:${OLD_OWNER}/dev-loop#v1`).length, 2)
  assert.equal(oldUrls(`https://github.com/${OLD_OWNER}/x`).length, 1)
})

test('no tracked file outside the installed kit names the old repository URL', () => {
  for (const [relative, text] of trackedTexts(outsideInstalledKit)) {
    assert.deepEqual(oldUrls(text), [], `old URL in ${relative}`)
  }
})

test('no tracked file outside the installed kit has the private repository text', () => {
  assert.deepEqual(privateText('The repository is ' + 'private, so'), ['repository is ' + 'private'])
  for (const [relative, text] of trackedTexts(outsideInstalledKit)) {
    assert.deepEqual(privateText(text), [], `private text in ${relative}`)
  }
})

const words = (text) => text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []

function namesFrom(note) {
  return new Set(note.split('\n').filter((line) => line.startsWith('- ')).map((line) => words(line.slice(2)).join(' ')).filter(Boolean))
}

function readNames(notes) {
  const path = join(notes, 'Internal Names.md')
  return existsSync(path) ? namesFrom(readFileSync(path, 'utf8')) : null
}

function internalNames(text, names) {
  const all = words(text)
  const found = new Set()
  for (let i = 0; i < all.length; i++) {
    for (let n = 1; n <= 3 && i + n <= all.length; n++) {
      const name = all.slice(i, i + n).join(' ')
      if (names.has(name)) found.add(name)
    }
  }
  return [...found]
}

test('internalNames finds a listed name of one to three words in any case', () => {
  const names = namesFrom('---\ntype: note\n---\n# Internal names\n\n- Acme\n- Big-Shop Co  \n  - nested\n\n## More\n')
  assert.deepEqual([...names].sort(), ['acme', 'big shop co'])
  assert.deepEqual(internalNames('Run the ACME suite', names), ['acme'])
  assert.deepEqual(internalNames('/x/big-shop_co/y', names), ['big shop co'])
  assert.deepEqual(internalNames('acmeshop and big shop', names), [])
})

test('readNames gives null for a folder with no Internal Names.md', () => {
  assert.equal(readNames(join(packageRoot, 'tests')), null)
})

test('no tracked file names an internal client or project', (t) => {
  let notes
  try {
    notes = execFileSync('python3', ['skill/scripts/notes_path.py'], { cwd: packageRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch {
    return t.skip('no notes path: skill/scripts/notes_path.py failed')
  }
  const names = readNames(notes)
  if (!names) return t.skip('no Internal Names.md in the notes folder')
  assert.ok(names.size > 0, 'Internal Names.md lists no name')
  for (const [relative, text] of trackedTexts()) {
    assert.deepEqual(internalNames(text, names), [], `internal name in ${relative}`)
  }
})

test('dev-loop.json maps commands.acceptance to the script that runs both runners', () => {
  const marker = JSON.parse(readFileSync(join(packageRoot, 'dev-loop.json'), 'utf8'))
  assert.equal(marker.commands.acceptance, 'scripts/acceptance.sh')
  const script = readFileSync(join(packageRoot, 'scripts', 'acceptance.sh'), 'utf8')
  assert.ok(script.includes('--discover'))
  assert.ok(script.includes('node --test'))
})

test('the ruleset files hold the CI job ids and the release tags', () => {
  const read = (path) => readFileSync(join(packageRoot, path), 'utf8')
  const jobs = [...read('.github/workflows/ci.yml').split(/^jobs:$/m)[1].matchAll(/^ {2}([\w-]+):\s*$/gm)].map((m) => m[1])
  assert.deepEqual(jobs, ['changes', 'test', 'acceptance'])
  const rule = (set, type) => set.rules.find((r) => r.type === type)
  const main = JSON.parse(read('.github/rulesets/main.json'))
  assert.equal(main.target, 'branch')
  assert.equal(main.enforcement, 'active')
  assert.deepEqual(main.conditions.ref_name.include, ['~DEFAULT_BRANCH'])
  assert.deepEqual(main.rules.map((r) => r.type).sort(),
    ['deletion', 'non_fast_forward', 'pull_request', 'required_linear_history', 'required_status_checks'])
  assert.deepEqual(rule(main, 'required_status_checks').parameters.required_status_checks.map((c) => c.context).sort(), [...jobs].sort())
  assert.equal(rule(main, 'pull_request').parameters.required_approving_review_count, 0)
  assert.deepEqual(rule(main, 'pull_request').parameters.allowed_merge_methods, ['rebase'])
  assert.deepEqual(main.bypass_actors, [{ actor_id: 5, actor_type: 'RepositoryRole', bypass_mode: 'pull_request' }])
  const tags = JSON.parse(read('.github/rulesets/release-tags.json'))
  assert.equal(tags.target, 'tag')
  assert.equal(tags.enforcement, 'active')
  assert.deepEqual(tags.conditions.ref_name.include, ['refs/tags/v*'])
  assert.deepEqual(tags.rules.map((r) => r.type).sort(), ['creation', 'deletion', 'non_fast_forward', 'update'])
  assert.deepEqual(tags.bypass_actors, [{ actor_id: 5, actor_type: 'RepositoryRole', bypass_mode: 'always' }])
})

test('ci.yml has a job named acceptance that runs commands.acceptance', () => {
  const ci = readFileSync(join(packageRoot, '.github', 'workflows', 'ci.yml'), 'utf8')
  const marker = JSON.parse(readFileSync(join(packageRoot, 'dev-loop.json'), 'utf8'))
  assert.match(ci, /^ {2}acceptance:$/m)
  assert.ok(ci.includes(`run: ${marker.commands.acceptance}`))
})

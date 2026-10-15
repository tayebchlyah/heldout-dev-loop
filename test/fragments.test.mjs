// test/fragments.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const readJson = (relative) => JSON.parse(readFileSync(join(repoRoot, relative), 'utf8'))

test('the Claude fragment covers the four hook events', () => {
  const fragment = readJson('claude/settings.fragment.json')
  for (const event of ['SessionStart', 'PreToolUse', 'Stop', 'SubagentStop']) {
    assert.ok(Array.isArray(fragment.hooks[event]), `missing event ${event}`)
    assert.ok(fragment.hooks[event].length > 0, `empty event ${event}`)
  }
})

test('the Claude fragment carries the exact hook commands, matchers and timeouts', () => {
  const fragment = readJson('claude/settings.fragment.json')

  assert.deepEqual(fragment.hooks.SessionStart, [
    {
      matcher: 'startup|resume|clear|compact',
      hooks: [
        {
          type: 'command',
          command:
            'd="${CLAUDE_PROJECT_DIR:-.}"; [ -f "$d/dev-loop.json" ] && echo \'This repo runs the dev-loop skill. Invoke it before any other action.\'; exit 0',
          timeout: 5,
        },
      ],
    },
    {
      matcher: 'startup|resume|clear|compact',
      hooks: [{ type: 'command', command: '~/.claude/hooks/board.sh session-start', timeout: 5 }],
    },
  ])

  assert.deepEqual(fragment.hooks.UserPromptSubmit, [
    { hooks: [{ type: 'command', command: '~/.claude/hooks/board.sh prompt', timeout: 5 }] },
  ])

  assert.deepEqual(fragment.hooks.PreToolUse, [
    {
      matcher: 'Bash',
      hooks: [{ type: 'command', command: '~/.claude/hooks/git-guard.sh', timeout: 5 }],
    },
    {
      matcher: 'Read|Edit|Write|Grep|Glob|Bash',
      hooks: [
        {
          type: 'command',
          command: '~/.claude/hooks/deny-acceptance-to-subagents.sh',
          timeout: 5,
        },
      ],
    },
  ])

  assert.deepEqual(fragment.hooks.Stop, [
    { hooks: [{ type: 'command', command: '~/.claude/hooks/gate.sh', timeout: 600 }] },
  ])

  assert.deepEqual(fragment.hooks.SubagentStop, [
    { hooks: [{ type: 'command', command: '~/.claude/hooks/gate.sh', timeout: 600 }] },
  ])
})

test('test_project_fragment_denies_the_replaced_skills', () => {
  const fragment = readJson('claude/settings.fragment.json')
  assert.deepEqual(fragment.permissions.deny, [
    'Skill(superpowers:verification-before-completion)',
    'Skill(superpowers:test-driven-development)',
    'Skill(superpowers:systematic-debugging)',
    'Skill(superpowers:brainstorming)',
    'Skill(superpowers:finishing-a-development-branch)',
    'Read(./.env)',
    'Read(./.env.*)',
  ])
})

test('the Claude fragment references no hook that dev-loop does not own', () => {
  const fragment = readJson('claude/settings.fragment.json')
  const commands = Object.values(fragment.hooks)
    .flat()
    .flatMap((group) => group.hooks.map((hook) => hook.command))
  for (const command of commands) {
    assert.ok(!command.includes('style-routing.sh'), 'style-routing.sh is not a dev-loop hook')
    assert.ok(!command.includes('format.sh'), 'format.sh is not a dev-loop hook')
  }
})

test('the Codex fragment covers the four hook events and omits style.sh', () => {
  const fragment = readJson('codex/hooks.fragment.json')
  for (const event of ['SessionStart', 'PreToolUse', 'Stop', 'SubagentStop']) {
    assert.ok(Array.isArray(fragment.hooks[event]), `missing event ${event}`)
  }
  assert.ok(!JSON.stringify(fragment).includes('style.sh'))
})

test('the Codex fragment carries the exact hook commands, matchers and timeouts', () => {
  const fragment = readJson('codex/hooks.fragment.json')

  assert.deepEqual(fragment.hooks.SessionStart, [
    {
      matcher: 'startup|resume|clear|compact',
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/session-start.sh',
          timeout: 5,
          statusMessage: 'Checking repository workflow',
        },
      ],
    },
    {
      matcher: 'startup|resume|clear|compact',
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/board.sh session-start',
          timeout: 5,
          statusMessage: 'Updating dev-loop board',
        },
      ],
    },
  ])

  assert.deepEqual(fragment.hooks.UserPromptSubmit, [
    {
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/board.sh prompt',
          timeout: 5,
          statusMessage: 'Updating dev-loop board',
        },
      ],
    },
  ])

  assert.deepEqual(fragment.hooks.PreToolUse, [
    {
      matcher: 'Bash',
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/git-guard.sh',
          timeout: 5,
          statusMessage: 'Checking command safety',
        },
      ],
    },
  ])

  assert.deepEqual(fragment.hooks.Stop, [
    {
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/gate.sh',
          timeout: 600,
          statusMessage: 'Running repository gate',
        },
      ],
    },
  ])

  assert.deepEqual(fragment.hooks.SubagentStop, [
    {
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/gate.sh',
          timeout: 600,
          statusMessage: 'Running repository gate',
        },
      ],
    },
  ])
})

test('the Codex TOML fragment puts its root keys before the first table', () => {
  const text = readFileSync(join(repoRoot, 'codex/config.permissions.toml'), 'utf8')
  const lines = text.split('\n')
  const firstTable = lines.findIndex((line) => line.trimStart().startsWith('['))
  assert.ok(firstTable > 0, 'the fragment needs at least one root key and one table')
  const root = lines.slice(0, firstTable).join('\n')
  assert.ok(root.includes('default_permissions = "dev-workspace"'))
  assert.ok(text.includes('[permissions.dev-workspace]'))
  assert.ok(text.includes('[permissions.dev-implementer]'))
  assert.ok(text.includes('[permissions.dev-spec-tester]'))
  assert.ok(text.includes('[features]'))
})

test('the Codex TOML fragment denies the held-out suite for dev-implementer and sources for dev-spec-tester', () => {
  const text = readFileSync(join(repoRoot, 'codex/config.permissions.toml'), 'utf8')
  const lines = text.split('\n')

  const sectionBody = (header) => {
    const start = lines.findIndex((line) => line.trim() === header)
    assert.ok(start >= 0, `missing section ${header}`)
    const rest = lines.slice(start + 1)
    let end = rest.findIndex((line) => line.trimStart().startsWith('['))
    if (end === -1) end = rest.length
    return rest.slice(0, end).join('\n')
  }

  const implementerRoots = sectionBody(
    '[permissions.dev-implementer.filesystem.":workspace_roots"]',
  )
  assert.match(implementerRoots, /"tests\/acceptance"\s*=\s*"deny"/)
  assert.match(implementerRoots, /"tests\/acceptance\/\*\*"\s*=\s*"deny"/)

  const specTesterRoots = sectionBody(
    '[permissions.dev-spec-tester.filesystem.":workspace_roots"]',
  )
  for (const dir of ['src', 'app', 'backend', 'frontend']) {
    assert.match(specTesterRoots, new RegExp(`"${dir}"\\s*=\\s*"deny"`))
    assert.match(specTesterRoots, new RegExp(`"${dir}/\\*\\*"\\s*=\\s*"deny"`))
  }
})

test('the Claude SessionStart command detects dev-loop.json only', () => {
  const command = readJson('claude/settings.fragment.json').hooks.SessionStart[0].hooks[0].command
  const run = (files) => {
    const dir = mkdtempSync(join(tmpdir(), 'dev-loop-session-'))
    try {
      for (const [name, body] of Object.entries(files)) writeFileSync(join(dir, name), body)
      return execFileSync('sh', ['-c', command], {
        env: { ...process.env, CLAUDE_PROJECT_DIR: dir },
        encoding: 'utf8',
      })
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  }
  assert.match(run({ 'dev-loop.json': '{}\n' }), /dev-loop skill/)
  assert.equal(run({ 'AGENTS.md': 'vault_notes: Software/Test.md\n' }), '')
  assert.equal(run({}), '')
})

// tests/unit/node/fragments.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
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
      hooks: [
        { type: 'command', command: '~/.claude/hooks/git-guard.sh', timeout: 5, onFailure: 'block' },
      ],
    },
    {
      matcher: 'Read|Edit|Write|NotebookEdit|Grep|Glob|Bash',
      hooks: [
        {
          type: 'command',
          command: '~/.claude/hooks/deny-acceptance-to-subagents.sh',
          timeout: 5,
          onFailure: 'block',
        },
      ],
    },
    {
      matcher: 'AskUserQuestion',
      hooks: [{ type: 'command', command: '~/.claude/hooks/deny-ask.sh', timeout: 5 }],
    },
  ])

  assert.deepEqual(fragment.hooks.StopFailure, [
    {
      matcher: 'rate_limit',
      hooks: [{ type: 'command', command: '~/.claude/hooks/pause.sh', timeout: 30 }],
    },
  ])

  assert.deepEqual(fragment.hooks.PostToolUse, [
    {
      matcher: 'Bash',
      hooks: [{ type: 'command', command: '~/.claude/hooks/board.sh post-tool', timeout: 5 }],
    },
    {
      matcher: 'TaskCreate|TaskUpdate',
      hooks: [{ type: 'command', command: '~/.claude/hooks/board.sh post-tool', timeout: 5 }],
    },
  ])

  assert.deepEqual(fragment.hooks.Stop, [
    { hooks: [{ type: 'command', command: '~/.claude/hooks/gate.sh', timeout: 600 }] },
    { hooks: [{ type: 'command', command: '~/.claude/hooks/seal.sh turn', timeout: 120 }] },
  ])

  assert.deepEqual(fragment.hooks.SubagentStop, [
    { hooks: [{ type: 'command', command: '~/.claude/hooks/gate.sh', timeout: 600 }] },
    { hooks: [{ type: 'command', command: '~/.claude/hooks/seal.sh stop', timeout: 120 }] },
  ])
})

test('the deny-path hooks of both Claude agent frontmatters block when they fail', () => {
  for (const agent of ['implementer', 'spec-tester']) {
    const text = readFileSync(join(repoRoot, 'claude', 'agents', `${agent}.md`), 'utf8')
    const frontmatter = text.split('---\n')[1]
    assert.match(frontmatter, /command: "~\/\.claude\/hooks\/deny-path\.sh [^"\n]+"\n\s+onFailure: block\n/, agent)
  }
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

test('the Codex fragment covers the five hook events and omits style.sh', () => {
  const fragment = readJson('codex/hooks.fragment.json')
  for (const event of ['SessionStart', 'PreToolUse', 'Stop', 'SubagentStart', 'SubagentStop']) {
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
    {
      matcher: 'Bash|apply_patch',
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/acceptance-wall.sh',
          timeout: 5,
          statusMessage: 'Checking held-out suite access',
        },
      ],
    },
  ])

  assert.deepEqual(fragment.hooks.PostToolUse, [
    {
      matcher: 'Bash',
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/board.sh post-tool',
          timeout: 5,
          statusMessage: 'Updating dev-loop board',
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
    {
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/seal.sh turn',
          timeout: 120,
          statusMessage: 'Checking held-out seal',
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
    {
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/seal.sh stop',
          timeout: 120,
          statusMessage: 'Checking held-out seal',
        },
      ],
    },
  ])

  assert.deepEqual(fragment.hooks.SubagentStart, [
    {
      hooks: [
        {
          type: 'command',
          command: '{{HOME}}/.codex/hooks/seal.sh start',
          timeout: 5,
          statusMessage: 'Recording subagent start',
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
  assert.ok(text.includes('[features]'))
})

test('the Codex TOML fragment has none of the per-agent profiles Codex never applied', () => {
  const text = readFileSync(join(repoRoot, 'codex/config.permissions.toml'), 'utf8')
  for (const name of ['dev-implementer', 'dev-spec-tester', 'dev-read-only']) assert.ok(!text.includes(name), name)
})

test('no Codex agent file sets default_permissions', () => {
  for (const name of readdirSync(join(repoRoot, 'codex/agents'))) {
    assert.doesNotMatch(readFileSync(join(repoRoot, 'codex/agents', name), 'utf8'), /^\s*default_permissions\s*=/m, name)
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

import { isDeepStrictEqual } from 'node:util'

function cloneGroup(group) {
  return { ...group, hooks: group.hooks.map((hook) => ({ ...hook })) }
}

// The SessionStart commands earlier dev-loop versions wrote. A merge replaces
// one of them in place, and uninstall-global removes them.
export const LEGACY_SESSION_START = [
  // 1.4
  'd="${CLAUDE_PROJECT_DIR:-.}"; [ -f "$d/AGENTS.md" ] && grep -q \'^vault_notes:\' "$d/AGENTS.md" && echo \'This repo runs the dev-loop skill. Invoke it before any other action.\'; exit 0',
  // 1.5
  'd="${CLAUDE_PROJECT_DIR:-.}"; { [ -f "$d/dev-loop.json" ] || grep -qs \'^vault_notes:\' "$d/AGENTS.md"; } && echo \'This repo runs the dev-loop skill. Invoke it before any other action.\'; exit 0',
]

// A kit hook runs a script the installer put under .dev-loop/hooks/<harness>/.
const isOwnHook = (hook) => /\.dev-loop\/hooks\/(claude|codex)\//.test(hook?.command ?? '')

function hasHooks(group) {
  return group !== null && typeof group === 'object' && Array.isArray(group.hooks)
}

// A user's settings.json is hand-edited, so it can hold shapes this merge
// cannot read: an event whose value is not a list of groups, or a group
// with no hooks array. Neither is worth aborting an install for. Both are
// left exactly as they are and named in `notes`, so the user hears why
// dev-loop's own entry did not land there.
export function mergeHooks(current, fragment) {
  const value = { ...current }
  const added = []
  const removed = []
  const changed = []
  const notes = []

  for (const [event, groups] of Object.entries(fragment)) {
    const existing = value[event] ?? []
    if (!Array.isArray(existing)) {
      notes.push(`left the "${event}" value alone: it is not a list of hook groups`)
      continue
    }
    const target = existing.map((group) => (hasHooks(group) ? cloneGroup(group) : group))
    if (existing.some((group) => !hasHooks(group))) {
      notes.push(`skipped a hook group under "${event}" in your own file: it has no "hooks" array`)
    }
    // A dev-loop hook an older install wrote is replaced, never kept beside
    // the new one: the old entry goes, the normal merge below places the new.
    // A command can sit in several fragment groups (one per matcher), so an
    // existing copy stays only when it equals one unclaimed fragment pair.
    const replaced = new Set()
    const wanted = groups.filter(hasHooks).flatMap((group) => group.hooks.filter(isOwnHook).map((hook) => ({ matcher: group.matcher, hook })))
    for (const candidate of target.filter(hasHooks)) {
      candidate.hooks = candidate.hooks.filter((existingHook) => {
        if (!isOwnHook(existingHook) || !wanted.some(({ hook }) => hook.command === existingHook.command)) return true
        const pair = wanted.findIndex(({ matcher, hook }) => matcher === candidate.matcher && isDeepStrictEqual(hook, existingHook))
        if (pair !== -1) return wanted.splice(pair, 1) && true
        replaced.add(existingHook.command)
        return false
      })
    }
    for (let i = target.length - 1; i >= 0; i--) if (hasHooks(target[i]) && target[i].hooks.length === 0) target.splice(i, 1)
    for (const group of groups) {
      if (!hasHooks(group)) {
        notes.push(`skipped a hook group under "${event}": it has no "hooks" array`)
        continue
      }
      const match = target.find((candidate) => hasHooks(candidate) && candidate.matcher === group.matcher)
      if (!match) {
        target.push(cloneGroup(group))
        for (const hook of group.hooks) (replaced.has(hook.command) ? changed : added).push(`${event}: ${hook.command}`)
        continue
      }
      for (const hook of group.hooks) {
        if (match.hooks.some((existingHook) => existingHook.command === hook.command)) continue
        // An old dev-loop SessionStart entry is replaced in place, never kept
        // beside the new one.
        const old = match.hooks.findIndex((existingHook) => LEGACY_SESSION_START.includes(existingHook?.command))
        if (old === -1) match.hooks.push({ ...hook })
        else {
          removed.push(`${event}: ${match.hooks[old].command}`)
          match.hooks[old] = { ...hook }
        }
        ;(replaced.has(hook.command) ? changed : added).push(`${event}: ${hook.command}`)
      }
    }
    value[event] = target
  }
  return { value, added, removed, changed, notes }
}

export function mergeDeny(current, rules) {
  const deny = [...(current?.deny ?? [])]
  const added = []
  for (const rule of rules) {
    if (deny.includes(rule)) continue
    deny.push(rule)
    added.push(rule)
  }
  return { value: { ...current, deny }, added }
}

export function mergeSettings(currentText, fragment) {
  const current = currentText === null ? {} : JSON.parse(currentText)
  const hooks = mergeHooks(current.hooks ?? {}, fragment.hooks ?? {})
  const permissions = mergeDeny(current.permissions ?? {}, fragment.permissions?.deny ?? [])
  const value = { ...current, hooks: hooks.value, permissions: permissions.value }
  // env: add a missing variable, never change one the user set.
  const envAdded = Object.entries(fragment.env ?? {}).filter(([name]) => !(name in (current.env ?? {})))
  if (envAdded.length > 0) value.env = { ...current.env, ...Object.fromEntries(envAdded) }
  return {
    text: JSON.stringify(value, null, 2) + '\n',
    added: [...hooks.added, ...permissions.added, ...envAdded.map(([name, setting]) => `env ${name}=${setting}`)],
    removed: hooks.removed,
    updated: hooks.changed,
    notes: hooks.notes,
  }
}

// ~/.codex/hooks.json carries the same hook shape as the Claude settings
// file, but at the top level and with no permissions, so it merges here
// beside mergeSettings rather than in the planning module.
export function mergeHooksFile(currentText, fragmentText) {
  const current = currentText === null ? {} : JSON.parse(currentText)
  const fragment = JSON.parse(fragmentText)
  const merged = mergeHooks(current.hooks ?? {}, fragment.hooks ?? {})
  const value = { ...current, hooks: merged.value }
  return { text: JSON.stringify(value, null, 2) + '\n', added: merged.added, removed: merged.removed, updated: merged.changed, notes: merged.notes }
}

// The reverse of a merge: drop every hook whose command is in `commands`,
// then any group left with no hook and any event left with no group. The
// Claude settings file and the Codex hooks file both keep their hooks under
// a top-level "hooks" key, so one function serves both. A shape this code
// cannot read is left as it is.
export function removeHookCommands(currentText, commands) {
  const current = JSON.parse(currentText)
  const hooks = { ...(current.hooks ?? {}) }
  const removed = []
  for (const [event, groups] of Object.entries(hooks)) {
    if (!Array.isArray(groups)) continue
    const kept = []
    for (const group of groups) {
      if (!hasHooks(group)) {
        kept.push(group)
        continue
      }
      const left = group.hooks.filter((hook) => !commands.includes(hook?.command))
      for (const hook of group.hooks) if (!left.includes(hook)) removed.push(`${event}: ${hook.command}`)
      if (left.length > 0) kept.push({ ...group, hooks: left })
    }
    if (kept.length > 0 || groups.length === 0) hooks[event] = kept
    else delete hooks[event]
  }
  return { text: JSON.stringify({ ...current, hooks }, null, 2) + '\n', removed }
}

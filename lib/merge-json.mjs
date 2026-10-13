function cloneGroup(group) {
  return { ...group, hooks: group.hooks.map((hook) => ({ ...hook })) }
}

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
    for (const group of groups) {
      if (!hasHooks(group)) {
        notes.push(`skipped a hook group under "${event}": it has no "hooks" array`)
        continue
      }
      const match = target.find((candidate) => hasHooks(candidate) && candidate.matcher === group.matcher)
      if (!match) {
        target.push(cloneGroup(group))
        for (const hook of group.hooks) added.push(`${event}: ${hook.command}`)
        continue
      }
      for (const hook of group.hooks) {
        if (match.hooks.some((existingHook) => existingHook.command === hook.command)) continue
        match.hooks.push({ ...hook })
        added.push(`${event}: ${hook.command}`)
      }
    }
    value[event] = target
  }
  return { value, added, notes }
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
  return {
    text: JSON.stringify(value, null, 2) + '\n',
    added: [...hooks.added, ...permissions.added],
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
  return { text: JSON.stringify(value, null, 2) + '\n', added: merged.added, notes: merged.notes }
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

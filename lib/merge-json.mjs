function cloneGroup(group) {
  return { ...group, hooks: group.hooks.map((hook) => ({ ...hook })) }
}

export function mergeHooks(current, fragment) {
  const value = { ...current }
  const added = []

  for (const [event, groups] of Object.entries(fragment)) {
    const target = (value[event] ?? []).map(cloneGroup)
    for (const group of groups) {
      const match = target.find((candidate) => candidate.matcher === group.matcher)
      if (!match) {
        target.push(cloneGroup(group))
        for (const hook of group.hooks) added.push(`${event}: ${hook.command}`)
        continue
      }
      for (const hook of group.hooks) {
        if (match.hooks.some((existing) => existing.command === hook.command)) continue
        match.hooks.push({ ...hook })
        added.push(`${event}: ${hook.command}`)
      }
    }
    value[event] = target
  }
  return { value, added }
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
  return { text: JSON.stringify(value, null, 2) + '\n', added: [...hooks.added, ...permissions.added] }
}

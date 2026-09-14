const MARKER = '[permissions.dev-workspace]'

export function splitToml(text) {
  const lines = text.split('\n')
  const index = lines.findIndex((line) => line.trimStart().startsWith('['))
  if (index === -1) return { root: lines, tables: [] }
  return { root: lines.slice(0, index), tables: lines.slice(index) }
}

// A substring check on the raw text would also fire on a commented-out
// mention ("# see [permissions.dev-workspace] docs") or on the marker
// spelled out inside a string value, and wrongly report the profile as
// already merged. Only a line that IS the table header, once surrounding
// whitespace is stripped, counts.
function hasMarkerHeader(text) {
  return text.split('\n').some((line) => line.trim() === MARKER)
}

export function mergeCodexConfig(currentText, fragmentText) {
  if (currentText !== null && hasMarkerHeader(currentText)) {
    return { text: currentText, changed: false }
  }
  if (currentText === null) {
    return { text: fragmentText.endsWith('\n') ? fragmentText : `${fragmentText}\n`, changed: true }
  }

  const current = splitToml(currentText)
  const fragment = splitToml(fragmentText)

  const existing = new Set(current.root.map((line) => line.trim()))
  const rootToAdd = fragment.root.filter((line) => line.trim() !== '' && !existing.has(line.trim()))

  const lines = [
    ...trimTrailingBlank(current.root),
    ...rootToAdd,
    '',
    ...trimTrailingBlank(current.tables),
    '',
    ...trimTrailingBlank(fragment.tables),
    '',
  ]
  return { text: lines.join('\n'), changed: true }
}

function trimTrailingBlank(lines) {
  const copy = [...lines]
  while (copy.length > 0 && copy[copy.length - 1].trim() === '') copy.pop()
  return copy
}

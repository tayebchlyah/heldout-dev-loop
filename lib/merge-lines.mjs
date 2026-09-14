const BLOCK_START = /^(prefix_rule|command_rule)\(/
const JUSTIFICATION = /justification="([^"]*)"/

function parseBlocks(text) {
  const lines = text.split('\n')
  const starts = []
  lines.forEach((line, index) => {
    if (BLOCK_START.test(line)) starts.push(index)
  })

  return starts.map((start, index) => {
    const end = index + 1 < starts.length ? starts[index + 1] : lines.length
    const blockLines = lines.slice(start, end)
    // A trailing newline in the source turns into a trailing '' entry once
    // split on '\n'; only the very last block can carry one, since interior
    // blocks are cut exactly at the next block's start line.
    while (blockLines.length > 0 && blockLines[blockLines.length - 1] === '') blockLines.pop()
    const blockText = blockLines.join('\n')
    const match = blockText.match(JUSTIFICATION)
    return { text: blockText, justification: match ? match[1] : null }
  })
}

export function mergeRules(currentText, fragmentText) {
  const fragmentBlocks = parseBlocks(fragmentText)

  if (currentText === null) {
    return { text: fragmentText, added: fragmentBlocks.map((block) => block.justification) }
  }

  const present = new Set(parseBlocks(currentText).map((block) => block.justification))
  const missing = fragmentBlocks.filter((block) => !present.has(block.justification))

  if (missing.length === 0) return { text: currentText, added: [] }

  const prefix = currentText.endsWith('\n') ? currentText : `${currentText}\n`
  const appended = missing.map((block) => block.text).join('\n')
  return { text: `${prefix}${appended}\n`, added: missing.map((block) => block.justification) }
}

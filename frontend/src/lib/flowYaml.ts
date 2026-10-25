// Reads the shared vector files (quickadd-cases.yaml, layout-cases.yaml). They are
// YAML written in one restricted shape so they can be read without a YAML library, in vitest and JUnit
// alike: `key: <json>` lines at the top level, and `  - <json>` items under `cases:`. Lines starting
// with `#` are comments. Android's twin is FlowYaml in the JUnit tests.

import { t as tr } from '../i18n'

export function readFlowYaml(text: string): Record<string, unknown> & { cases: Array<Record<string, unknown>> } {
  const out: Record<string, unknown> = {}
  const cases: Array<Record<string, unknown>> = []
  let inCases = false
  text.split('\n').forEach((line, i) => {
    const t = line.trim()
    if (!t || t.startsWith('#')) return
    if (t === 'cases:') {
      inCases = true
      return
    }
    if (inCases && t.startsWith('- ')) {
      cases.push(parseAt(t.slice(2), i))
      return
    }
    const m = /^([A-Za-z_][\w-]*):\s+(.+)$/.exec(line)
    if (!m || line.startsWith(' ')) throw new Error(tr('lib.flowYaml.lineNotAKeyOr', { value: i + 1 }))
    out[m[1]] = parseAt(m[2], i)
    inCases = false
  })
  return { ...out, cases }
}

function parseAt(json: string, i: number) {
  try {
    return JSON.parse(json)
  } catch (e) {
    throw new Error(tr('lib.flowYaml.line', { value: i + 1, message: (e as Error).message }), { cause: e })
  }
}

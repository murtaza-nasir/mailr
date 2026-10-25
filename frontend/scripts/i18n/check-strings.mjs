// Fails when a TSX or TS file in src/ shows a person a string that is not in src/locales/en.json.
// Run by `npm run lint`. The scanner is a heuristic (scan.mjs): text between
// JSX tags, text attributes (aria-label, title, placeholder…), and string literals that read like
// prose. A string that is really an ID goes in exemptions.json with the reason; a line can also
// say `// i18n-ignore` (or `{/* i18n-ignore */}` in JSX).
//
//   node scripts/i18n/check-strings.mjs           list what is left and exit 1 if anything is
//   node scripts/i18n/check-strings.mjs --count   also print the number of exempted strings

import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { scanSource } from './scan.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const exemptions = JSON.parse(readFileSync(path.join(ROOT, 'scripts/i18n/exemptions.json'), 'utf8'))
const files = execSync(`git ls-files --cached --others --exclude-standard 'src/*.ts' 'src/*.tsx' 'src/**/*.ts' 'src/**/*.tsx'`, { cwd: ROOT })
  .toString().split('\n').map((f) => f.trim())
  .filter((f) => f && !/\.test\.tsx?$/.test(f) && !f.endsWith('.d.ts') && !f.startsWith('src/i18n/'))

const bad = []
let exempted = 0
const usedExemptions = new Set()
for (const f of files) {
  let src
  try {
    src = readFileSync(path.join(ROOT, f), 'utf8')
  } catch {
    continue
  }
  const allowed = new Set(exemptions[f]?.texts ?? [])
  for (const c of scanSource(f, src)) {
    if (allowed.has(c.text)) {
      exempted++
      usedExemptions.add(`${f}\u0000${c.text}`)
      continue
    }
    bad.push(`${f}:${c.line}  ${JSON.stringify(c.text.length > 90 ? c.text.slice(0, 90) + '…' : c.text)}`)
  }
}
const stale = []
for (const [f, e] of Object.entries(exemptions)) {
  if (f.startsWith('_')) continue
  for (const tx of e.texts ?? []) if (!usedExemptions.has(`${f}\u0000${tx}`)) stale.push(`${f}: ${JSON.stringify(tx)}`)
}
if (process.argv.includes('--count')) console.log(`exempted strings: ${exempted} (${Object.keys(exemptions).filter((k) => !k.startsWith('_')).length} files)`)
if (stale.length) console.error(`exemptions.json lists strings that are no longer found; remove them:\n  ${stale.join('\n  ')}`)
if (bad.length) {
  console.error(`${bad.length} hard-coded user-facing string(s). Move each to src/locales/en.json and use t('key'):\n  ${bad.join('\n  ')}`)
}
process.exit(bad.length || stale.length ? 1 : 0)

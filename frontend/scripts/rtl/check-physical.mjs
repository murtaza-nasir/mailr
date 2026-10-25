#!/usr/bin/env node
// The right-to-left check: fails on a new physical left or
// right in the web app's styles, so the layout keeps mirroring in Arabic, Hebrew, Persian and Urdu.
//
// CSS (src/**/*.css):
//   left / right positions, margin-, padding-, border-left/right, border-*-left/right-radius,
//   float, clear and text-align left/right, translateX() without var(--dir), a four-value margin,
//   padding or inset whose right and left differ, a border-radius whose corners differ left to right.
// TSX / TS (src/**/*.tsx, src/**/*.ts):
//   marginLeft, paddingRight, borderLeft… and the corner radii, textAlign 'left' / 'right', and
//   left: / right: in a style={{ }} on the same line.
//
// Use the logical form instead (inset-inline-start, margin-inline-end, border-start-end-radius,
// text-align: start, translateX(calc(12px * var(--dir))), insetInlineStart …). Where a physical side
// is meant (a box centred with left: 50% and translateX(-50%), a tick mark, coordinates measured on
// the screen), say why on the line: `/* rtl: reason */` in CSS, `// rtl-ok: reason` in TS (or on
// the line above), or list the file and text in scripts/rtl/exemptions.json with the reason.
//
//   node scripts/rtl/check-physical.mjs           fails with the list
//   node scripts/rtl/check-physical.mjs --count   prints the number of exempted lines
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '../..')
const src = path.join(root, 'src')
const exemptions = JSON.parse(fs.readFileSync(path.join(here, 'exemptions.json'), 'utf8'))

function walk(d, out = []) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f)
    if (fs.statSync(p).isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}

function splitVals(v) {
  const out = []
  let depth = 0
  let cur = ''
  for (const ch of v.trim()) {
    if (ch === '(') depth++
    if (ch === ')') depth--
    if (/\s/.test(ch) && depth === 0) {
      if (cur) out.push(cur)
      cur = ''
    } else cur += ch
  }
  if (cur) out.push(cur)
  return out
}

/** What is physical in one CSS declaration, or null. */
export function cssProblem(prop, value) {
  const p = prop.toLowerCase()
  const v = value.replace(/\s*!important\s*$/, '').trim()
  if (p === 'left' || p === 'right') return `${p}: use inset-inline-${p === 'left' ? 'start' : 'end'}`
  if (/^(margin|padding|border|scroll-margin|scroll-padding)-(left|right)(-|$)/.test(p)) return `${p}: use the -inline-start / -inline-end form`
  if (/^border-(top|bottom)-(left|right)-radius$/.test(p)) return `${p}: use border-start-start-radius and the like`
  if ((p === 'float' || p === 'clear') && /^(left|right)$/.test(v)) return `${p}: ${v}: use inline-start / inline-end`
  if (p === 'text-align' && /^(left|right)$/.test(v)) return `text-align: ${v}: use start / end`
  if (/^(transition|transition-property)$/.test(p) && /(^|[\s,])(left|right)(?=[\s,]|$)/.test(v)) return `${p} names left/right`
  if (/translateX\(/.test(v) && !/var\(--dir\)/.test(v)) return 'translateX(): multiply by var(--dir)'
  if (/^(margin|padding|inset|scroll-margin|scroll-padding)$/.test(p)) {
    const vs = splitVals(v)
    if (vs.length === 4 && vs[1] !== vs[3]) return `${p} with four values: use ${p}-block and ${p}-inline`
  }
  if (p === 'border-radius' && !v.includes('/')) {
    const vs = splitVals(v)
    if (vs.length >= 2) {
      const [tl, tr, br, bl] = vs.length === 2 ? [vs[0], vs[1], vs[0], vs[1]] : vs.length === 3 ? [vs[0], vs[1], vs[2], vs[1]] : vs
      if (tl !== tr || bl !== br) return 'border-radius corners differ left to right: use border-start-start-radius and the like'
    }
  }
  return null
}

const TS_PROP = /\b(marginLeft|marginRight|paddingLeft|paddingRight|borderLeft\w*|borderRight\w*|borderTopLeftRadius|borderTopRightRadius|borderBottomLeftRadius|borderBottomRightRadius)\s*:/
const TS_ALIGN = /\btextAlign\s*:\s*['"](left|right)['"]/
const TS_POS = /style=\{\{[^}]*\b(left|right)\s*:/

function exempt(file, line) {
  const rel = path.relative(root, file)
  return exemptions.some((e) => e.file === rel && (!e.match || line.includes(e.match)))
}

const problems = []
let exempted = 0
for (const file of walk(src)) {
  if (/\.(test|spec)\.tsx?$/.test(file)) continue
  const text = fs.readFileSync(file, 'utf8')
  const lines = text.split('\n')
  if (file.endsWith('.css')) {
    // Each declaration with the line it starts on; comments are blanked (keeping line breaks).
    const bare = text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '))
    const re = /(^|[;{])\s*([a-zA-Z-]+)\s*:\s*([^;{}]*)/g
    for (let m = re.exec(bare); m; m = re.exec(bare)) {
      const prop = m[2]
      // A selector such as `a:hover {` is not a declaration: what follows the value is `{`.
      if (bare[m.index + m[0].length] === '{') continue
      const why = cssProblem(prop, m[3])
      if (!why) continue
      const ln = bare.slice(0, m.index + m[0].indexOf(prop)).split('\n').length
      const line = lines[ln - 1]
      if (/rtl:/.test(line) || exempt(file, line)) {
        exempted++
        continue
      }
      problems.push(`${path.relative(root, file)}:${ln}: ${why}\n    ${line.trim()}`)
    }
  } else if (/\.tsx?$/.test(file)) {
    lines.forEach((line, i) => {
      if (/^\s*(\/\/|\*|\/\*)/.test(line)) return
      const why = TS_PROP.test(line) ? `${line.match(TS_PROP)[1]}: use the logical form (marginInlineStart, borderInlineEnd …)`
        : TS_ALIGN.test(line) ? "textAlign 'left' / 'right': use 'start' / 'end'"
          : TS_POS.test(line) ? 'left / right in a style: use insetInlineStart / insetInlineEnd' : null
      if (!why) return
      if (/rtl-ok:/.test(line) || /rtl-ok:/.test(lines[i - 1] ?? '') || exempt(file, line)) {
        exempted++
        return
      }
      problems.push(`${path.relative(root, file)}:${i + 1}: ${why}\n    ${line.trim().slice(0, 160)}`)
    })
  }
}

if (process.argv.includes('--count')) {
  console.log(`right-to-left check: ${exempted} physical sides kept on purpose, ${problems.length} to fix`)
}
if (problems.length) {
  console.error(`Physical left/right in the web app's styles:\n\n${problems.join('\n')}\n\n` +
    'Use the logical form, or say why the side is meant: /* rtl: reason */ in CSS, // rtl-ok: reason in TS, or scripts/rtl/exemptions.json.')
  process.exit(1)
}

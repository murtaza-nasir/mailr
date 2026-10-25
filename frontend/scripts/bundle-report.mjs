// Bundle sizes of a production build (dist/): what the first page load fetches (the entry
// script, its static imports and the stylesheets index.html links) and everything else.
//   node scripts/bundle-report.mjs [--json]
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

const dist = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../dist')
const html = readFileSync(path.join(dist, 'index.html'), 'utf8')
const size = (f) => {
  const b = readFileSync(path.join(dist, f))
  return { raw: b.length, gzip: gzipSync(b, { level: 9 }).length }
}
const entry = [...html.matchAll(/<script[^>]+type="module"[^>]+src="\/([^"]+)"/g)].map((m) => m[1])
// With other languages the page starts through the locale boot script (vite.config.ts i18nKeys),
// which adds the entry's script tag itself: follow it.
const boot = [...html.matchAll(/<script src="\/(assets\/boot-[^"]+\.js)"/g)].map((m) => m[1])
for (const b of boot) {
  const m = /s\.src="\/([^"]+\.js)"/.exec(readFileSync(path.join(dist, b), 'utf8'))
  if (m) entry.push(m[1])
}
const css = [...html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="\/([^"]+)"/g)].map((m) => m[1])
// Static imports only (`import … from "./x.js"`), not import("./x.js").
const initial = new Set()
const walk = (f) => {
  if (initial.has(f)) return
  initial.add(f)
  const src = readFileSync(path.join(dist, f), 'utf8')
  for (const m of src.matchAll(/(?:^|[;\n}])\s*import\s*(?:[^'"()]*?from\s*)?["']\.\/([^"']+\.js)["']/g)) walk(path.join(path.dirname(f), m[1]))
}
entry.forEach(walk)
boot.forEach((b) => initial.add(b))
const all = readdirSync(path.join(dist, 'assets')).map((f) => `assets/${f}`)
const sum = (files) => files.reduce((a, f) => { const s = size(f); return { raw: a.raw + s.raw, gzip: a.gzip + s.gzip } }, { raw: 0, gzip: 0 })
const kb = (n) => `${(n / 1024).toFixed(1)} kB`
const report = {
  initialJs: sum([...initial]), initialCss: sum(css),
  allJs: sum(all.filter((f) => f.endsWith('.js'))), allCss: sum(all.filter((f) => f.endsWith('.css'))),
  initialFiles: [...initial].map((f) => ({ file: f, ...size(f) })).sort((a, b) => b.raw - a.raw),
}
if (process.argv.includes('--json')) console.log(JSON.stringify(report, null, 2))
else {
  console.log(`first load JS : ${kb(report.initialJs.raw)} (gzip ${kb(report.initialJs.gzip)}) in ${initial.size} files`)
  console.log(`first load CSS: ${kb(report.initialCss.raw)} (gzip ${kb(report.initialCss.gzip)})`)
  console.log(`all JS        : ${kb(report.allJs.raw)} (gzip ${kb(report.allJs.gzip)})`)
  console.log(`all CSS       : ${kb(report.allCss.raw)} (gzip ${kb(report.allCss.gzip)})`)
  for (const f of report.initialFiles) console.log(`  ${f.file.padEnd(40)} ${kb(f.raw).padStart(10)} ${kb(f.gzip).padStart(10)}`)
}

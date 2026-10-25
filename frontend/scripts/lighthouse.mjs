// Lighthouse on the production build: the PWA category (installable manifest, service worker,
// splash screen, themed address bar, viewport, maskable icon) and Accessibility, on the sign-in
// page and, signed in as a fresh e2e user, the inbox, a conversation, Calendar, Tasks and
// Settings, with Lighthouse's phone emulation. Fails (exit 1) under the thresholds.
//
//   npm run build && npx vite preview --port 4173 &
//   node scripts/lighthouse.mjs [base-url]                    # default http://localhost:4173
//
// Lighthouse 11 is the last release with the PWA category; it runs through npx, so it is not a
// dependency. CHROME_PATH picks the browser (the newest Playwright Chromium when unset).
// Reports go to lighthouse-report/ (JSON per page and summary.json).
import { execFileSync, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const base = (process.argv[2] ?? process.env.LH_BASE ?? 'http://localhost:4173').replace(/\/$/, '')
const out = path.resolve(here, '../lighthouse-report')
const MIN = { pwa: 0.9, accessibility: 0.95 }
mkdirSync(out, { recursive: true })

function chrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH
  const root = path.join(os.homedir(), '.cache/ms-playwright')
  const found = existsSync(root) ? readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)).sort((a, b) => Number(a.slice(9)) - Number(b.slice(9))) : []
  for (const d of found.reverse()) {
    const p = path.join(root, d, 'chrome-linux64/chrome')
    if (existsSync(p)) return p
  }
  return undefined
}

/** A fresh user (backend: mailr e2e-seed) and its session cookie. */
async function session() {
  const backend = path.resolve(here, '../../backend')
  const seed = JSON.parse(execFileSync('uv', ['run', 'mailr', 'e2e-seed'], { cwd: backend, env: { ...process.env, MAILR_PUBLIC_IDS: process.env.MAILR_PUBLIC_IDS ?? 'true' }, encoding: 'utf8' }).trim().split('\n').pop())
  const r = await fetch(`${base}/api/v1/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: seed.email, password: seed.password }) })
  if (!r.ok) throw new Error(`login failed: ${r.status}`)
  const cookie = r.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ')
  const list = await (await fetch(`${base}/api/v1/conversations?view=inbox&limit=5`, { headers: { Cookie: cookie } })).json()
  return { cookie, conv: list.items?.[0]?.id }
}

function convSlug(id) {
  if (id < 2 ** 52) return String(id)
  const B = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
  let v = id
  let s = ''
  while (v > 0) {
    s = B[v % 62] + s
    v = Math.floor(v / 62)
  }
  return `c${s}`
}

function run(name, url, categories, headers) {
  const file = path.join(out, `${name}.json`)
  const args = ['-y', 'lighthouse@11.7.1', url, `--only-categories=${categories.join(',')}`, '--output=json', `--output-path=${file}`, '--quiet',
    '--chrome-flags=--headless=new --no-sandbox', '--max-wait-for-load=45000']
  if (headers) {
    // From a file, so the session cookie never shows in a command line or a log.
    const hf = path.join(out, '.headers.json')
    writeFileSync(hf, JSON.stringify(headers), { mode: 0o600 })
    args.push(`--extra-headers=${hf}`)
  }
  const env = { ...process.env }
  const c = chrome()
  if (c) env.CHROME_PATH = c
  const r = spawnSync('npx', args, { env, stdio: ['ignore', 'ignore', 'inherit'] })
  if (r.status !== 0) throw new Error(`lighthouse failed on ${url}`)
  const rep = JSON.parse(readFileSync(file, 'utf8'))
  const scores = Object.fromEntries(Object.entries(rep.categories).map(([k, v]) => [k, v.score]))
  const failing = Object.values(rep.categories).flatMap((cat) =>
    cat.auditRefs.filter((a) => a.weight > 0 && rep.audits[a.id].score !== null && rep.audits[a.id].score < 1).map((a) => `${cat.id}/${a.id}: ${rep.audits[a.id].title}`),
  )
  return { name, url: url.replace(base, ''), scores, failing }
}

const { cookie, conv } = await session()
const auth = { Cookie: cookie }
const pages = [
  ['login', `${base}/login`, ['pwa', 'accessibility'], null],
  ['inbox', `${base}/mail/inbox`, ['pwa', 'accessibility'], auth],
  ...(conv ? [['conversation', `${base}/mail/inbox/${convSlug(conv)}`, ['accessibility'], auth]] : []),
  ['calendar', `${base}/calendar`, ['accessibility'], auth],
  ['tasks', `${base}/tasks`, ['accessibility'], auth],
  ['settings', `${base}/settings/general`, ['accessibility'], auth],
]
const results = pages.map(([n, u, c, h]) => run(n, u, c, h))
writeFileSync(path.join(out, 'summary.json'), JSON.stringify(results, null, 2))
try {
  writeFileSync(path.join(out, '.headers.json'), '{}')
} catch {
  /* ignore */
}
let bad = false
for (const r of results) {
  const s = Object.entries(r.scores).map(([k, v]) => `${k} ${Math.round(v * 100)}`).join(', ')
  console.log(`${r.name.padEnd(13)} ${s}`)
  for (const f of r.failing) console.log(`    ${f}`)
  for (const [k, v] of Object.entries(r.scores)) if (MIN[k] && v < MIN[k]) bad = true
}
process.exit(bad ? 1 : 0)

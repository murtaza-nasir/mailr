// A fresh seeded user per test (backend: mailr e2e-seed), logged in through the API.
// Every test gets its own user, mailbox, labels and calendar, so specs running side by side
// (E2E_WORKERS) cannot see each other's changes. Each worker keeps one seeder process
// (`mailr e2e-seed --serve`) and asks it for a user per test; starting Python for every test cost
// about 3 s of CPU each. E2E_SEED_ONESHOT=1 runs `mailr e2e-seed` once per test, as before.
import { execFileSync, spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import { test as base, expect, type APIRequestContext, type BrowserContext, type Locator, type Page } from '@playwright/test'

export interface Seed {
  email: string
  password: string
  conversations: Record<string, number>
  labels: Record<string, number>
  /** Standing orders by name ("E2E Order" stars "E2E Order me"). */
  orders: Record<string, number>
  /** The assistant's requests waiting for approval, by order name ("E2E Approve me", "E2E Deny me"). */
  approvals: Record<string, number>
  /** The approval links as handed out (/approvals/a…), and the internal ids old links named. */
  approval_links: Record<string, string>
  approval_legacy_ids: Record<string, number>
  /** The user's own dev-Radicale calendar (null when Radicale is not running). */
  caldav: { url: string; username: string } | null
  /** `mailr e2e-seed --screener` (`test.use({ screener: true })`): the Screener on, senders waiting, by key. */
  screener?: { senders: Record<string, number> }
  /** `mailr e2e-seed --extras`, only for specs that ask with `test.use({ extras: true })`. */
  extras?: SeedExtras
  /** `mailr e2e-seed --subscriptions` (`test.use({ subscriptions: true })`): list and bulk mail, by subject. */
  subscriptions?: Record<string, number>
  /** `mailr e2e-seed --accounts` (`test.use({ accounts: true })`): a second mail account, Work, beside
   *  the e2e account (Home), with mail through each (backend core/mailaccounts.py). */
  accounts?: { home: number; work: number; home_address: string; work_address: string; work_identity: number; conversations: Record<string, number> }
}

export interface SeedExtras {
  conversations: Record<string, number> | null
  people: Record<string, string> | null
  calendar?: string
  task_list?: { name: string; url: string }
  /** "E2E Work" and "E2E Holidays" (read-only), by name → collection URL. */
  calendars?: Record<string, string>
  read_only_calendar?: string
  events?: Record<string, string>
  tasks?: Record<string, string>
  occurrences?: Record<string, string>
}

const BACKEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../backend')
/** The browser's zone (playwright.config.ts timezoneId): the seeded user's settings.timezone. */
const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone
const SEED_ENV = () => ({ ...process.env, MAILR_PUBLIC_IDS: process.env.MAILR_PUBLIC_IDS ?? 'true', MAILR_E2E_TIMEZONE: ZONE })
/** The backend's own mailr, when the venv exists (no `uv run` sync check per call). */
function mailr(): [string, string[]] {
  const bin = path.join(BACKEND, '.venv', 'bin', 'mailr')
  return fs.existsSync(bin) ? [bin, []] : ['uv', ['run', 'mailr']]
}

/** Runs the backend's `mailr` (args) or, with `python: true`, its Python on a script (`-c`), with the
 *  e2e environment; returns stdout. The venv's own binaries when they exist: `uv run` checks the
 *  lock file and the environment first, every call. */
export function backend(args: string[], opts: { python?: boolean; input?: string; env?: Record<string, string> } = {}): string {
  const [cmd, pre] = opts.python ? python() : mailr()
  return execFileSync(cmd, [...pre, ...args], { cwd: BACKEND, env: { ...SEED_ENV(), ...opts.env }, input: opts.input, encoding: 'utf8' })
}
function python(): [string, string[]] {
  const bin = path.join(BACKEND, '.venv', 'bin', 'python')
  return fs.existsSync(bin) ? [bin, []] : ['uv', ['run', 'python']]
}

interface SeedRequest { extras?: boolean; bulk?: number; subscriptions?: boolean; screener?: boolean; accounts?: boolean; access?: boolean; timezone?: string }

function seedOnce(req: SeedRequest): unknown {
  const [cmd, pre] = mailr()
  const flags = req.access ? ['--access'] : [...(req.extras ? ['--extras'] : []), ...(req.bulk ? ['--bulk', String(req.bulk)] : []),
    ...(req.subscriptions ? ['--subscriptions'] : []), ...(req.screener ? ['--screener'] : []), ...(req.accounts ? ['--accounts'] : [])]
  const out = execFileSync(cmd, [...pre, 'e2e-seed', ...flags], { cwd: BACKEND, env: SEED_ENV(), encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop()!)
}

const MARK = '@@seed '

/** How many users a worker seeds ahead for the request its last test made (E2E_SEED_AHEAD, default 1;
 *  0 seeds only on request). */
const SEED_AHEAD = Math.max(0, Number(process.env.E2E_SEED_AHEAD ?? 1))

/** One `mailr e2e-seed --serve` per worker: requests go in on stdin, one answer per line comes back,
 *  matched by id.
 *
 *  Seeding ahead: a test waited 1.5–3.5 s for its user before the page opened. Tests of one spec
 *  file run one after another on a worker and ask for the same kind of user (the same options and
 *  MAILR_E2E_* switches), so when a test takes a user, the seeder starts on the next one of that
 *  kind while the test runs. The next test with the same request takes it at once. Every user still
 *  goes to exactly one test, and a request of another kind is seeded on demand, as before. */
class Seeder {
  private proc: ChildProcessWithoutNullStreams
  private waiting = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>()
  private nextId = 1
  private stderr: string[] = []
  private exited: string | null = null
  /** Users seeded ahead, by request key; each is handed out once. */
  private ahead = new Map<string, Promise<unknown>[]>()

  constructor() {
    const [cmd, pre] = mailr()
    this.proc = spawn(cmd, [...pre, 'e2e-seed', '--serve'], { cwd: BACKEND, env: SEED_ENV() })
    createInterface({ input: this.proc.stdout }).on('line', (line) => {
      if (!line.startsWith(MARK)) return
      const reply = JSON.parse(line.slice(MARK.length)) as { id?: number; ok?: unknown; error?: string }
      const w = reply.id === undefined ? undefined : this.waiting.get(reply.id)
      if (!w) return
      this.waiting.delete(reply.id!)
      if (reply.error) w.reject(new Error(`mailr e2e-seed --serve: ${reply.error}\n${this.stderr.slice(-20).join('\n')}`))
      else w.resolve(reply.ok)
    })
    createInterface({ input: this.proc.stderr }).on('line', (line) => {
      this.stderr.push(line)
      if (this.stderr.length > 200) this.stderr.splice(0, 100)
    })
    this.proc.on('exit', (code, signal) => {
      this.exited = `exited (${code ?? signal})`
      for (const w of this.waiting.values()) w.reject(new Error(`mailr e2e-seed --serve ${this.exited}\n${this.stderr.slice(-20).join('\n')}`))
      this.waiting.clear()
    })
  }

  private send(req: Record<string, unknown>): Promise<unknown> {
    if (this.exited) return Promise.reject(new Error(`mailr e2e-seed --serve ${this.exited}\n${this.stderr.slice(-20).join('\n')}`))
    const id = this.nextId++
    return new Promise((resolve, reject) => {
      this.waiting.set(id, { resolve, reject })
      this.proc.stdin.write(JSON.stringify({ ...req, id }) + '\n')
    })
  }

  seed(req: SeedRequest): Promise<unknown> {
    // The seed reads MAILR_E2E_* switches (MAILR_E2E_HOSTILE); a spec may set them after the
    // seeder started, so each request carries their values now, and they are part of its key.
    const env = Object.fromEntries(['MAILR_E2E_HOSTILE', 'MAILR_E2E_EXTRAS'].map((k) => [k, process.env[k] ?? null]))
    const full = { timezone: ZONE, ...req, env }
    const key = JSON.stringify(full)
    const queue = this.ahead.get(key) ?? []
    const got = queue.shift() ?? this.send(full)
    while (queue.length < SEED_AHEAD) {
      const next = this.send(full)
      // A failed seed-ahead surfaces when a test takes it, not as an unhandled rejection.
      next.catch(() => {})
      queue.push(next)
    }
    this.ahead.set(key, queue)
    return got
  }

  /** One raw message into `email`'s inbox (mailr/dev/e2e.py ingest_for). */
  ingest(email: string, raw: string): Promise<Ingested> {
    return this.send({ ingest: { email, raw } }) as Promise<Ingested>
  }

  /** Ends the seeder; it deletes the worker's dev Radicale users before it exits (waited for, up to a minute). */
  close(): Promise<void> {
    if (this.exited !== null) return Promise.resolve()
    const done = new Promise<void>((resolve) => {
      const t = setTimeout(resolve, 60_000)
      this.proc.once('exit', () => {
        clearTimeout(t)
        resolve()
      })
    })
    this.proc.stdin.end()
    return done
  }
}

export interface Ingested { message_id: number; conversation_id: number }

/** The worker's seeder, while its fixture is up (ingest uses it). */
let workerSeeder: Seeder | null = null

/** One raw message (RFC 5322 text) into the inbox of the user `email`, as the ingest job takes mail
 *  in, through the worker's seeder (no Python started per message). */
export async function ingest(email: string, raw: string): Promise<Ingested> {
  if (workerSeeder) return workerSeeder.ingest(email, raw)
  const out = backend(['e2e-seed', '--ingest', email], { input: raw })
  return JSON.parse(out.trim().split('\n').pop()!) as Ingested
}

/** A seed request through the worker's seeder (or a one-shot `mailr e2e-seed` with E2E_SEED_ONESHOT=1). */
type SeedFn = (req: SeedRequest) => Promise<unknown>

/** Where a failed attempt notes whether the browser saw a network change (for the retry's gate). */
const ATTEMPTS = path.join(process.env.PW_OUTPUT_DIR ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '../test-results'), '.attempts')

const seederFixture = base.extend<{ networkGuard: void; cspGuard: void }, { seeder: SeedFn }>({
  seeder: [async ({}, run) => { // eslint-disable-line no-empty-pattern -- Playwright reads the fixtures a function asks for from this pattern.
    if (process.env.E2E_SEED_ONESHOT === '1') {
      await run(async (req) => seedOnce(req))
      return
    }
    const s = new Seeder()
    workerSeeder = s
    try {
      await run((req) => s.seed(req))
    } finally {
      workerSeeder = null
      await s.close()
    }
  }, { scope: 'worker', timeout: 90_000 }],
  // Chromium drops every request in flight with net::ERR_NETWORK_CHANGED when the machine's network
  // interfaces change, and they change whenever a Docker container starts or stops (a veth pair on
  // docker0). On a shared workstation that happens during a long run, and the page then misses a
  // module or an API answer. The config allows one retry (E2E_RETRIES); this guard lets a test use
  // it only when its first attempt failed and the browser saw such a change. Any other failure
  // fails the retry at once, with the first attempt's error, so a real flake is never hidden.
  networkGuard: [async ({ context, browser }, run, testInfo) => {
    const key = path.join(ATTEMPTS, `${testInfo.testId}.json`)
    if (testInfo.retry > 0) {
      let prev: { network: boolean; error: string } | null = null
      try { prev = JSON.parse(fs.readFileSync(key, 'utf8')) } catch { /* no note: not ours to retry */ }
      if (!prev?.network) throw new Error(`Not retried: the first attempt failed without a network change.\n${prev?.error ?? '(no note of the first attempt)'}`)
      testInfo.annotations.push({ type: 'retry', description: 'the first attempt failed after a network change (net::ERR_NETWORK_CHANGED)' })
    }
    let changed = 0
    const watch = (c: typeof context) => c.on('requestfailed', (r) => { if (r.failure()?.errorText.includes('ERR_NETWORK_CHANGED')) changed++ })
    watch(context)
    // Contexts the test opens itself (a second user) count too.
    const newContext = browser.newContext.bind(browser)
    browser.newContext = async (...a: Parameters<typeof browser.newContext>) => { const c = await newContext(...a); watch(c); return c }
    try {
      await run()
    } finally {
      browser.newContext = newContext
      if (testInfo.status !== testInfo.expectedStatus) {
        fs.mkdirSync(ATTEMPTS, { recursive: true })
        fs.writeFileSync(key, JSON.stringify({ network: changed > 0, error: testInfo.error?.message ?? testInfo.status }))
      }
    }
  }, { auto: true }],
  // The pages run under the production Content-Security-Policy (vite.config.ts pageHeaders sends
  // it). Any violation fails the test: a console message about the policy ("Refused to …") or a
  // securitypolicyviolation event in any page or frame of the test's contexts. The one exception is
  // the message body frame (about:srcdoc): its own policy blocks mail scripts, frames and
  // unapproved images on purpose.
  cspGuard: [async ({ context, browser }, run) => {
    const seen: string[] = []
    const watch = async (c: BrowserContext) => {
      c.on('console', (m) => {
        const text = m.text()
        if (!CSP_MESSAGE.test(text)) return
        const where = m.location().url
        if (where === 'about:srcdoc') return
        seen.push(`console ${m.type()}: ${text}${where ? ` (${where})` : ''}`)
      })
      await c.exposeBinding('__mailrCspViolation', ({ frame }, v: CspViolation) => {
        if (frame.url() === 'about:srcdoc') return
        seen.push(`securitypolicyviolation: ${v.directive} blocked ${v.blocked || '(inline)'} in ${v.document}` +
          (v.source ? ` at ${v.source}:${v.line}` : '') + (v.sample ? ` ("${v.sample}")` : ''))
      })
      await c.addInitScript(() => {
        document.addEventListener('securitypolicyviolation', (e) => {
          const report = (window as unknown as { __mailrCspViolation?: (v: unknown) => void }).__mailrCspViolation
          report?.({ directive: e.effectiveDirective, blocked: e.blockedURI, source: e.sourceFile, line: e.lineNumber,
            sample: e.sample, document: document.URL })
        })
      })
    }
    await watch(context)
    // Contexts the test opens itself (a second user) count too.
    const newContext = browser.newContext.bind(browser)
    browser.newContext = async (...a: Parameters<typeof browser.newContext>) => { const c = await newContext(...a); await watch(c); return c }
    try {
      await run()
    } finally {
      browser.newContext = newContext
    }
    if (seen.length) throw new Error(`The page broke the Content-Security-Policy (${seen.length}):\n${[...new Set(seen)].join('\n')}`)
  }, { auto: true }],
  page: async ({ page }, run) => {
    gateReviewShots(page)
    const live = new Map<object, string>()
    inFlight.set(page, live)
    // A new document starts afresh: the old one's requests get no finished event.
    page.on('framenavigated', (f) => { if (f === page.mainFrame()) live.clear() })
    page.on('request', (r) => live.set(r, r.url()))
    page.on('requestfinished', (r) => live.delete(r))
    page.on('requestfailed', (r) => live.delete(r))
    await run(page)
    // A route handler still waiting for the real answer when the test ends would fail the test.
    if (!page.isClosed()) await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {})
  },
})

/** Review screenshots: taken while E2E_SHOTS (a folder: most specs write their shots there) or any
 *  spec's own switch is set (PANEL_SHOTS, SWEEP_SHOTS…; a switch still selects its spec's shots, and
 *  the other review shots of the run come too). Specs wrote 191 screenshots to files for a person to look through in every run (one test 40
 *  of them), and none was compared with anything. Without a switch, `screenshot({ path })` on a page
 *  writes nothing, and on a locator it only waits until the element is visible and scrolls it into
 *  view, as the screenshot did, so the test goes on from the same page. A screenshot without a path
 *  (its picture is used) is always taken. */
export const REVIEW_SHOTS = Object.entries(process.env).some(([k, v]) => /^[A-Z0-9_]+_SHOTS$/.test(k) && !!v && v !== '0')
let shotsGated = false
function gateReviewShots(page: Page) {
  if (REVIEW_SHOTS || shotsGated) return
  shotsGated = true
  // The client classes are shared by every page and locator of the worker (second users' pages too).
  const pageProto = Object.getPrototypeOf(page) as { screenshot: Page['screenshot'] }
  const pageShot = pageProto.screenshot
  pageProto.screenshot = async function (this: Page, opts?: Parameters<Page['screenshot']>[0]) {
    if (opts?.path) return Buffer.alloc(0)
    return pageShot.call(this, opts)
  }
  const locProto = Object.getPrototypeOf(page.locator('html')) as { screenshot: Locator['screenshot'] }
  const locShot = locProto.screenshot
  locProto.screenshot = async function (this: Locator, opts?: Parameters<Locator['screenshot']>[0]) {
    if (!opts?.path) return locShot.call(this, opts)
    await this.scrollIntoViewIfNeeded({ timeout: opts.timeout })
    return Buffer.alloc(0)
  }
}

/** Console text about the Content-Security-Policy (Chromium: "Refused to …"; Firefox and WebKit name the policy). */
const CSP_MESSAGE = /Content[- ]Security[- ]Policy|Refused to/i
interface CspViolation { directive: string; blocked: string; source: string; line: number; sample: string; document: string }

/** Requests the page has sent and not yet seen answered (apiSettled). */
const inFlight = new WeakMap<Page, Map<object, string>>()

/** Waits until the page has no API request matching `match` in flight, for `quietMs` in a row. A
 *  test that acts on what the page has cached (an event's version, a list it refetches after a
 *  change) waits for the refetch first, as a person would wait to see it; on a busy machine the
 *  refetch can take seconds. The quiet time covers the live stream too: the server tells the page
 *  about a change up to a second later (core/events.py coalesces for 0.75 s), and the page then
 *  reloads again. */
export async function apiSettled(page: Page, match: RegExp = /\/api\/v1\//, quietMs = 1200) {
  const pending = () => [...(inFlight.get(page)?.values() ?? [])].filter((u) => match.test(u) && !/\/api\/v1\/(events|live\/stream)/.test(u))
  let quietSince = Date.now()
  await expect.poll(() => {
    if (pending().length) quietSince = Date.now()
    return Date.now() - quietSince >= quietMs
  }, { intervals: [50], timeout: 30_000, message: 'API requests still in flight' }).toBe(true)
}

/** Playwright's own test plus the network guard, for specs that sign in without the seeded user. */
/** The authserv-id the e2e servers trust (the backend's MAILR_TRUSTED_AUTHSERV_IDS must hold it).
 * Stamp it on an Authentication-Results header for mail a test means to be verified; `localhost`
 * is not trusted, as in production. */
export const E2E_AUTHSERV_ID = 'mx.e2e.example'

/** Where a spec saves its review screenshots: under Playwright's output folder (PW_OUTPUT_DIR or
 *  test-results), never a fixed path on someone's machine. */
export function shotsDir(name: string): string {
  const dir = path.resolve(process.env.PW_OUTPUT_DIR ?? 'test-results', 'shots', name)
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

/** A review screenshot's file under shotsDir('review'): `shot('lists-offline.png')`. */
export function shot(file: string): string {
  return path.join(shotsDir('review'), file)
}

export const bareTest = seederFixture

export const test = seederFixture.extend<{ page: Page; seeded: Seed; extras: boolean; bulk: number; subscriptions: boolean; screener: boolean; accounts: boolean }>({
  // The richer seed (second and read-only calendars, a second task list, people, follow-ups) is opt-in.
  extras: [false, { option: true }],
  // `test.use({ bulk: 30 })`: that many more inbox conversations ("E2E Bulk 0" …).
  bulk: [0, { option: true }],
  // `test.use({ subscriptions: true })`: mail for the Subscriptions and Clean up pages.
  subscriptions: [false, { option: true }],
  // `test.use({ screener: true })`: the sender screener on, with first-time senders waiting.
  screener: [false, { option: true }],
  // `test.use({ accounts: true })`: a second mail account with mail of its own.
  accounts: [false, { option: true }],
  // A fresh user per test: tests never see each other's changes.
  seeded: async ({ seeder, extras, bulk, subscriptions, screener, accounts }, run) => run((await seeder({ extras, bulk, subscriptions, screener, accounts })) as Seed),
  page: async ({ page, seeded }, run) => {
    const r = await page.request.post('/api/v1/auth/login', { data: { email: seeded.email, password: seeded.password } })
    expect(r.ok()).toBeTruthy()
    // API writes from the test itself (setting up tasks and events) carry the session's CSRF token.
    await page.context().setExtraHTTPHeaders({ 'X-CSRF-Token': (await r.json()).csrf })
    await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
    await run(page)
  },
})
export { expect }
export type { Page }

/** `mailr e2e-seed --access`: an admin, and a "family" user in `group`, whose policy allows only
 *  `endpoint` (model e2e-small) for chat and sorting, with model choice and API tokens off. */
export interface AccessSeed {
  group: string
  endpoint: string
  admin: SeedAccount
  family: SeedAccount
}
export interface SeedAccount { email: string; password: string; id: number }

/** Signs `page` in as `who` through the API, like the page fixture (CSRF header, classic theme). */
export async function signIn(page: Page, who: SeedAccount) {
  const r = await page.request.post('/api/v1/auth/login', { data: { email: who.email, password: who.password } })
  expect(r.ok(), await r.text()).toBeTruthy()
  await page.context().setExtraHTTPHeaders({ 'X-CSRF-Token': (await r.json()).csrf })
  await page.addInitScript(() => localStorage.setItem('mailr.uiTheme', 'classic'))
}

/** Specs for access policies: a fresh admin and family user per test; nobody is signed in yet. */
export const accessTest = seederFixture.extend<{ access: AccessSeed }>({
  access: async ({ seeder }, run) => run((await seeder({ access: true })) as AccessSeed),
})

/** The conversation as the API sees it now, found by its (unique) subject. */
export async function conv(request: APIRequestContext, subject: string) {
  const r = await request.get('/api/v1/conversations', { params: { view: 'all', q: `in:anywhere subject:"${subject}"`, limit: '5' } })
  const items = (await r.json()).items as { id: number; subject: string; label_ids: number[]; unread_count: number;
    starred: boolean; important: boolean; muted: boolean; snoozed_until: string | null }[]
  const c = items.find((x) => x.subject === subject)
  if (!c) throw new Error(`no conversation "${subject}"`)
  return c
}

export async function roles(request: APIRequestContext): Promise<Record<string, number>> {
  const labels = (await (await request.get('/api/v1/labels')).json()) as { id: number; role: string | null }[] | { items: { id: number; role: string | null }[] }
  const list = Array.isArray(labels) ? labels : labels.items
  return Object.fromEntries(list.filter((l) => l.role).map((l) => [l.role!, l.id]))
}

/** Connects the seeded user's calendar; skips the test when the dev Radicale is not running. */
export async function connectCalendar(request: APIRequestContext, seeded: Seed) {
  test.skip(!seeded.caldav, 'Radicale dev server not running on :15232')
  // The dev Radicale accepts any password.
  const r = await request.post('/api/v1/calendar/connect', { data: { url: seeded.caldav!.url, username: seeded.caldav!.username, password: 'x' } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

export interface ApiOutbox { id: number; status: string; send_at: string; subject: string; sent_at: string | null }

/** The outbox (the seeded identity's connector is disabled, so nothing here can leave the machine). */
export async function outbox(request: APIRequestContext): Promise<ApiOutbox[]> {
  return (await (await request.get('/api/v1/outbox')).json()) as ApiOutbox[]
}

export interface ApiTask { uid: string; summary: string; due: string | null; status: string; notes: string | null; conversation_id: number | null }
export interface ApiEvent { uid: string; summary: string; start: string; end: string | null; location: string | null; my_status: string | null }

export async function tasks(request: APIRequestContext): Promise<ApiTask[]> {
  const j = await (await request.get('/api/v1/tasks', { params: { status: 'all' } })).json()
  return (Array.isArray(j) ? j : j.items) as ApiTask[]
}

/** Events from now to `days` ahead. */
export async function events(request: APIRequestContext, days = 5): Promise<ApiEvent[]> {
  const from = new Date(); from.setHours(0, 0, 0, 0)
  const to = new Date(from.getTime() + days * 86_400_000)
  const j = await (await request.get('/api/v1/calendar/agenda', { params: { from: from.toISOString(), to: to.toISOString() } })).json()
  return (Array.isArray(j) ? j : j.events ?? j.items) as ApiEvent[]
}

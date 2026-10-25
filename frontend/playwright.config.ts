// Browser end-to-end tests (the web.* surfaces). They need the dev stack:
//   backend: uv run mailr serve --port 8025 (MAILR_PUBLIC_IDS=true,
//            MAILR_TRUSTED_AUTHSERV_IDS=mx.e2e.example: E2E_AUTHSERV_ID in e2e/fixtures.ts), worker optional
//   frontend: npx vite --port 5175
// then: npx playwright test. Each test seeds its own user (mailr e2e-seed, through one seeder per worker).
// Several checkouts at once: set E2E_BASE, MAILR_WEB_PORT and PW_OUTPUT_DIR for each.
// E2E_WORKERS=6 runs specs side by side: every test seeds its own user, so they do not collide.
import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test'

// Phones: e2e/phone/ runs on a Pixel 7 (Chromium) and an iPhone 14 (WebKit),
// touch on, against the dev server; e2e/pwa/ needs the production build (service worker,
// precache, offline start) and runs against `vite preview` of it, started here when asked for.
// WebKit runs in Playwright's container (scripts/webkit-server.sh) when E2E_WEBKIT_WS is set,
// or on this machine with E2E_WEBKIT=local; without either the iPhone projects are left out.
// With E2E_WEBKIT_AUTO=1 (the container started for a run that includes e2e/pwa) only
// iphone-pwa joins; the iphone project (e2e/phone in WebKit) stays
// opt-in through E2E_WEBKIT_WS or E2E_WEBKIT set by hand.
const webBase = process.env.E2E_BASE ?? 'http://localhost:5175'
const previewPort = Number(process.env.E2E_PREVIEW_PORT ?? (process.env.MAILR_WEB_PORT ? Number(process.env.MAILR_WEB_PORT) + 7 : 4173))
const previewBase = `http://localhost:${previewPort}`
/** The file filters and --project names on the command line (`playwright test [filters…]`). */
function cliSelection(argv: string[]): { files: string[]; projects: string[] } {
  // Options that take the next word as their value (the --opt=value form needs no skipping).
  const valued = new Set(['-c', '--config', '-g', '--grep', '-gv', '--grep-invert', '--project', '--output', '-j', '--workers',
    '--reporter', '--timeout', '--retries', '--repeat-each', '--shard', '--max-failures', '--browser', '--trace',
    '--global-timeout', '--tsconfig', '--tag', '--last-failed-file', '--update-source-method'])
  const at = argv.indexOf('test')
  const args = at < 0 ? [] : argv.slice(at + 1)
  const files: string[] = []
  const projects: string[] = []
  for (let i = 0; i < args.length; i++) {
    const a = args[i]
    if (a === '--project') projects.push(args[++i] ?? '')
    else if (a.startsWith('--project=')) projects.push(a.slice('--project='.length))
    else if (valued.has(a)) i++
    else if (!a.startsWith('-')) files.push(a)
  }
  return { files, projects }
}
// The preview server starts when e2e/pwa is asked for by path or project, and for a whole run
// (no file filters and no --project that leaves the pwa projects out): the pixel-pwa project is
// part of it.
const cli = cliSelection(process.argv)
const pwaProject = (p: string) => /^[a-z]+-pwa$/.test(p)
const wantsPwa = process.env.E2E_PWA === '1'
  || cli.files.some((f) => /e2e[\\/]pwa|^pwa([\\/]|$)/.test(f))
  || cli.projects.some(pwaProject)
  || (!cli.files.length && !cli.projects.length)
const webkit = process.env.E2E_WEBKIT_WS ? { connectOptions: { wsEndpoint: process.env.E2E_WEBKIT_WS } }
  : process.env.E2E_WEBKIT === 'local' ? {} : null
const pixel = { ...devices['Pixel 7'], hasTouch: true, isMobile: true }
const iphone = { ...devices['iPhone 14'], hasTouch: true, isMobile: true, launchOptions: {} }
const phoneProjects: NonNullable<PlaywrightTestConfig['projects']> = [
  { name: 'pixel', testMatch: /e2e[\\/]phone[\\/].*\.spec\.ts$/, use: pixel },
  { name: 'pixel-pwa', testMatch: /e2e[\\/]pwa[\\/].*\.spec\.ts$/, use: { ...pixel, baseURL: previewBase } },
  ...(webkit
    ? [
        ...(process.env.E2E_WEBKIT_AUTO === '1' ? [] : [{ name: 'iphone', testMatch: /e2e[\\/]phone[\\/].*\.spec\.ts$/, use: { ...iphone, ...webkit } }]),
        { name: 'iphone-pwa', testMatch: /e2e[\\/]pwa[\\/].*\.spec\.ts$/, use: { ...iphone, ...webkit, baseURL: previewBase } },
      ]
    : []),
]

export default defineConfig({
  testDir: './e2e',
  // Headroom for a full parallel run on a machine other agents load too: in dev mode a page's lazy
  // chunks load one after another, and the dev Radicale and Postgres are shared. A waiting
  // assertion only takes this long when it is about to fail.
  timeout: 120_000,
  expect: { timeout: 30_000 },
  // One retry, used only after a network change (fixtures.ts, networkGuard): any other failure
  // fails the retry at once. E2E_RETRIES=0 turns it off.
  retries: Number(process.env.E2E_RETRIES ?? 1),
  workers: Number(process.env.E2E_WORKERS ?? 1),
  // Tests, not files, are shared out to the workers: every test seeds its own user, so the tests of
  // one file do not depend on each other, and a long file (a theme grid) no longer runs on one
  // worker after the others are done. A file that must run in order says so
  // (test.describe.configure({ mode: 'default' })).
  fullyParallel: true,
  // PW_OUTPUT_DIR gives each checkout its own folder; --output still wins.
  outputDir: process.env.PW_OUTPUT_DIR ?? 'test-results',
  use: {
    // The browser's clock zone matches the test's, so times typed into the page mean the same thing.
    timezoneId: Intl.DateTimeFormat().resolvedOptions().timeZone,
    baseURL: webBase,
    viewport: { width: 1440, height: 900 },
    // A browser build other than the one this Playwright expects: E2E_CHROME (with --browser=chromium)
    // or E2E_FIREFOX (with --browser=firefox).
    launchOptions: process.env.E2E_CHROME ? { executablePath: process.env.E2E_CHROME }
      : process.env.E2E_FIREFOX ? { executablePath: process.env.E2E_FIREFOX } : {},
    trace: 'retain-on-failure',
    // The review screenshots specs used to take on every run are off (E2E_SHOTS, e2e/fixtures.ts);
    // a failed test still leaves a picture of where it stopped.
    screenshot: 'only-on-failure',
  },
  projects: [
    // Everything else, as before: desktop Chromium at 1440 × 900.
    { name: 'desktop', testIgnore: [/e2e[\\/]phone[\\/]/, /e2e[\\/]pwa[\\/]/] },
    ...phoneProjects,
  ],
  // The production build behind `vite preview` (it proxies /api to the same API as the dev server).
  webServer: wantsPwa
    ? { command: `npx vite build --logLevel error && npx vite preview --host 127.0.0.1 --port ${previewPort} --strictPort`, url: `${previewBase}/manifest.webmanifest`, reuseExistingServer: true, timeout: 120_000 }
    : undefined,
})

import react from '@vitejs/plugin-react'
import { createHash } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import { bootLocale } from './src/i18n/boot.ts'

/** The service worker of a build (public/sw.js): its VERSION (a hash of the build's files) and
 *  PRECACHE (every hashed script and stylesheet, so the installed app starts and every app opens
 *  offline). A new build therefore always changes sw.js, which is how the browser notices it. */
function serviceWorker(): Plugin {
  let files: string[] = []
  let outDir = 'dist'
  return {
    name: 'mailr-service-worker',
    apply: 'build',
    configResolved(c) {
      outDir = path.resolve(c.root, c.build.outDir)
    },
    // After the other plugins' generateBundle (i18nKeys emits the locale boot there), so the boot
    // script is precached too; without it the installed app could not start offline.
    generateBundle: {
      order: 'post',
      handler(_opts, bundle) {
        files = Object.keys(bundle).filter((f) => f.startsWith('assets/') && /\.(js|css|woff2?)$/.test(f)).sort().map((f) => `/${f}`)
      },
    },
    closeBundle() {
      const file = path.join(outDir, 'sw.js')
      const src = readFileSync(file, 'utf8')
      const version = createHash('sha256').update(files.join('\n')).update(src).digest('hex').slice(0, 12)
      const out = src
        .replace("const VERSION = 'dev'", `const VERSION = '${version}'`)
        .replace('const PRECACHE = []', `const PRECACHE = ${JSON.stringify(files)}`)
      if (out === src) throw new Error('sw.js: VERSION or PRECACHE placeholder missing')
      writeFileSync(file, out)
    },
  }
}

/** The text keys in a build.
 *  - Short IDs: code says t('settings.calendarPrefs.region'); the build writes t('4f') and the locale
 *    files hold '4f' (plurals '4f_one', '4f_other').
 *  - English travels with the code that shows it: each module imports a small generated module with
 *    the English of the keys it names, so a lazy page's words load with that page, as they did
 *    before the extraction. en.json is not bundled whole; other languages load as one file.
 *  - The other languages are hashed files (assets/locale-<code>-<hash>.json, short IDs). index.html
 *    starts the app through src/i18n/boot.ts, which first makes sure the browser's kept table of the
 *    chosen language is this build's. With no language but
 *    English, index.html is left as it is.
 *  Development and the tests keep the readable keys and the whole en.json. */
function i18nKeys(): Plugin {
  const ids = new Map<string, string>()
  const PLURAL = /_(zero|one|two|few|many|other)$/
  const VIRTUAL = 'virtual:mailr-text/'
  type Table = { [k: string]: string | Table }
  const flatten = (o: Table, prefix = '', out: Record<string, string> = {}): Record<string, string> => {
    for (const [k, v] of Object.entries(o)) {
      if (typeof v === 'string') out[prefix + k] = v
      else flatten(v, `${prefix}${k}.`, out)
    }
    return out
  }
  let en: Record<string, string> = {}
  const byModule = new Map<string, Set<string>>()
  // The locale boot goes out as its own script file, never inline: the server's CSP (default-src
  // 'self') blocks inline scripts, and the inline boot also carried the app's start, so the page
  // stayed blank (0.16.0, 8 Oct 2026). Emitted in generateBundle, so the service worker precaches it;
  // index.html points at it.
  let bootFile: { fileName: string; source: string } | null = null
  const i18nEntry = path.resolve(import.meta.dirname, 'src/i18n/index.ts')
  const shortTable = (keys: Iterable<string>) => {
    const out: Record<string, string> = {}
    for (const base of keys) {
      const short = ids.get(base)!
      for (const k of [base, ...['zero', 'one', 'two', 'few', 'many', 'other'].map((c) => `${base}_${c}`)]) {
        if (en[k] !== undefined) out[short + k.slice(base.length)] = en[k]
      }
    }
    return out
  }
  return {
    name: 'mailr-i18n-keys',
    apply: 'build',
    enforce: 'pre',
    buildStart() {
      en = flatten(JSON.parse(readFileSync(path.resolve(import.meta.dirname, 'src/locales/en.json'), 'utf8')) as Table)
      const bases = [...new Set(Object.keys(en).map((k) => k.replace(PLURAL, '')))].sort()
      ids.clear()
      byModule.clear()
      bases.forEach((k, i) => ids.set(k, i.toString(36)))
      // Each other language as one hashed file of short IDs.
      const dir = path.resolve(import.meta.dirname, 'src/locales')
      for (const f of readdirSync(dir).filter((x) => x.endsWith('.json') && x !== 'en.json')) {
        const out: Record<string, string> = {}
        for (const [k, v] of Object.entries(flatten(JSON.parse(readFileSync(path.join(dir, f), 'utf8')) as Table))) {
          const base = k.replace(PLURAL, '')
          const short = ids.get(base)
          if (short !== undefined) out[short + k.slice(base.length)] = v
        }
        this.emitFile({ type: 'asset', name: `locale-${f.slice(0, -5)}.json`, source: JSON.stringify(out) })
      }
    },
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        const files: Record<string, string> = {}
        for (const item of Object.values(ctx.bundle ?? {})) {
          if (item.type !== 'asset') continue
          const name = (item.names ?? []).find((n) => /^locale-.+\.json$/.test(n))
          if (name) files[name.slice('locale-'.length, -'.json'.length)] = `/${item.fileName}`
        }
        if (!Object.keys(files).length) return html
        const tag = /<script type="module" crossorigin src="([^"]+)"><\/script>/.exec(html)
        if (!tag) throw new Error('i18nKeys: the entry script tag is missing from index.html')
        const boot = `window.__MAILR_LOCALES__=${JSON.stringify(files)};(${bootLocale.toString()})({` +
          'local:(function(){try{return localStorage}catch(e){return null}})(),' +
          'session:(function(){try{return sessionStorage}catch(e){return null}})(),' +
          'search:location.search,languages:navigator.languages||[navigator.language],' +
          'fetchText:function(u){return fetch(u).then(function(r){if(!r.ok)throw new Error(String(r.status));return r.text()})},' +
          `start:function(){var s=document.createElement("script");s.type="module";s.crossOrigin="";s.setAttribute("data-mailr-boot","");s.src=${JSON.stringify(tag[1])};document.head.appendChild(s)}` +
          '},window.__MAILR_LOCALES__)'
        const fileName = `assets/boot-${createHash('sha256').update(boot).digest('hex').slice(0, 10)}.js`
        bootFile = { fileName, source: boot }
        // The entry downloads while the boot fetches the language table (it would otherwise start
        // only when the boot adds its script tag).
        return html.replace(tag[0], `<script src="/${fileName}"></script>\n    <link rel="modulepreload" crossorigin href="${tag[1]}">`)
      },
    },
    generateBundle: {
      // After Vite's own HTML step, which runs transformIndexHtml (where the boot is made).
      order: 'post',
      handler() {
        if (bootFile) this.emitFile({ type: 'asset', fileName: bootFile.fileName, source: bootFile.source })
      },
    },
    writeBundle(opts) {
      const dir = opts.dir ?? path.resolve(import.meta.dirname, 'dist')
      // Guard: the page must run under the CSP, so no inline script may reach index.html.
      const html = readFileSync(path.join(dir, 'index.html'), 'utf8')
      const inline = [...html.matchAll(/<script\b([^>]*)>/g)].filter((m) => !/\bsrc=/.test(m[1]) && !/type="application\/(ld\+)?json"/.test(m[1]))
      if (inline.length) throw new Error(`index.html has ${inline.length} inline script(s); the server's CSP blocks them (vite.config.ts i18nKeys)`)
    },
    resolveId(id) {
      return id.startsWith(VIRTUAL) ? `\0${id}` : null
    },
    load(id) {
      if (!id.startsWith(`\0${VIRTUAL}`)) return null
      const keys = byModule.get(decodeURIComponent(id.slice(VIRTUAL.length + 1))) ?? new Set<string>()
      return `import { registerText } from ${JSON.stringify(i18nEntry)}\nregisterText(${JSON.stringify(shortTable(keys))})\n`
    },
    transform(code, id) {
      const file = id.split('?')[0]
      if (file.includes('/node_modules/') || file.startsWith('\0')) return null
      if (/\/src\/locales\/[^/]+\.json$/.test(file)) {
        const out: Record<string, string> = {}
        for (const [k, v] of Object.entries(flatten(JSON.parse(code) as Table))) {
          const base = k.replace(PLURAL, '')
          const short = ids.get(base)
          if (short !== undefined) out[short + k.slice(base.length)] = v
        }
        return { code: JSON.stringify(out), map: null }
      }
      if (!/\/src\/.*\.tsx?$/.test(file)) return null
      if (file === i18nEntry) return { code: code.replace("import en from '../locales/en.json'", 'const en = {}'), map: null }
      const used = new Set<string>()
      const next = code.replace(/(['"])([a-z][\w]*(?:\.[\w]+)+)\1/g, (m, q: string, k: string) => {
        const short = ids.get(k)
        if (short === undefined) return m
        used.add(k)
        return q + short + q
      })
      if (!used.size) return null
      byModule.set(file, used)
      return { code: `import ${JSON.stringify(VIRTUAL + encodeURIComponent(file))}\n${next}`, map: null }
    },
  }
}

/** The production page headers (CSP, X-Frame-Options, nosniff, Referrer-Policy) on the development
 *  server and `vite preview`, so the browser tests, the screenshots and development run under the
 *  policy production sends.
 *  - The values: the headers the API server sends on a page (backend main.py security_headers, from
 *    backend/mailr/core/webheaders.json), asked again after two seconds. The CSP there depends on
 *    the instance's image proxy setting. With no API server (the mock), the file's strict values.
 *  - React's refresh preamble is an inline script, which the CSP blocks: in development it loads as
 *    a module file instead (the React plugin's own virtual module), so hot reload still works. */
function pageHeaders(): Plugin {
  const file = path.resolve(import.meta.dirname, '../backend/mailr/core/webheaders.json')
  const api = process.env.MAILR_API ?? 'http://localhost:8025'
  type Table = { all: Record<string, string>; pages: Record<string, string>; csp: Record<string, string> }
  const fromFile = (): Record<string, string> => {
    const t = JSON.parse(readFileSync(file, 'utf8')) as Table
    return { ...t.all, ...t.pages, 'Content-Security-Policy': Object.entries(t.csp).map(([k, v]) => `${k} ${v}`).join('; ') }
  }
  const fromServer = async (): Promise<Record<string, string>> => {
    const want = fromFile()
    try {
      // Any path outside /api/ gets the page headers (a 404 here: the API server has no built pages).
      const r = await fetch(`${api}/__mailr-page-headers`, { signal: AbortSignal.timeout(2000) })
      const got = Object.fromEntries(Object.keys(want).map((k) => [k, r.headers.get(k)]).filter((e): e is [string, string] => !!e[1]))
      if (got['Content-Security-Policy']) return got
    } catch {
      // No API server: the file's values.
    }
    return want
  }
  let cached: { at: number; headers: Promise<Record<string, string>> } | null = null
  const current = () => {
    if (!cached || Date.now() - cached.at > 2000) cached = { at: Date.now(), headers: fromServer() }
    return cached.headers
  }
  const send = (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    // The API and the help pages (backend core/helpdocs.py) send their own headers.
    if (req.url?.startsWith('/api/') || req.url === '/help' || req.url?.startsWith('/help/')) return next()
    current().then((h) => {
      for (const [k, v] of Object.entries(h)) res.setHeader(k, v)
      next()
    }, () => next())
  }
  const PREAMBLE = /<script type="module">(?:(?!<\/script>)[\s\S])*injectIntoGlobalHook(?:(?!<\/script>)[\s\S])*<\/script>/
  return {
    name: 'mailr-page-headers',
    configureServer(server) {
      server.middlewares.use(send)
    },
    configurePreviewServer(server) {
      server.middlewares.use(send)
    },
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!ctx.server) return html
        return html.replace(PREAMBLE, '<script type="module" src="/@id/@vitejs/plugin-react/preamble"></script>')
      },
    },
  }
}

export default defineConfig({
  plugins: [i18nKeys(), react(), serviceWorker(), pageHeaders()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.MAILR_API ?? 'http://localhost:8025',
        changeOrigin: false,
        // SSE endpoints must not be buffered.
        configure: (proxy) => {
          proxy.on('proxyRes', (res) => {
            if ((res.headers['content-type'] ?? '').includes('text/event-stream')) {
              res.headers['cache-control'] = 'no-cache'
            }
          })
        },
      },
      // The user guide, served by the API server to signed-in people (core/helpdocs.py).
      '^/help(/|$)': { target: process.env.MAILR_API ?? 'http://localhost:8025', changeOrigin: false },
    },
  },
  build: {
    // Maps are for local debugging; not published (they contain the full source).
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rolldownOptions: {
      // @material/material-color-utilities has no "sideEffects" field, so the bundler kept the
      // whole library (58 kB) for the HCT colour space alone (20 kB). Its modules have none.
      treeshake: { moduleSideEffects: (id: string) => !id.includes('material-color-utilities') },
      output: {
        codeSplitting: {
          groups: [
            // React and the core libraries first: a group also takes the modules its own modules
            // import, so the editor group listed first used to take React with it, and the editor
            // (Tiptap, ~400 kB) loaded with the first page. Now it loads with the compose form.
            // react-dom/server is for the assistant's copy-as-email only (AssistantPanel loads it
            // lazily); keeping it out of vendor takes it off first paint.
            { name: 'vendor', test: /node_modules[\\/](react|react-dom|react-router|@tanstack|zustand|scheduler|use-sync-external-store)[\\/](?!server|cjs[\\/]react-dom-server)/, priority: 20 },
            { name: 'editor', test: /node_modules[\\/](@tiptap|prosemirror)/, priority: 10 },
            // Everything else the first page loads statically, in one file. Without it the lazy
            // splits (the reader, the palette, the rail panels…) leave the first page's shared
            // modules in dozens of small chunks, each one more request on the first load.
            { name: 'app', tags: ['$initial'], priority: 5 },
          ],
        },
      },
    },
  },
})

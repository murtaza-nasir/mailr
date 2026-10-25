import { describe, expect, it } from 'vitest'
import { RELOAD_GUARD_MS, buildSignature, claimChunkReload, countsForBuild, isNewBuild, moduleScripts } from './buildCheck'

const built = (hash: string) => `<!doctype html><html><head>
<link rel="manifest" href="/manifest.webmanifest">
<script type="module" crossorigin src="/assets/index-${hash}.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-${hash}.css">
</head><body><div id="root"></div></body></html>`

describe('build signature', () => {
  it("reads a production index.html's entry script", () => {
    expect(moduleScripts(built('AbC123'))).toEqual(['/assets/index-AbC123.js'])
  })
  it('reads the dev server page and skips inline and classic scripts', () => {
    const dev = `<script type="module" src="/@vite/client"></script>
<script type="module">import RefreshRuntime from "/@react-refresh"</script>
<script src="/legacy.js"></script>
<script type='module' src='/src/main.tsx?t=1'></script>`
    expect(moduleScripts(dev)).toEqual(['/@vite/client', '/src/main.tsx?t=1'])
    expect(buildSignature(moduleScripts(dev))).toBe('/@vite/client|/src/main.tsx')
  })
  it('reads a page that starts through the locale boot (other languages)', () => {
    const boot = (h: string) => `<script src="/assets/boot-${h}.js"></script>
    <link rel="modulepreload" crossorigin href="/assets/index-${h}.js">
    <link rel="modulepreload" crossorigin href="/assets/vendor-x.js">`
    expect(moduleScripts(boot('aaa'))).toEqual(['/assets/boot-aaa.js'])
    expect(isNewBuild(buildSignature(moduleScripts(boot('aaa'))), buildSignature(moduleScripts(boot('bbb'))))).toBe(true)
    expect(countsForBuild({ module: false, src: '/assets/boot-aaa.js' })).toBe(true)
    expect(countsForBuild({ module: false, src: '/legacy.js' })).toBe(false)
  })
  it('sees a deploy', () => {
    const now = buildSignature(moduleScripts(built('old111')))
    expect(isNewBuild(now, buildSignature(moduleScripts(built('old111'))))).toBe(false)
    expect(isNewBuild(now, buildSignature(moduleScripts(built('new222'))))).toBe(true)
    // An error page or a login proxy without scripts is not a new build.
    expect(isNewBuild(now, buildSignature(moduleScripts('<html><body>502 Bad Gateway</body></html>')))).toBe(false)
  })
})

describe('claimChunkReload', () => {
  const session = () => {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
  }
  it('reloads once, then not again inside the guard window', () => {
    const s = session()
    expect(claimChunkReload(1_000_000, s)).toBe(true)
    expect(claimChunkReload(1_000_000 + 5000, s)).toBe(false)
    expect(claimChunkReload(1_000_000 + RELOAD_GUARD_MS + 1, s)).toBe(true)
  })
  it('never reloads without session storage (no loop guard)', () => {
    expect(claimChunkReload(1, null)).toBe(false)
    const broken = { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } }
    expect(claimChunkReload(1, broken)).toBe(false)
  })
})

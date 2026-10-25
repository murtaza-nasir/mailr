// Has the server a newer build than this tab runs? A deploy changes the hashed entry script
// that index.html loads; a tab open across it still holds the old one, and its lazy chunks are
// gone from /assets.

/** Whether a script counts for the build: a module script, or the locale boot (a classic script
 *  whose name carries a hash of the build's entry and language files, vite.config.ts i18nKeys). */
export function countsForBuild(s: { module: boolean; src: string }): boolean {
  return s.module || /^\/assets\/boot-[^/]+\.js$/.test(s.src.replace(/[?#].*$/, ''))
}

/** The scripts of an HTML page that name its build (their src, in order): the module scripts, and
 *  the locale boot where the build has one. A page with other languages starts its entry from the
 *  boot (no module script in index.html), so without the boot such a page had no signature and a
 *  deploy went unnoticed. */
export function moduleScripts(html: string): string[] {
  const out: string[] = []
  for (const m of html.matchAll(/<script\b([^>]*)>/gi)) {
    const attrs = m[1]
    const src = /\bsrc\s*=\s*["']([^"']+)["']/i.exec(attrs)
    if (src && countsForBuild({ module: /\btype\s*=\s*["']?module\b/i.test(attrs), src: src[1] })) out.push(src[1])
  }
  return out
}

/** One string per build: the entry scripts, with any cache-busting query dropped. */
export function buildSignature(scripts: string[]): string {
  return scripts.map((s) => s.replace(/[?#].*$/, '')).join('|')
}

/** True when both signatures are known and differ. */
export function isNewBuild(current: string, served: string): boolean {
  return !!current && !!served && current !== served
}

const RELOAD_KEY = 'mailr.chunkReloadAt'
/** A second chunk failure within this window does not reload again: the prompt shows instead. */
export const RELOAD_GUARD_MS = 5 * 60_000

type Session = Pick<Storage, 'getItem' | 'setItem'>

/** May the app reload itself for a failed chunk now? Records the reload when it may. Without
 * session storage there is no loop guard, so it never reloads by itself. */
export function claimChunkReload(now: number, s: Session | null): boolean {
  if (!s) return false
  try {
    const last = Number(s.getItem(RELOAD_KEY)) || 0
    if (now - last < RELOAD_GUARD_MS) return false
    s.setItem(RELOAD_KEY, String(now))
    return true
  } catch {
    return false
  }
}

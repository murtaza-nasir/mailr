// Which answers the page asks for again once a service worker takes over a page that started
// without one (lib/serviceWorker.ts warmCache). The worker keeps only what passes through it, so a
// first visit's session, labels and lists must be fetched again through it, or the next start
// offline has nothing to show (installed-app.spec.ts, "offline changes … survive a restart").

/** What the worker keeps for offline use (public/sw.js KINDS). */
export const KEPT = /^\/api\/v1\/(auth\/session|labels|counts|layouts|layouts\/[^/]+\/counts|pictures\/local|conversations|conversations\/[^/]+|messages\/[^/]+\/body)$/

/** The answers every start needs, asked for even when the page has no timing entry for them. */
export const BOOT_URLS = ['/api/v1/auth/session', '/api/v1/labels', '/api/v1/counts', '/api/v1/layouts']

/** At most this many answers are asked for again. */
export const WARM_MAX = 30

/** The kept answers among these resource timing entries that went past the worker: same origin,
 *  a kept path, and started before the worker took over (`takeoverAt`, performance.now() time).
 *  Requests still running at the takeover count too: their entries arrive later, and the caller
 *  passes them in as they come (a list that was slow to answer is the one most needed offline). */
export function bypassedUrls(entries: Iterable<{ name: string; startTime: number }>, origin: string, takeoverAt: number): string[] {
  const out: string[] = []
  for (const e of entries) {
    if (e.startTime >= takeoverAt) continue
    let u: URL
    try {
      u = new URL(e.name)
    } catch {
      continue
    }
    // Timing entries carry no method: the bulk action endpoint (a POST) is never asked for again.
    if (u.origin === origin && KEPT.test(u.pathname) && u.pathname !== '/api/v1/conversations/actions') out.push(u.pathname + u.search)
  }
  return out
}

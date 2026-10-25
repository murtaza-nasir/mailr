// The page's side of the service worker (public/sw.js): registration, the update flow behind the
// "New version" banner (the new build waits until the user picks Reload), messages from the
// worker (open a notification's page, mail changed by a notification button), the app badge,
// and forgetting this device's cached mail on sign-out.
import { useAppStatus } from '../stores/appStatus'
import { appNavigate } from './appNav'
import { flushAllDrafts } from './draftStore'
import { BOOT_URLS, bypassedUrls, WARM_MAX } from './swWarm'

let reg: ServiceWorkerRegistration | null = null
const listeners = new Set<(m: { type: string; [k: string]: unknown }) => void>()

function supported(): boolean {
  return typeof navigator !== 'undefined' && 'serviceWorker' in navigator
}

/** A message from the worker reaches every listener (api/events.ts invalidates mail on 'mailr:mail-changed'). */
export function onWorkerMessage(fn: (m: { type: string; [k: string]: unknown }) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function watchWaiting(r: ServiceWorkerRegistration) {
  // A new build that installed while an older one controls this page waits for the user.
  const flag = () => {
    if (r.waiting && navigator.serviceWorker.controller) useAppStatus.getState().setNewVersion(true)
  }
  flag()
  r.addEventListener('updatefound', () => {
    const w = r.installing
    w?.addEventListener('statechange', () => {
      if (w.state === 'installed') flag()
    })
  })
}

/** Register /sw.js (production builds) and follow its updates. */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!supported()) return null
  navigator.serviceWorker.addEventListener('message', (e: MessageEvent) => {
    const m = e.data as { type?: string; url?: string } | null
    if (!m?.type) return
    if (m.type === 'mailr:navigate' && typeof m.url === 'string') appNavigate(m.url)
    for (const fn of listeners) fn(m as { type: string })
  })
  try {
    reg = await navigator.serviceWorker.register('/sw.js')
  } catch {
    return null // installability is optional
  }
  watchWaiting(reg)
  // The installed app may stay open for days: look for a new worker when it comes back.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void reg?.update().catch(() => undefined)
  })
  return reg
}

/** The worker controlling this page, if any. */
function controller(): ServiceWorker | null {
  return supported() ? navigator.serviceWorker.controller : null
}

/** The banner just appeared because index.html changed: fetch and install the new worker now, in
 *  the background, so Reload has it waiting instead of downloading the build after the click. */
let prefetched = false
export async function prefetchUpdate(): Promise<void> {
  if (prefetched || !supported() || !controller()) return
  prefetched = true
  const r = reg ?? ((await navigator.serviceWorker.getRegistration()) ?? null)
  if (r && !r.waiting && !r.installing) await r.update().catch(() => undefined)
}

/** "Reload" in the New version banner: write open drafts locally, let the waiting worker take
 *  over (the user agreed), and reload once it controls the page. Without a worker: reload. */
export async function applyUpdate(): Promise<void> {
  flushAllDrafts(true)
  const r = reg ?? (supported() ? ((await navigator.serviceWorker.getRegistration()) ?? null) : null)
  if (!r || !controller()) {
    window.location.reload()
    return
  }
  if (!r.waiting) {
    // The page saw the new build first (index.html changed): fetch the new worker now.
    await r.update().catch(() => undefined)
    // Usually done already (prefetchUpdate started it when the banner appeared); never keep the
    // user waiting long: past this the page reloads from the network and the worker finishes later.
    const deadline = Date.now() + 2_500
    while (!r.waiting && Date.now() < deadline) await new Promise((res) => setTimeout(res, 200))
  }
  if (!r.waiting) {
    // No new worker came: ask the old one to fetch the page from the server this time.
    controller()?.postMessage({ type: 'mailr:refresh-shell' })
    await new Promise((res) => setTimeout(res, 300))
    window.location.reload()
    return
  }
  let reloading = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloading) return
    reloading = true
    window.location.reload()
  })
  r.waiting.postMessage({ type: 'SKIP_WAITING' })
}

/** Tell the worker whose mail it keeps (its cache is per user), now and whenever a worker takes over. */
let userId: number | null = null
export function announceUser(id: number): void {
  userId = id
  controller()?.postMessage({ type: 'mailr:user', id })
  // Taken over before the user was known (the first visit): warm the cache now.
  if (controller() && !startedControlled) {
    takeoverAt ??= performance.now()
    void warmCache()
  }
}
const startedControlled = supported() && !!navigator.serviceWorker.controller
/** When a worker took over this page (performance.now()); requests started before it went past it. */
let takeoverAt: number | null = null
if (supported()) {
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!startedControlled) takeoverAt ??= performance.now()
    if (userId != null) controller()?.postMessage({ type: 'mailr:user', id: userId })
    void warmCache()
  })
}

let warmed = false

/** The first time a worker takes over (a fresh install), the page has already loaded its session,
 *  labels and lists past it: ask for them again through the worker, so the next start works offline.
 *  Lists still loading at the takeover went past the worker too: they are asked for again when
 *  they finish (a slow Primary tab was missed before, and the next start offline showed an error). */
async function warmCache(): Promise<void> {
  if (warmed || !controller() || userId == null) return
  warmed = true
  const before = takeoverAt ?? performance.now()
  const asked = new Set<string>()
  let chain = Promise.resolve()
  const warm = (urls: string[]) => {
    for (const u of urls) {
      if (asked.has(u) || asked.size >= WARM_MAX) continue
      asked.add(u)
      chain = chain.then(() => fetch(u, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(() => undefined, () => undefined))
    }
  }
  let entries: PerformanceEntryList = []
  try {
    entries = performance.getEntriesByType('resource')
  } catch {
    /* no timing data */
  }
  warm([...BOOT_URLS, ...bypassedUrls(entries, location.origin, before)])
  try {
    const watch = new PerformanceObserver((list) => warm(bypassedUrls(list.getEntries(), location.origin, before)))
    watch.observe({ type: 'resource' })
    setTimeout(() => watch.disconnect(), 30_000)
  } catch {
    /* no PerformanceObserver: the lists that finished in time are kept */
  }
  await chain
}

/** Forget this user's cached mail on this device (sign-out). */
export function forgetDeviceData(): void {
  userId = null
  controller()?.postMessage({ type: 'mailr:signout' })
  void setBadge(0)
}

/** The app icon's badge: the Inbox's unread count (Badging API; installed apps on iOS 16.4+,
 *  Chrome and Edge). Unsupported browsers ignore it. */
export async function setBadge(n: number): Promise<void> {
  const nav = navigator as Navigator & { setAppBadge?: (n?: number) => Promise<void>; clearAppBadge?: () => Promise<void> }
  try {
    if (n > 0) await nav.setAppBadge?.(n)
    else await nav.clearAppBadge?.()
  } catch {
    /* not allowed here (a plain browser tab on iOS) */
  }
}

/** Close the notifications of these conversations (read, archived or deleted here or elsewhere). */
export async function closeConversationNotifications(ids: number[]): Promise<void> {
  if (!ids.length || !supported()) return
  try {
    const r = reg ?? (await navigator.serviceWorker.getRegistration())
    if (!r) return
    const want = new Set(ids.map((x) => `c${x}`))
    for (const n of await r.getNotifications()) if (want.has(n.tag)) n.close()
  } catch {
    /* ignore */
  }
}

/** The notification tag of an approval (the service worker's showMail uses the same). */
export const approvalTag = (id: number | string) => `a${id}`

/** ACT approval.sync @web.notification: close the notifications of approvals that left pending
 *  (decided here or elsewhere, expired, withdrawn). The worker's own push handler does the same
 *  when no tab is open. */
export async function closeApprovalNotifications(ids: (number | string)[]): Promise<void> {
  if (!ids.length || !supported()) return
  try {
    const r = reg ?? (await navigator.serviceWorker.getRegistration())
    if (!r) return
    const want = new Set(ids.map(approvalTag))
    for (const n of await r.getNotifications()) if (want.has(n.tag)) n.close()
  } catch {
    /* ignore */
  }
}

/** The conversation ids of mail notifications showing now. */
export async function shownConversationNotifications(): Promise<number[]> {
  if (!supported()) return []
  try {
    const r = reg ?? (await navigator.serviceWorker.getRegistration())
    if (!r) return []
    return (await r.getNotifications())
      .map((n) => (n.data as { mail?: boolean; conversation_id?: number } | null))
      .filter((d): d is { mail: true; conversation_id: number } => !!d?.mail && typeof d.conversation_id === 'number')
      .map((d) => d.conversation_id)
  } catch {
    return []
  }
}

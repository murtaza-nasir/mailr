// In-app navigation from outside React (notifications, toasts, the service worker): the router
// moves, so the app keeps its state and never reloads the page. Before the router is mounted (or
// for a path outside the app) it falls back to a page load.

type Nav = (to: string) => void
let nav: Nav | null = null

export function setAppNavigate(fn: Nav | null): void {
  nav = fn
}

/** Go to an app path ("/mail/inbox/c…", "/approvals/a…"). Other origins are ignored. */
export function appNavigate(to: string): void {
  let path: string
  try {
    const u = new URL(to, window.location.origin)
    if (u.origin !== window.location.origin) return
    path = u.pathname + u.search + u.hash
  } catch {
    return
  }
  if (nav && !path.startsWith('/api/')) nav(path)
  else window.location.assign(path)
}

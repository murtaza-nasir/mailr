// Error reports from the web app to the server's error tracker (backend core/errors.py and
// api/error_routes.py). Off unless the server names a DSN (MAILR_SENTRY_BROWSER_DSN or
// MAILR_SENTRY_DSN): GET /api/v1/errors/config answers {dsn: null} otherwise, and the Sentry SDK
// is never loaded (it is a chunk of its own).
//
// Reports go to /api/v1/errors/envelope on this origin (the SDK's `tunnel`): the page's
// Content-Security-Policy allows no other host, and the server scrubs each report again before it
// passes it on. Here, before anything leaves the page: no breadcrumbs (clicks, console lines and
// requests could name mail), no request headers or cookies, no query strings or fragments in URLs,
// no user beyond what the server adds, no default integrations beyond the error handlers.

type Cfg = { dsn: string | null; release?: string | null; environment?: string | null; tunnel?: string | null }

/** A URL without its query string and fragment (a search or a token never leaves the page). */
export function stripUrl(url: string | undefined): string | undefined {
  if (!url) return url
  const cut = url.search(/[?#]/)
  return cut < 0 ? url : url.slice(0, cut)
}

const EMAIL = /[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+/g

/** Drop everything personal from a report (the server does the same once more). */
export function scrubEvent<T extends object>(event: T): T {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const e = event as Record<string, any>
  if (e.request) e.request = { url: stripUrl(e.request.url) }
  delete e.breadcrumbs
  delete e.extra
  delete e.user
  if (typeof e.message === 'string') e.message = e.message.replace(EMAIL, '<address>')
  for (const v of e.exception?.values ?? []) {
    if (typeof v.value === 'string') v.value = v.value.replace(EMAIL, '<address>')
    for (const f of v.stacktrace?.frames ?? []) {
      f.abs_path = stripUrl(f.abs_path)
      f.filename = stripUrl(f.filename)
      delete f.vars
    }
  }
  return event
}

let started = false

export async function startErrorReports(): Promise<boolean> {
  if (started) return true
  let cfg: Cfg
  try {
    const r = await fetch('/api/v1/errors/config', { credentials: 'same-origin' })
    if (!r.ok) return false
    cfg = (await r.json()) as Cfg
  } catch {
    return false
  }
  if (!cfg.dsn) return false
  try {
    const Sentry = await import('./sentryParts')
    Sentry.init({
      dsn: cfg.dsn,
      release: cfg.release ?? undefined,
      environment: cfg.environment ?? undefined,
      // The tunnel's address carries a key for this browser (signed-out pages need it).
      tunnel: cfg.tunnel ?? '/api/v1/errors/envelope',
      sendDefaultPii: false,
      maxBreadcrumbs: 0,
      defaultIntegrations: false,
      integrations: [
        Sentry.eventFiltersIntegration(),
        Sentry.functionToStringIntegration(),
        Sentry.globalHandlersIntegration(),
        Sentry.linkedErrorsIntegration(),
        Sentry.dedupeIntegration(),
      ],
      sendClientReports: false,
      beforeSend: (event) => scrubEvent(event),
    })
    started = true
    return true
  } catch {
    return false
  }
}

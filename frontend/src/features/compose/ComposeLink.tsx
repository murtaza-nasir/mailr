// /compose?to=…&cc=…&bcc=…&subject=…&body=… and /compose?mailto=<mailto: URL>: the link that the
// browser's email-link handler (Settings → Mail → Writing → Email links), the PWA manifest's
// protocol_handlers and the desktop's mailto handler (tools/desktop/install-mailto-handler.sh) open.
// It opens the composer, filled in, over the mail list, in the current theme. Signed out, the sign-in
// page comes first and brings the link back (next=). A long link waits in this tab's sessionStorage
// across the sign-in, since the sign-in provider's round trip keeps `next` in a small cookie.
// ACT compose.deeplink @web.editor

import { useEffect } from 'react'
import { Navigate, useLocation } from 'react-router'
import { appHome } from '../../lib/routes'
import { parseComposeQuery } from '../../lib/mailto'
import { openComposeWith } from './mailtoLinks'

const STASH = 'mailr.composeLink'
/** Longer than this, the link waits in sessionStorage while the user signs in. */
const LONG = 1500

/** The sign-in address for a signed-out visit to `path` (pathname + search). */
export function loginNext(path: string): string {
  if (path.startsWith('/compose') && path.length > LONG) {
    try {
      sessionStorage.setItem(STASH, path.slice(path.indexOf('?')))
      return '/compose?kept=1'
    } catch {
      /* no storage: the long link goes as it is */
    }
  }
  return path
}

function takeStash(): string | null {
  try {
    const v = sessionStorage.getItem(STASH)
    sessionStorage.removeItem(STASH)
    return v
  } catch {
    return null
  }
}

// One open per history entry (React's development double effects, a re-render of the route).
const handled = new Set<string>()

export function ComposeLink() {
  const location = useLocation()
  useEffect(() => {
    if (handled.has(location.key)) return
    handled.add(location.key)
    const search = new URLSearchParams(location.search).get('kept') === '1' ? (takeStash() ?? '') : location.search
    // A bare /compose opens an empty draft, like the Compose button.
    openComposeWith(parseComposeQuery(search) ?? { to: [], cc: [], bcc: [], subject: '', body: '' })
  }, [location.key, location.search])
  return <Navigate to={appHome('mail')} replace />
}

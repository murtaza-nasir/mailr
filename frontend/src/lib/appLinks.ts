// A link into this app, written into free text (a task's notes, an event's description), shown
// short: "Open in Notes" for https://<this server>/notes/n/n8c7…#i-01a1…. Other clients keep the
// full address; here the long URL would fill the field (review of the notes-links shot, 7 Oct).
import { t } from '../i18n'

const AREAS: [RegExp, () => string][] = [
  [/^\/notes\//, () => t('lib.appLinks.openInNotes')],
  [/^\/tables\//, () => t('lib.appLinks.openInTables')],
  [/^\/mail\//, () => t('lib.appLinks.openInMail')],
  [/^\/calendar\//, () => t('lib.appLinks.openInCalendar')],
  [/^\/tasks\//, () => t('lib.appLinks.openInTasks')],
  [/^\/approvals\//, () => t('lib.appLinks.openTheApproval')],
]

/** The app path of `href` when it points into this app (same origin), else null. */
export function appPath(href: string | undefined | null, origin = typeof window === 'undefined' ? '' : window.location.origin): string | null {
  if (!href || !origin) return null
  try {
    const u = new URL(href, origin)
    if (u.origin !== origin || u.pathname.startsWith('/api/')) return null
    return u.pathname + u.search + u.hash
  } catch {
    return null
  }
}

/** The short label for a link into this app ("Open in Notes"), or null for any other link. */
export function appLinkLabel(href: string | undefined | null, origin?: string): string | null {
  const path = appPath(href, origin)
  if (path == null) return null
  const hit = AREAS.find(([re]) => re.test(path))
  return hit ? hit[1]() : null
}

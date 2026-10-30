// Phone calendars (backend calendar/phonecal.py): busy times the Android app sends from calendars on
// the phone. They show as "Busy" blocks in the calendar's colour, read-only, with the device and
// when it last sent them; after a day without an update they are marked stale (still shown).

import { formatRelative } from '../../lib/format'
import { t } from '../../i18n'
import type { Collection, PhoneInfo } from './types'

/** After this long without an update a phone calendar is stale (the server's STALE_AFTER). */
export const PHONE_STALE_MS = 24 * 3600_000

export function isStale(p: Pick<PhoneInfo, 'synced_at' | 'stale'>, now: Date = new Date()): boolean {
  if (!p.synced_at) return true
  const at = new Date(p.synced_at).getTime()
  if (Number.isNaN(at)) return !!p.stale
  return now.getTime() - at > PHONE_STALE_MS
}

/** "Updated from Pixel 9 3 minutes ago", and whether it is stale; null for any other calendar. */
export function phoneFreshness(c: Pick<Collection, 'phone'> | null | undefined, now: Date = new Date()): { text: string; stale: boolean } | null {
  const p = c?.phone
  if (!p) return null
  const when = p.synced_at ? formatRelative(p.synced_at, now) : ''
  const text = !when ? t('calendar.phone.neverUpdated')
    : p.device ? t('calendar.phone.updatedFrom', { device: p.device, when }) : t('calendar.phone.updated', { when })
  return { text, stale: isStale(p, now) }
}

/** The title of a busy block, in the app's language. */
export function busyTitle(): string {
  return t('calendar.phone.busy')
}

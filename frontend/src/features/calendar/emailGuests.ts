// event.email_guests: compose to the guests, all of them (the default), those who
// said yes, or those who have not answered. My own addresses are left out, and each address
// appears once. The organizer is one of "all" when it is someone else.

import type { ApiEvent } from './types'
import { t } from '../../i18n'

export type GuestGroup = 'all' | 'yes' | 'waiting'

export const GUEST_GROUPS: { id: GuestGroup; label: string }[] = [
  { id: 'all', label: t('calendar.emailGuests.allGuests') },
  { id: 'yes', label: t('calendar.emailGuests.guestsWhoSaidYes') },
  { id: 'waiting', label: t('calendar.emailGuests.guestsWhoHaveNotAnswered') },
]

type Guests = Pick<ApiEvent, 'organizer' | 'organizer_name' | 'attendee_list'>

const answer = (p: string | undefined) => (p ?? 'NEEDS-ACTION').toUpperCase()

/** The recipients for one group, without my addresses and without repeats. */
export function guestRecipients(e: Guests, which: GuestGroup, mine: string[]): { email: string; name: string }[] {
  const me = new Set(mine.filter(Boolean).map((x) => x.toLowerCase()))
  const guests = (e.attendee_list ?? []).filter((a) =>
    which === 'all' ? true : which === 'yes' ? answer(a.partstat) === 'ACCEPTED' : answer(a.partstat) === 'NEEDS-ACTION')
  const people = [
    ...(which === 'all' && e.organizer ? [{ email: e.organizer, name: e.organizer_name ?? '' }] : []),
    ...guests.map((a) => ({ email: a.email, name: a.name ?? '' })),
  ]
  const seen = new Set<string>()
  return people.filter((p) => {
    const k = p.email.toLowerCase()
    if (!k || me.has(k) || seen.has(k)) return false
    seen.add(k)
    return true
  })
}

/** How many each group would reach, for the menu ("Guests who said yes · 2"). */
export function groupCounts(e: Guests, mine: string[]): Record<GuestGroup, number> {
  return {
    all: guestRecipients(e, 'all', mine).length,
    yes: guestRecipients(e, 'yes', mine).length,
    waiting: guestRecipients(e, 'waiting', mine).length,
  }
}

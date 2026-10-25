// Find a time with household members (backend calendar/household.py): the busy times of
// the people in my groups who share their free/busy, from their built-in calendars. Only busy
// times come back: no titles, places or guests.

import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { utcIsoformat } from '../../lib/dates'
import { isMissingRoute } from './data'

export interface HouseholdPerson {
  id: number
  name: string
  email: string
  busy: { start: string; end: string }[]
  tentative: { start: string; end: string }[]
}

export function useHousehold(from: Date, to: Date) {
  return useQuery<{ people: HouseholdPerson[]; sharing: boolean }>({
    queryKey: ['calendar', 'household', utcIsoformat(from), utcIsoformat(to)],
    queryFn: async ({ signal }) => {
      try {
        // ACT event.find_time_household @web.editor @web.calendar
        return await api.get<{ people: HouseholdPerson[]; sharing: boolean }>('/calendar/household/freebusy', { from: utcIsoformat(from), to: utcIsoformat(to) }, signal)
      } catch (e) {
        if (isMissingRoute(e)) return { people: [], sharing: false }
        throw e
      }
    },
    staleTime: 60_000,
    retry: false,
  })
}

/** The household people a guest list names (by address), chosen at once in the event editor. */
export function guestsInHousehold(people: HouseholdPerson[], guests: { email: string }[]): number[] {
  const want = new Set(guests.map((g) => g.email.toLowerCase()))
  return people.filter((p) => want.has(p.email.toLowerCase())).map((p) => p.id)
}

/** Busy intervals of the chosen people, as Dates for the local ranking. */
export function chosenBusy(people: HouseholdPerson[], chosen: number[]): { start: Date; end: Date }[] {
  return people.filter((p) => chosen.includes(p.id)).flatMap((p) => [...p.busy, ...p.tentative].map((b) => ({ start: new Date(b.start), end: new Date(b.end) })))
}

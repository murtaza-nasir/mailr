// Postal addresses from People as options of the event editor's location field:
// the guests' addresses first, then those of the people you correspond with most.

import { usePlaces } from './api'

// ACT event.suggest_location @web.calendar
export function PlaceOptions({ guests, skip }: { guests: { email: string }[]; skip?: (string | null | undefined)[] }) {
  const places = usePlaces(guests.map((g) => g.email)).data ?? []
  const seen = new Set((skip ?? []).filter(Boolean).map((s) => s!.toLowerCase()))
  return (
    <>
      {places.filter((p) => !seen.has(p.text.toLowerCase())).map((p) => (
        <option key={`p:${p.email}:${p.text}`} value={p.text} label={`${p.text} (${p.name || p.email})`} data-testid="place-option" />
      ))}
    </>
  )
}

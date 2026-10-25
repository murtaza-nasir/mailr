// What every view of the Calendar app shares, provided once by CalendarApp: preferences, the
// loaded events and collections, the actions, and how to open things.

import { createContext, useContext } from 'react'
import type { ISODate } from '../../lib/dates'
import type { CalendarView } from '../../lib/routes'
import type { CalActions } from './actions'
import type { Busy } from './geometry'
import type { CalPrefs } from './prefs'
import type { ApiTask, CalEvent, Collection } from './types'
import { t } from '../../i18n'

export interface CalCtx {
  prefs: CalPrefs
  dark: boolean
  view: CalendarView
  date: ISODate
  today: ISODate
  now: Date
  events: CalEvent[]
  tasks: ApiTask[]
  busy: Busy[]
  collections: Collection[]
  collectionByUrl: Map<string, Collection>
  /** Calendars not shown (by URL). */
  hidden: Set<string>
  colourOf: (c: Collection | undefined) => string
  actions: CalActions
  /** Writes are off (offline). */
  readOnly: boolean
  /** The range is loading (the old range stays, dimmed). */
  loading: boolean
  /** Open the detail for an event, anchored to the element it was opened from. */
  openEvent: (e: CalEvent, anchor?: Element | null) => void
  /** Go to a view and date. */
  go: (view: CalendarView, date?: ISODate | null) => void
  narrow: boolean
  /** The event that takes the one Tab stop into the events: the focused one, else the first
   *  in time order. Arrows move between events; Tab leaves them. */
  tabStop: string | null
}

export const CalContext = createContext<CalCtx | null>(null)

export function useCal(): CalCtx {
  const c = useContext(CalContext)
  if (!c) throw new Error(t('calendar.context.usecalOutsideCalendarapp'))
  return c
}

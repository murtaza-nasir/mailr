// The Calendar app's context outside the app (features/calendar/context.ts), so the side panel and
// the reader can use the app's own rows, detail, editor and prompts: one range of events, the
// collections, the preferences and the actions. When the Calendar app itself is on screen it already
// draws the editor and the prompts, so the host leaves them out.

import { useCallback, useEffect, useId, useMemo, type ReactNode } from 'react'
import { create } from 'zustand'
import { useNavigate } from 'react-router'
import { useAppRoute } from '../../hooks/useAppRoute'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useTheme } from '../../hooks/useTheme'
import { todayISO, type ISODate } from '../../lib/dates'
import { calendarPath, eventPath } from '../../lib/routes'
import { busyOf, useCalActions } from '../calendar/actions'
import { CalContext, type CalCtx } from '../calendar/context'
import { collectionColour, useCollections, useNormalized, useRange } from '../calendar/data'
import { LazyEventEditor as EventEditor } from '../calendar/LazyEventEditor'
import { useCalPrefs } from '../calendar/prefs'
import { Prompts } from '../calendar/Prompts'
import { useHiddenCalendars } from '../calendar/visibility'
import { useCalUI, type EditorInit } from '../calendar/store'
import type { CalEvent, Collection } from '../calendar/types'
import '../calendar/calendar.css'
import { useNow } from '../../hooks/useNow'
import { eventOrder, tabStopOf } from '../calendar/eventNav'


export interface CalHostProps {
  /** The range to load: [from, to). */
  from: Date
  to: Date
  date: ISODate
  /** Opens an event from a row (the panel shows the app's detail at the row). */
  onOpen?: (e: CalEvent, anchor?: Element | null) => void
  children: ReactNode
}

export function CalHost({ from, to, date, onOpen, children }: CalHostProps) {
  const nav = useNavigate()
  const prefs = useCalPrefs()
  const dark = useTheme() === 'dark'
  const narrow = useMediaQuery('(max-width: 720px)')
  const now = useNow()
  const cols = useCollections()
  const collections = useMemo(() => cols.data ?? [], [cols.data])
  const collectionByUrl = useMemo(() => new Map(collections.map((c) => [c.url ?? c.id, c])), [collections])
  // The same hidden set as the calendar app, pending clicks included (calendar/visibility.ts).
  const hidden = useHiddenCalendars(collections)
  const range = useRange(from, to)
  const events = useNormalized(range.data?.events, prefs.zone, collections, hidden)
  const busy = useMemo(() => busyOf(events, (url?: string) => (url ? collectionByUrl.get(url)?.busy !== false : true)), [events, collectionByUrl])
  const actions = useCalActions(prefs, busy)
  const colourOf = useCallback((c: Collection | undefined) => collectionColour(c, c ? collections.indexOf(c) : 0), [collections])

  const ctx: CalCtx = useMemo(() => ({
    prefs, dark, view: 'day', date, today: todayISO(prefs.zone, now), now, events, tasks: [], busy, collections, collectionByUrl, hidden,
    colourOf, actions, readOnly: typeof navigator !== 'undefined' && !navigator.onLine, loading: range.isFetching,
    openEvent: onOpen ?? ((e: CalEvent) => nav(eventPath(e.uid, e.occurrence))),
    go: (v, d) => nav(calendarPath(v, d ?? null)),
    narrow,
    // One Tab stop among the day's events (the first; arrows move between them).
    tabStop: tabStopOf(eventOrder(events, [date]), null),
  }), [prefs, dark, date, now, events, busy, collections, collectionByUrl, hidden, colourOf, actions, range.isFetching, onOpen, nav, narrow])

  return <CalContext.Provider value={ctx}>{children}</CalContext.Provider>
}

/** Editors a caller draws inside its own host (the reader's "Schedule a meeting"); the overlays skip them. */
export const claimedEditors = new WeakSet<EditorInit>()

const useOverlayOwners = create<{ ids: string[]; add: (id: string) => void; remove: (id: string) => void }>((set) => ({
  ids: [],
  add: (id) => set((s) => ({ ids: [...s.ids, id] })),
  remove: (id) => set((s) => ({ ids: s.ids.filter((x) => x !== id) })),
}))

/**
 * The app's editor and prompts, drawn here when the Calendar app is not on screen. Several hosts
 * may mount this; the first one mounted draws them.
 */
export function CalOverlays() {
  const id = useId()
  const owners = useOverlayOwners((s) => s.ids)
  useEffect(() => {
    useOverlayOwners.getState().add(id)
    return () => useOverlayOwners.getState().remove(id)
  }, [id])
  const app = useAppRoute().app
  const editor = useCalUI((s) => s.editor)
  if (app === 'calendar' || owners[0] !== id) return null
  return (
    <>
      {editor && !claimedEditors.has(editor) && <EventEditor key={editor.event?.key ?? 'new'} init={editor} mode="modal" />}
      <Prompts />
    </>
  )
}

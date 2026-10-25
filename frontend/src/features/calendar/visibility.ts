// Calendar visibility (calendar.toggle_visibility, calendar.show_only, "Show all") from every
// surface: the calendar app's left column and all-hidden banner, the calendar panel, the palette.
// The server keeps visibility when it can (synced across devices); otherwise this device does.

import { useMemo, useSyncExternalStore } from 'react'
import type { QueryClient } from '@tanstack/react-query'
import { qk } from '../../api/hooks'
import { t } from '../../i18n'
import { toastError } from '../../stores/ui'
import { calApi } from './data'
import { useSavedView } from './prefs'
import type { Collection } from './types'
import { hiddenCalendars, visibilityIntents } from './visibilityIntents'

const urlOf = (c: Collection) => c.url ?? c.id

/** The pending visibility choices (visibilityIntents.ts), for the hidden set on screen. */
export function usePendingVisibility(): ReadonlyMap<string, boolean> {
  return useSyncExternalStore(visibilityIntents.subscribe, visibilityIntents.getSnapshot)
}

/**
 * The hidden calendars (by URL) every surface shows: the calendar app, the calendar panel, the
 * palette. One derivation, so a click on any of them shows the same state on all of them.
 */
export function useHiddenCalendars(collections: Collection[] | undefined): Set<string> {
  const hiddenLocal = useSavedView((s) => s.hiddenLocal)
  const pending = usePendingVisibility()
  return useMemo(() => hiddenCalendars(collections ?? [], hiddenLocal, pending), [collections, hiddenLocal, pending])
}

/**
 * Hide or show calendars: on screen at once, then saved one calendar after another. The saves
 * also wait for `after` when given.
 */
export function saveVisibility(qc: QueryClient, targets: Collection[], hide: boolean, after?: Promise<unknown>): Promise<void> {
  const refresh = () => qc.invalidateQueries({ queryKey: qk.calendar.all })
  return Promise.all(targets.map((c) => visibilityIntents.set(urlOf(c), hide, async () => {
    const url = urlOf(c)
    try {
      const out = await calApi.patchCollection(c, { hidden: hide })
      // This device keeps the choice only when the server cannot.
      const local = new Set(useSavedView.getState().hiddenLocal)
      if (hide && !out) local.add(url)
      else local.delete(url)
      useSavedView.getState().set({ hiddenLocal: [...local] })
      if (out) qc.setQueryData<Collection[]>(qk.calendar.calendars, (old) => old?.map((x) => (urlOf(x) === url ? { ...x, hidden: out.hidden ?? hide } : x)))
    } catch (e) {
      toastError(e, t('calendar.leftColumn.couldNotChangeTheCalendar'))
    }
  }, refresh, after))).then(() => undefined)
}

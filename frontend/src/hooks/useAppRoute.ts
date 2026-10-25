import { useMemo } from 'react'
import { useLocation } from 'react-router'
import { appFromPath, parseCalendarPath, parseNotesPath, parseRecordingsPath, parseTasksPath, type AppId, type CalendarRoute, type NotesRoute, type RecordingsRoute, type TasksRoute } from '../lib/routes'

export interface AppRoute {
  /** Which app the URL belongs to: the route discriminator shells and keys read. */
  app: AppId
  /** The parsed /calendar/* URL (null outside the calendar). */
  calendar: CalendarRoute | null
  /** The parsed /tasks/* URL (null outside tasks). */
  tasks: TasksRoute | null
  /** The parsed /recordings/* URL (null outside recordings). */
  recordings: RecordingsRoute | null
  /** The parsed /notes/* URL (null outside notes). */
  notes: NotesRoute | null
}

/** The current app and, inside Calendar or Tasks, the parsed URL (lib/routes.ts). */
export function useAppRoute(): AppRoute {
  const { pathname, search, hash } = useLocation()
  return useMemo(
    () => ({
      app: appFromPath(pathname), calendar: parseCalendarPath(pathname, search), tasks: parseTasksPath(pathname, search),
      recordings: parseRecordingsPath(pathname, search), notes: parseNotesPath(pathname, search, hash),
    }),
    [pathname, search, hash],
  )
}

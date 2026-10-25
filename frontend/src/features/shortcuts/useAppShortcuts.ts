// How the Calendar and Tasks apps run their keys. The keys themselves live in lib/shortcuts.ts
// (CALENDAR_BINDINGS, TASK_BINDINGS), so the help overlay and the palette show them; an app
// mounts this hook with a handler per action it supports:
//
//   useAppShortcuts('calendar', {
//     'calendar.today': () => goToday(),
//     'calendar.goToDate': (firstChar) => openGoToDate(firstChar),
//     'calendar.nextEvent': () => (focusNext() ? undefined : false), // false: the key stays the browser's
//   })
//
// Handlers see the latest render's closures. The global listener (useGlobalShortcuts) ignores
// keys typed in fields and keys a focused component already handled (event.preventDefault()).

import { useEffect, useRef } from 'react'
import {
  setShortcutHandlers, type AppScope, type CalendarShortcut, type NoteShortcut, type RecordingShortcut, type ShortcutHandler, type TaskShortcut,
} from '../../lib/shortcuts'

type HandlersFor<S extends AppScope> = S extends 'calendar'
  ? Partial<Record<CalendarShortcut, ShortcutHandler>>
  : S extends 'tasks'
    ? Partial<Record<TaskShortcut, ShortcutHandler>>
    : S extends 'recordings'
      ? Partial<Record<RecordingShortcut, ShortcutHandler>>
      : S extends 'notes'
        ? Partial<Record<NoteShortcut, ShortcutHandler>>
        : never

export function useAppShortcuts<S extends Exclude<AppScope, 'mail' | 'settings'>>(scope: S, handlers: HandlersFor<S>): void {
  const ref = useRef(handlers)
  useEffect(() => {
    ref.current = handlers
  })
  useEffect(() => setShortcutHandlers(scope, () => ref.current), [scope])
}

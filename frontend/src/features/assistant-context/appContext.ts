// What the Calendar and Tasks apps show, for the assistant (backend/mailr/agent/screen.py): sent
// with each chat turn beside the mail screen state, so "this meeting" and "these tasks" resolve.
// Ids only; the server reads titles and times from its mirror. Read from the apps' own stores and
// the URL at send time, so the apps need no extra wiring.

import { useCalUI } from '../calendar/store'
import { useSavedView } from '../calendar/prefs'
import { useTasksUI } from '../tasks/store'
import { useNotesUI } from '../notes/store'
import { useTableUI } from '../tables/store'
import { parseCalendarPath, parseNotesPath, parseTablePath, parseTasksPath, taskListSegment } from '../../lib/routes'

const MAX_SELECTED = 50

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** `calendar`, `tasks`, `notes` or `table` for the turn's `ui`, from the path the user is on (empty elsewhere). */
export function appContext(pathname: string = window.location.pathname, search: string = window.location.search,
  hash: string = typeof window === 'undefined' ? '' : window.location.hash):
  { calendar?: Record<string, unknown>; tasks?: Record<string, unknown>; notes?: Record<string, unknown>; table?: Record<string, unknown> } {
  const cal = parseCalendarPath(pathname, search)
  if (cal) return { calendar: calendarContext(cal) }
  const tasks = parseTasksPath(pathname, search)
  if (tasks) return { tasks: tasksContext(tasks) }
  const table = parseTablePath(pathname)
  if (table) return { table: tableContext(table) }
  const notes = parseNotesPath(pathname, search, hash)
  if (notes) return { notes: notesContext(notes) }
  return {}
}

/** The Notes app (agent/screen.py `notes`): the view, the open note and the item focused in it, and the
 *  cards selected in the grid. Ids only. */
function notesContext(route: NonNullable<ReturnType<typeof parseNotesPath>>): Record<string, unknown> {
  const ui = useNotesUI.getState()
  const out: Record<string, unknown> = { view: route.view }
  if (route.label) out.label = route.label
  if (route.q) out.q = route.q
  if (route.open) {
    out.focused = route.open
    const item = route.item ?? (ui.focusedItem?.note === route.open ? ui.focusedItem.item : null)
    if (item) out.focused_item = item
  }
  const selected = [...ui.selected].slice(0, MAX_SELECTED)
  if (selected.length) out.selected = selected
  return out
}

/** The open table: "this row", "these rows" and "this column". */
function tableContext(route: NonNullable<ReturnType<typeof parseTablePath>>): Record<string, unknown> {
  const s = useTableUI.getState()
  const same = s.id === route.id
  return {
    id: route.id, view: (same ? s.view : null) ?? route.view, filters: same ? s.filters : [], sort: same ? s.sort : [],
    focused_row: route.row ?? (same ? s.focused_row : null), focused_column: same ? s.focused_column : null,
    selected_rows: same ? s.selected_rows.slice(0, MAX_SELECTED) : [],
  }
}

function calendarContext(route: NonNullable<ReturnType<typeof parseCalendarPath>>): Record<string, unknown> {
  const saved = useSavedView.getState()
  const ui = useCalUI.getState()
  const out: Record<string, unknown> = {
    view: route.event ? 'event' : route.search !== null ? 'search' : (route.view ?? saved.view),
    ndays: saved.ndays,
  }
  if (route.date) out.date = route.date
  if (route.search) out.q = route.search
  if (saved.displayZone) out.tz = saved.displayZone
  if (saved.hiddenLocal.length) out.hidden = saved.hiddenLocal.slice(0, 50)
  // The focused event: the open deep link, else the event focused in the grid (key `${uid}|${occ}`).
  if (route.event) out.focused_event = { uid: route.event.uid, occurrence: route.event.occ || null }
  else if (ui.focus) {
    const at = ui.focus.lastIndexOf('|')
    if (at > 0) out.focused_event = { uid: ui.focus.slice(0, at), occurrence: ui.focus.slice(at + 1) || null }
  }
  if (ui.slot) {
    const m = ui.slot.min
    out.selected_slot = { start: `${ui.slot.day}T${pad(Math.floor(m / 60))}:${pad(m % 60)}`, all_day: false }
  }
  return out
}

function tasksContext(route: NonNullable<ReturnType<typeof parseTasksPath>>): Record<string, unknown> {
  const ui = useTasksUI.getState()
  const out: Record<string, unknown> = {}
  const list = route.list
  if (list?.kind === 'smart') out.smart = list.id
  else if (list?.kind === 'list') out.list = list.id
  else if (list?.kind === 'tag') out.tag = list.id
  if (list) {
    const sort = ui.prefs[taskListSegment(list)]?.sort
    if (sort) out.sort = sort
  }
  if (ui.search) out.q = ui.search
  const focused = route.taskUid ?? ui.focused
  if (focused) out.focused = focused
  const selected = [...ui.selected].filter((u) => !ui.hidden.has(u)).slice(0, MAX_SELECTED)
  if (selected.length) out.selected = selected
  return out
}

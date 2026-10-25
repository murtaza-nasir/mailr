// The app routes (plan §3, product spec §3.0): which app a URL belongs to, and the Calendar and
// Tasks paths, parsed and built in one place. router.tsx draws the routes; shells, keys and
// the apps use these helpers so a link always has one spelling.
//
//   /mail/:view[/:conv]                 mail
//   /calendar[/:view[/:date]]           calendar (view: today, day, ndays, workweek, week, month, schedule)
//   /calendar/event/:uid/:occ           one occurrence (occ = canonical occurrence string)
//   /calendar/search?q=                 calendar search
//   /tasks[/<list>]                     tasks (list: today, upcoming, l/<id>, t/<tag>, …)
//   /tasks[/<list>]/task/:uid           one task, over its list (or over the default list)
//   /tasks/search?q=<query>              a task search (opens All with the query in the search box)
//   /recordings[/<filter>]              recordings (filter: recent, linked, unlinked, f/<folder>, t/<tag>)
//   /recordings/<cid>/<rid>[?t=<s>]     one recording, open in the app's sheet
//   /notes[/<view>]                     notes (view: lists, tables, archive, trash, shared, label/<name>)
//   /notes/search?q=<query>             a notes search
//   /notes/n/<id>[#i-<item>]            one note open over its grid (an item focused)
//   /tables/t/<id>[/v/<view>][/r/<row>] one table (part of the Notes app), a view, a row's form
//   /settings/:group/:page              settings (features/settings/groups.ts)

export type AppId = 'mail' | 'calendar' | 'tasks' | 'recordings' | 'notes' | 'settings'

export const CALENDAR_VIEWS = ['today', 'day', 'ndays', 'workweek', 'week', 'month', 'schedule'] as const
export type CalendarView = (typeof CALENDAR_VIEWS)[number]

export function appFromPath(pathname: string): AppId {
  const seg = pathname.split('/')[1] ?? ''
  if (seg === 'calendar' || seg === 'tasks' || seg === 'recordings' || seg === 'notes' || seg === 'settings') return seg
  // A table is part of the Notes app.
  if (seg === 'tables') return 'notes'
  return 'mail'
}

const enc = encodeURIComponent
const dec = (s: string) => {
  try {
    return decodeURIComponent(s)
  } catch {
    return s
  }
}

// ---------- calendar ----------

export interface CalendarRoute {
  /** The view in the URL; null when the URL names none (the app picks the saved or default view). */
  view: CalendarView | null
  /** The date in the URL (YYYY-MM-DD); null means today. */
  date: string | null
  /** An occurrence deep link: /calendar/event/:uid/:occ. */
  event: { uid: string; occ: string } | null
  /** /calendar/search?q= */
  search: string | null
}

export function isCalendarView(v: string | null | undefined): v is CalendarView {
  return !!v && (CALENDAR_VIEWS as readonly string[]).includes(v)
}

/** Null when the path is not a calendar path. Unknown views and bad dates read as absent. */
export function parseCalendarPath(pathname: string, search = ''): CalendarRoute | null {
  const parts = pathname.split('/').filter(Boolean)
  if (parts[0] !== 'calendar') return null
  const out: CalendarRoute = { view: null, date: null, event: null, search: null }
  if (parts[1] === 'event' && parts[2]) {
    out.event = { uid: dec(parts[2]), occ: dec(parts[3] ?? '') }
    return out
  }
  if (parts[1] === 'search') {
    out.search = new URLSearchParams(search).get('q') ?? ''
    return out
  }
  if (isCalendarView(parts[1])) out.view = parts[1]
  const date = parts[out.view ? 2 : 1]
  if (date && /^\d{4}-\d{2}-\d{2}$/.test(date)) out.date = date
  return out
}

/** /calendar, /calendar/week, /calendar/week/2026-10-01. */
export function calendarPath(view?: CalendarView | null, date?: string | null): string {
  if (!view) return '/calendar'
  return date ? `/calendar/${view}/${date}` : `/calendar/${view}`
}

/** The deep link to one occurrence; `occ` is the canonical occurrence string (lib/dates). */
export function eventPath(uid: string, occ: string): string {
  return `/calendar/event/${enc(uid)}/${enc(occ)}`
}

export function calendarSearchPath(q: string): string {
  return `/calendar/search?q=${enc(q)}`
}

// ---------- tasks ----------

/** Smart lists the server defines (GET /tasks?smart=). */
export const SMART_LISTS = ['today', 'upcoming', 'scheduled', 'nodate', 'high', 'email', 'waiting', 'all', 'completed'] as const
export type SmartList = (typeof SMART_LISTS)[number]

export type TaskListRef =
  | { kind: 'smart'; id: SmartList }
  | { kind: 'list'; id: string }
  | { kind: 'tag'; id: string }

export interface TasksRoute {
  /** The list in the URL; null when the URL names none (the app picks the saved or default list). */
  list: TaskListRef | null
  /** /tasks/task/:uid (or /tasks/<list>/task/:uid): the task open in the pane. */
  taskUid: string | null
  /** /tasks/search?q=: the query (null on every other path). */
  search?: string | null
}

function listFromParts(parts: string[]): TaskListRef | null {
  if (!parts.length) return null
  if (parts[0] === 'l' && parts[1]) return { kind: 'list', id: dec(parts[1]) }
  if (parts[0] === 't' && parts[1]) return { kind: 'tag', id: dec(parts[1]) }
  if ((SMART_LISTS as readonly string[]).includes(parts[0])) return { kind: 'smart', id: parts[0] as SmartList }
  return null
}

export function parseTasksPath(pathname: string, search = ''): TasksRoute | null {
  const parts = pathname.split('/').filter(Boolean)
  if (parts[0] !== 'tasks') return null
  if (parts[1] === 'search' && parts.length === 2) {
    return { list: { kind: 'smart', id: 'all' }, taskUid: null, search: new URLSearchParams(search).get('q') ?? '' }
  }
  const rest = parts.slice(1)
  const at = rest.length >= 2 && rest[rest.length - 2] === 'task' ? rest.length - 2 : -1
  return {
    list: listFromParts(at >= 0 ? rest.slice(0, at) : rest),
    taskUid: at >= 0 ? dec(rest[at + 1]) : null,
  }
}

export function taskListSegment(list: TaskListRef): string {
  return list.kind === 'smart' ? list.id : `${list.kind === 'list' ? 'l' : 't'}/${enc(list.id)}`
}

/** /tasks, /tasks/today, /tasks/l/12, /tasks/t/phone. */
export function tasksPath(list?: TaskListRef | SmartList | null): string {
  if (!list) return '/tasks'
  const ref: TaskListRef = typeof list === 'string' ? { kind: 'smart', id: list } : list
  return `/tasks/${taskListSegment(ref)}`
}

/** A task search: /tasks/search?q=… ("See all in Tasks" from mail search). */
export function taskSearchPath(q: string): string {
  return `/tasks/search?q=${enc(q)}`
}

/** One task: /tasks/task/:uid, or over a list: /tasks/today/task/:uid. */
export function taskPath(uid: string, list?: TaskListRef | SmartList | null): string {
  return `${list ? tasksPath(list) : '/tasks'}/task/${enc(uid)}`
}

// ---------- recordings ----------

/** The left column's choices: all, the last seven days, linked to an event or not, a folder, a tag. */
export type RecordingFilter =
  | { kind: 'all' | 'recent' | 'linked' | 'unlinked' }
  | { kind: 'folder' | 'tag'; id: string }

export interface RecordingsRoute {
  filter: RecordingFilter
  /** /recordings/<cid>/<rid>: the recording open in the sheet. */
  open: { cid: number; rid: number; t: number | null } | null
  /** ?q=: the search box. */
  q: string
}

export function parseRecordingsPath(pathname: string, search = ''): RecordingsRoute | null {
  const parts = pathname.split('/').filter(Boolean)
  if (parts[0] !== 'recordings') return null
  const params = new URLSearchParams(search)
  const q = params.get('q') ?? ''
  if (parts.length >= 3 && /^\d+$/.test(parts[1]) && /^\d+$/.test(parts[2])) {
    const t = params.get('t')
    return { filter: { kind: 'all' }, open: { cid: Number(parts[1]), rid: Number(parts[2]), t: t && /^\d+(\.\d+)?$/.test(t) ? Number(t) : null }, q }
  }
  let filter: RecordingFilter = { kind: 'all' }
  if ((parts[1] === 'f' || parts[1] === 't') && parts[2]) filter = { kind: parts[1] === 'f' ? 'folder' : 'tag', id: dec(parts[2]) }
  else if (parts[1] === 'recent' || parts[1] === 'linked' || parts[1] === 'unlinked') filter = { kind: parts[1] }
  return { filter, open: null, q }
}

/** /recordings, /recordings/recent, /recordings/f/Advising, /recordings/t/Work (with ?q=). */
export function recordingsPath(filter?: RecordingFilter | null, q?: string): string {
  const base = !filter || filter.kind === 'all' ? '/recordings'
    : filter.kind === 'folder' || filter.kind === 'tag' ? `/recordings/${filter.kind === 'folder' ? 'f' : 't'}/${enc(filter.id)}`
      : `/recordings/${filter.kind}`
  return q ? `${base}?q=${enc(q)}` : base
}

// ---------- notes ----------

/** The views of the Notes app. */
export type NotesView = 'all' | 'lists' | 'tables' | 'archive' | 'trash' | 'shared' | 'reminders' | 'label' | 'search'

export interface NotesRoute {
  view: NotesView
  /** view 'label': the label's name. */
  label: string | null
  /** view 'search': the query. */
  q: string
  /** /notes/n/<id>: the note open over the grid. */
  open: string | null
  /** #i-<item>: the item to focus in the open note. */
  item: string | null
}

const NOTE_VIEWS = new Set(['lists', 'tables', 'archive', 'trash', 'shared', 'reminders'])

export function parseNotesPath(pathname: string, search = '', hash = ''): NotesRoute | null {
  const parts = pathname.split('/').filter(Boolean)
  if (parts[0] !== 'notes') return null
  const q = new URLSearchParams(search).get('q') ?? ''
  const item = /^#i-(.+)$/.exec(hash)?.[1] ?? null
  if (parts[1] === 'n' && parts[2]) return { view: 'all', label: null, q: '', open: dec(parts[2]), item: item && dec(item) }
  if (parts[1] === 'label' && parts[2]) return { view: 'label', label: dec(parts[2]), q: '', open: null, item: null }
  if (parts[1] === 'search') return { view: 'search', label: null, q, open: null, item: null }
  if (parts[1] && NOTE_VIEWS.has(parts[1])) return { view: parts[1] as NotesView, label: null, q: '', open: null, item: null }
  return { view: 'all', label: null, q: '', open: null, item: null }
}

/** /notes, /notes/lists, /notes/label/Home, /notes/search?q=… */
export function notesPath(view: NotesView = 'all', arg?: string | null): string {
  if (view === 'all') return '/notes'
  if (view === 'label') return `/notes/label/${enc(arg ?? '')}`
  if (view === 'search') return `/notes/search?q=${enc(arg ?? '')}`
  return `/notes/${view}`
}

/** One note (and an item in it): /notes/n/<id>#i-<item>. */
export function notePath(id: string, item?: string | null): string {
  return `/notes/n/${enc(id)}${item ? `#i-${enc(item)}` : ''}`
}

// ---------- tables (web.table) ----------

export interface TableRoute {
  id: string
  /** /v/<view>: a saved view other than the default. */
  view: string | null
  /** /r/<row>: the row form open over the grid. */
  row: string | null
  /** /add: the members-only "Add a row" form on its own (features/tables/AddRowPage.tsx). */
  form?: true
}

export function parseTablePath(pathname: string): TableRoute | null {
  const parts = pathname.split('/').filter(Boolean)
  if (parts[0] !== 'tables' || parts[1] !== 't' || !parts[2]) return null
  const out: TableRoute = { id: dec(parts[2]), view: null, row: null }
  if (parts[3] === 'add') return { ...out, form: true }
  for (let i = 3; i + 1 < parts.length; i += 2) {
    if (parts[i] === 'v') out.view = dec(parts[i + 1])
    else if (parts[i] === 'r') out.row = dec(parts[i + 1])
  }
  return out
}

/** The members-only "Add a row" form of a table: /tables/t/<id>/add (never public; sign-in first). */
export function tableFormPath(id: string): string {
  return `/tables/t/${enc(id)}/add`
}

/** /tables/t/<id>, /tables/t/<id>/v/<view>, /tables/t/<id>/r/<row> (both: view first). */
export function tableRoutePath(id: string, view?: string | null, row?: string | null): string {
  return `/tables/t/${enc(id)}${view ? `/v/${enc(view)}` : ''}${row ? `/r/${enc(row)}` : ''}`
}

// ---------- last place in each app ----------

const HOMES: Record<AppId, string> = { mail: '/mail/inbox', calendar: '/calendar', tasks: '/tasks', recordings: '/recordings', notes: '/notes', settings: '/settings' }
const last: Partial<Record<AppId, string>> = {}

/** Note where the user is, so going back to an app returns there (AppShell calls this on every route). */
export function rememberAppPath(pathname: string, search = ''): void {
  const app = appFromPath(pathname)
  // A conversation or a single event/task is not a place to come back to; its list or view is.
  if (app === 'mail') last.mail = pathname.replace(/^(\/mail\/[^/]+)\/.*/, '$1') + (search.includes('q=') ? search : '')
  else if (app === 'calendar') last.calendar = pathname.startsWith('/calendar/event/') ? last.calendar : pathname + (pathname.startsWith('/calendar/search') ? search : '')
  else if (app === 'tasks') last.tasks = pathname.replace(/\/task\/[^/]+$/, '') || '/tasks'
  // One recording is not a place to come back to; its list is.
  else if (app === 'recordings') last.recordings = /^\/recordings\/\d+\/\d+/.test(pathname) ? (last.recordings ?? '/recordings') : pathname + search
  // One note is not a place to come back to; its grid is.
  // A table is a place to come back to; its row form is not.
  else if (app === 'notes' && pathname.startsWith('/tables/')) last.notes = pathname.replace(/\/r\/[^/]+$/, '')
  else if (app === 'notes') last.notes = pathname.startsWith('/notes/n/') ? (last.notes ?? '/notes') : pathname + (pathname.startsWith('/notes/search') ? search : '')
  else last.settings = pathname
}

/** Where a nav entry for an app goes: the last place in it this session, else its start. */
export function appHome(app: AppId): string {
  return last[app] ?? HOMES[app]
}

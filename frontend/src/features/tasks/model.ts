// The Tasks app's pure logic: the task shape of the API contract,
// the smart lists as a client-side fallback for the server's
// GET /tasks?smart=, sections and order per view, due chips, search operators, counts and the
// inverse of a bulk change (one undo for the batch). No React here; model.test.ts covers it.

import {
  addDaysISO, diffDaysISO, formatDate, formatTime, parseISO, startOfWeekISO, toISODate, todayISO,
  type DatePrefs, type ISODate,
} from '../../lib/dates'
import type { SmartList, TaskListRef } from '../../lib/routes'
import { t as tr } from '../../i18n'

// ---------------------------------------------------------------- the task

export interface TaskItem {
  id?: number
  uid: string
  summary: string
  notes?: string | null
  status: string
  completed?: boolean
  completed_at?: string | null
  due: string | null
  due_all_day?: boolean
  start?: string | null
  start_all_day?: boolean
  tz?: string | null
  /** iCalendar: 0 none, 1-4 high, 5 medium, 6-9 low. */
  priority?: number
  recurring?: boolean
  rrule?: string | null
  repeat_from?: 'due' | 'completion' | string
  categories?: string[]
  sort_order?: number | null
  parent?: string | null
  subtask_count?: number
  open_subtask_count?: number
  reminders?: number[]
  /** Absolute reminders, as UTC instants (the task API's `reminders_at`). */
  reminders_at?: string[]
  list?: string
  list_id?: number
  connector_id?: number
  href?: string
  etag?: string | null
  /** The live version (lib/live): an editor's base_version. */
  version?: number | null
  created?: string | null
  last_modified?: string | null
  conversation_id?: number | string | null
  local_only?: boolean
}

/** A task list (a VTODO collection). */
export interface TaskList {
  id: string
  url?: string
  calendar_id?: number
  connector_id: number
  name: string
  color?: string | null
  tasks?: boolean
  read_only?: boolean
  hidden?: boolean
  sort_order?: number
}

export interface TagInfo {
  name: string
  count: number
  color?: string | null
  order?: number
}

export const isDone = (t: TaskItem) => t.completed === true || t.status === 'completed'

// ---------------------------------------------------------------- priority

/** The four levels the UI offers (keys 0-3). */
export type PriorityLevel = 0 | 1 | 2 | 3
export const PRIORITY_NAMES = [tr('common.none'), tr('common.low'), tr('common.medium'), tr('common.high')] as const

/** iCalendar PRIORITY → level: 1-4 High, 5 Medium, 6-9 Low, 0 None. */
export function priorityLevel(p: number | null | undefined): PriorityLevel {
  if (!p) return 0
  if (p <= 4) return 3
  if (p === 5) return 2
  return 1
}

/** Level → the iCalendar value mailr writes (High 1, Medium 5, Low 9, None 0). */
export function icalPriority(level: PriorityLevel): number {
  return [0, 9, 5, 1][level]
}

// ---------------------------------------------------------------- days

/** The calendar day a due or start falls on in the user's zone; null when unset. */
export function dayOf(value: string | null | undefined, allDay: boolean | undefined, zone: string): ISODate | null {
  if (!value) return null
  if (allDay || /^\d{4}-\d{2}-\d{2}$/.test(value)) return value.slice(0, 10)
  const d = parseISO(value, zone)
  return d ? toISODate(d, zone) : null
}

export const dueDay = (t: TaskItem, zone: string) => dayOf(t.due, t.due_all_day, zone)
export const startDay = (t: TaskItem, zone: string) => dayOf(t.start, t.start_all_day, zone)

/** The due as an instant (a date-only due is the end of its day, so it is not overdue until then). */
export function dueInstant(t: TaskItem, zone: string): Date | null {
  if (!t.due) return null
  if (t.due_all_day || /^\d{4}-\d{2}-\d{2}$/.test(t.due)) return parseISO(addDaysISO(t.due.slice(0, 10), 1), zone)
  return parseISO(t.due, zone)
}

/** A start in the future hides the task from Today and All (deferred). */
export function isDeferred(t: TaskItem, now: Date, zone: string): boolean {
  if (!t.start) return false
  if (t.start_all_day || /^\d{4}-\d{2}-\d{2}$/.test(t.start)) return t.start.slice(0, 10) > todayISO(zone, now)
  const s = parseISO(t.start, zone)
  return !!s && s.getTime() > now.getTime()
}

export function isOverdue(t: TaskItem, now: Date, zone: string): boolean {
  if (isDone(t) || !t.due) return false
  const d = dueInstant(t, zone)
  return !!d && d.getTime() <= now.getTime()
}

// ---------------------------------------------------------------- smart lists (client fallback)

export interface ViewOpts {
  now: Date
  zone: string
  /** "Show deferred" (Today and All). */
  showDeferred?: boolean
}

/**
 * Whether an open task belongs to a smart list. The server defines the
 * lists (GET /tasks?smart=); this mirrors its rules for when that endpoint is not there yet.
 */
export function inSmartList(t: TaskItem, list: SmartList, o: ViewOpts): boolean {
  if (list === 'completed') return isDone(t)
  if (isDone(t)) return false
  const today = todayISO(o.zone, o.now)
  const due = dueDay(t, o.zone)
  const start = startDay(t, o.zone)
  const deferred = isDeferred(t, o.now, o.zone)
  switch (list) {
    case 'today': {
      if (deferred && !o.showDeferred) return false
      if (due && due <= today) return true
      // Started (start on or before today) with no due, or a due within 7 days.
      return !!start && start <= today && (!due || diffDaysISO(today, due) <= 7)
    }
    case 'upcoming':
      return (!!due && due > today) || (!!start && start > today)
    case 'scheduled':
      return !!due
    case 'nodate':
      return !due && !start
    case 'high':
      return priorityLevel(t.priority) === 3
    case 'email':
      return t.conversation_id != null && t.conversation_id !== ''
    case 'all':
      return !deferred || !!o.showDeferred
    case 'waiting':
      return false
  }
}

/** A user list's route id: the numeric collection id when there is one, else the URL. */
export function listKey(l: { calendar_id?: number; id: string } | TaskList): string {
  return l.calendar_id != null ? String(l.calendar_id) : l.id
}

export function taskInList(t: TaskItem, listId: string, lists: TaskList[]): boolean {
  const l = lists.find((x) => listKey(x) === listId || x.id === listId)
  if (!l) return String(t.list_id ?? '') === listId || t.list === listId
  return t.list === l.id || (l.calendar_id != null && t.list_id === l.calendar_id)
}

export function hasTag(t: TaskItem, tag: string): boolean {
  const want = tag.toLowerCase()
  return (t.categories ?? []).some((c) => c.toLowerCase() === want)
}

/** The tasks a route shows, from all tasks (the fallback for every view). */
export function filterForView(all: TaskItem[], ref: TaskListRef, lists: TaskList[], o: ViewOpts & { showCompleted?: boolean }): TaskItem[] {
  if (ref.kind === 'smart') return all.filter((t) => inSmartList(t, ref.id, o))
  const base = ref.kind === 'list' ? all.filter((t) => taskInList(t, ref.id, lists)) : all.filter((t) => hasTag(t, ref.id))
  return base.filter((t) => (o.showCompleted ? true : !isDone(t)))
}

export interface Counts {
  smart: Partial<Record<SmartList | 'overdue', number>>
  lists: Record<string, number>
  tags: Record<string, number>
}

/** Open-task counts for the left column (the fallback for GET /tasks/counts). */
export function computeCounts(all: TaskItem[], o: ViewOpts): Counts {
  const smart: Counts['smart'] = { today: 0, overdue: 0, upcoming: 0, scheduled: 0, nodate: 0, high: 0, email: 0, all: 0 }
  const lists: Record<string, number> = {}
  const tags: Record<string, number> = {}
  for (const t of all) {
    if (isDone(t)) continue
    for (const id of ['today', 'upcoming', 'scheduled', 'nodate', 'high', 'email', 'all'] as const)
      if (inSmartList(t, id, o)) smart[id] = (smart[id] ?? 0) + 1
    if (isOverdue(t, o.now, o.zone) && !isDeferred(t, o.now, o.zone)) smart.overdue = (smart.overdue ?? 0) + 1
    if (t.list) lists[t.list] = (lists[t.list] ?? 0) + 1
    for (const c of t.categories ?? []) tags[c] = (tags[c] ?? 0) + 1
  }
  return { smart, lists, tags }
}

/** The tag list (the fallback for GET /tasks/tags): every tag on an open task, by name. */
export function deriveTags(all: TaskItem[]): TagInfo[] {
  const by = new Map<string, TagInfo>()
  for (const t of all) {
    for (const c of t.categories ?? []) {
      const k = c.toLowerCase()
      const cur = by.get(k) ?? { name: c, count: 0 }
      if (!isDone(t)) cur.count++
      by.set(k, cur)
    }
  }
  return [...by.values()].sort((a, b) => a.name.localeCompare(b.name))
}

// ---------------------------------------------------------------- order

export type SortKey = 'auto' | 'manual' | 'due' | 'priority' | 'title' | 'created'
export type GroupKey = 'auto' | 'none' | 'due' | 'priority' | 'list' | 'tag'

const cmpStr = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

function dueKey(t: TaskItem, zone: string): number {
  const d = t.due ? dueInstant(t, zone) : null
  // A date-only due sorts after the timed ones of its day (its instant is the end of the day).
  return d ? d.getTime() : Number.POSITIVE_INFINITY
}

export function manualCmp(a: TaskItem, b: TaskItem): number {
  const x = a.sort_order ?? Number.MAX_SAFE_INTEGER
  const y = b.sort_order ?? Number.MAX_SAFE_INTEGER
  if (x !== y) return x - y
  return cmpStr(a.created ?? '', b.created ?? '') || cmpStr(a.uid, b.uid)
}

export function comparator(sort: Exclude<SortKey, 'auto'>, zone: string): (a: TaskItem, b: TaskItem) => number {
  switch (sort) {
    case 'manual':
      return manualCmp
    case 'due':
      return (a, b) => dueKey(a, zone) - dueKey(b, zone) || priorityLevel(b.priority) - priorityLevel(a.priority) || manualCmp(a, b)
    case 'priority':
      return (a, b) => priorityLevel(b.priority) - priorityLevel(a.priority) || dueKey(a, zone) - dueKey(b, zone) || manualCmp(a, b)
    case 'title':
      return (a, b) => a.summary.localeCompare(b.summary, undefined, { sensitivity: 'base' }) || manualCmp(a, b)
    case 'created':
      return (a, b) => cmpStr(b.created ?? '', a.created ?? '') || manualCmp(a, b)
  }
}

/** The sort a view uses when the header says "Default". */
export function defaultSort(ref: TaskListRef): Exclude<SortKey, 'auto'> {
  if (ref.kind === 'list') return 'manual'
  if (ref.kind === 'tag') return 'due'
  switch (ref.id) {
    case 'nodate':
      return 'manual'
    case 'email':
      return 'due'
    case 'completed':
      return 'created'
    default:
      return 'due'
  }
}

// ---------------------------------------------------------------- sections

export interface Section {
  /** Stable id: "overdue", "today", "started", "day:2026-10-03", "list:<url>", … */
  id: string
  title: string
  /** Shown in red (Overdue). */
  tone?: 'danger'
  /** Quick add from this section: what the new task gets (the quick-add defaults). */
  prefill?: { due?: ISODate; list?: string; priority?: number; tag?: string }
  /** A day section the user can drop a task on (task.reschedule). */
  dropDay?: ISODate | null
  tasks: TaskItem[]
  /** Empty sections still show (Upcoming's next seven days). */
  keepEmpty?: boolean
}

export interface GroupOpts extends ViewOpts {
  lists: TaskList[]
  sort: SortKey
  group: GroupKey
  weekStart?: DatePrefs['weekStart']
}

function dayTitle(day: ISODate, today: ISODate): string {
  const n = diffDaysISO(today, day)
  if (n === 0) return tr('common.today')
  if (n === 1) return tr('common.tomorrow')
  if (n === -1) return tr('common.yesterday')
  const sameYear = day.slice(0, 4) === today.slice(0, 4)
  return sameYear ? formatDate(day, 'day') : formatDate(day, 'dayYear')
}

function listName(url: string | undefined, lists: TaskList[]): string {
  return lists.find((l) => l.id === url)?.name ?? tr('common.tasks')
}

/** Order inside "Today": timed by time, then priority, then manual. */
function todayCmp(zone: string) {
  return (a: TaskItem, b: TaskItem) => {
    const ta = a.due && !a.due_all_day ? dueKey(a, zone) : Number.POSITIVE_INFINITY
    const tb = b.due && !b.due_all_day ? dueKey(b, zone) : Number.POSITIVE_INFINITY
    if (ta !== tb) return ta - tb
    return priorityLevel(b.priority) - priorityLevel(a.priority) || manualCmp(a, b)
  }
}

function bucketBy(tasks: TaskItem[], key: (t: TaskItem) => { id: string; title: string; order: string }[]): Section[] {
  const map = new Map<string, Section & { order: string }>()
  for (const t of tasks) {
    for (const k of key(t)) {
      let s = map.get(k.id)
      if (!s) map.set(k.id, (s = { id: k.id, title: k.title, order: k.order, tasks: [] }))
      s.tasks.push(t)
    }
  }
  return [...map.values()].sort((a, b) => cmpStr(a.order, b.order)).map(({ order: _o, ...s }) => s)
}

/** Sections for an explicit grouping (the header's Group menu). */
function groupExplicit(tasks: TaskItem[], group: Exclude<GroupKey, 'auto' | 'none'>, o: GroupOpts): Section[] {
  const today = todayISO(o.zone, o.now)
  switch (group) {
    case 'due':
      return bucketBy(tasks, (t) => {
        const d = dueDay(t, o.zone)
        if (!d) return [{ id: 'nodue', title: tr('common.noDate'), order: '9' }]
        if (d < today && !isDone(t)) return [{ id: 'overdue', title: tr('tasks.model.overdue'), order: '0' }]
        return [{ id: `day:${d}`, title: dayTitle(d, today), order: `1${d}` }]
      }).map((s) => (s.id === 'overdue' ? { ...s, tone: 'danger' as const } : s.id.startsWith('day:') ? { ...s, dropDay: s.id.slice(4), prefill: { due: s.id.slice(4) } } : s))
    case 'priority':
      return bucketBy(tasks, (t) => {
        const l = priorityLevel(t.priority)
        return [{ id: `pri:${l}`, title: l ? tr('tasks.model.priority', { priorityNames: PRIORITY_NAMES[l] }) : tr('tasks.model.noPriority'), order: String(3 - l) }]
      }).map((s) => ({ ...s, prefill: { priority: icalPriority(Number(s.id.slice(4)) as PriorityLevel) } }))
    case 'list':
      return bucketBy(tasks, (t) => {
        const i = o.lists.findIndex((l) => l.id === t.list)
        return [{ id: `list:${t.list ?? ''}`, title: listName(t.list, o.lists), order: String(i < 0 ? 999 : i).padStart(4, '0') }]
      }).map((s) => ({ ...s, prefill: { list: s.id.slice(5) } }))
    case 'tag':
      return bucketBy(tasks, (t) => {
        const cs = t.categories ?? []
        if (!cs.length) return [{ id: 'tag:', title: tr('tasks.model.noTags'), order: '~' }]
        return cs.map((c) => ({ id: `tag:${c.toLowerCase()}`, title: `@${c}`, order: c.toLowerCase() }))
      }).map((s) => (s.id === 'tag:' ? s : { ...s, prefill: { tag: s.title.slice(1) } }))
  }
}

/**
 * The sections a view shows (grouping and order). `group` and `sort`
 * other than 'auto' replace the view's own. Tasks in each section are sorted.
 */
export function buildSections(tasks: TaskItem[], ref: TaskListRef, o: GroupOpts): Section[] {
  const today = todayISO(o.zone, o.now)
  const sortKey = o.sort === 'auto' ? defaultSort(ref) : o.sort
  const cmp = comparator(sortKey, o.zone)
  const sorted = (s: Section[]) => s.map((x) => ({ ...x, tasks: [...x.tasks].sort(cmp) }))

  if (o.group !== 'auto') {
    if (o.group === 'none') return [{ id: 'all', title: '', tasks: [...tasks].sort(cmp) }]
    return sorted(groupExplicit(tasks, o.group, o))
  }

  if (ref.kind === 'list') return [{ id: 'all', title: '', tasks: [...tasks].sort(cmp) }]
  if (ref.kind === 'tag') return [{ id: 'all', title: '', tasks: [...tasks].sort(cmp), prefill: { tag: ref.id } }]

  switch (ref.id) {
    case 'today': {
      const overdue: TaskItem[] = []
      const due: TaskItem[] = []
      const started: TaskItem[] = []
      for (const t of tasks) {
        const d = dueDay(t, o.zone)
        if (d && d < today) overdue.push(t)
        else if (d === today) due.push(t)
        else started.push(t)
      }
      const out: Section[] = []
      if (overdue.length) out.push({ id: 'overdue', title: tr('tasks.model.overdue'), tone: 'danger', tasks: overdue.sort((a, b) => dueKey(a, o.zone) - dueKey(b, o.zone) || manualCmp(a, b)) })
      out.push({ id: 'today', title: tr('common.today'), prefill: { due: today }, dropDay: today, tasks: due.sort(o.sort === 'auto' ? todayCmp(o.zone) : cmp) })
      if (started.length) out.push({ id: 'started', title: tr('tasks.model.started'), tasks: started.sort(o.sort === 'auto' ? todayCmp(o.zone) : cmp) })
      return out.filter((s) => s.tasks.length)
    }
    case 'upcoming': {
      const days = new Map<ISODate, TaskItem[]>()
      const later: TaskItem[] = []
      const months = new Map<string, TaskItem[]>()
      const horizon = addDaysISO(today, 7)
      const monthEnd = addDaysISO(`${addMonthsISOStr(today.slice(0, 7))}-01`, -1)
      for (const t of tasks) {
        const d = dueDay(t, o.zone)
        const s = startDay(t, o.zone)
        // A deferred task appears on its start day; a task due later appears on its due day.
        const key = d && d > today ? (s && s > today && s < d ? s : d) : s && s > today ? s : d ?? today
        if (key <= horizon) days.set(key, [...(days.get(key) ?? []), t])
        else if (key <= monthEnd) later.push(t)
        else months.set(key.slice(0, 7), [...(months.get(key.slice(0, 7)) ?? []), t])
      }
      const out: Section[] = []
      for (let i = 1; i <= 7; i++) {
        const day = addDaysISO(today, i)
        out.push({ id: `day:${day}`, title: dayTitle(day, today), dropDay: day, prefill: { due: day }, tasks: days.get(day) ?? [], keepEmpty: true })
      }
      if (later.length) out.push({ id: 'later', title: tr('tasks.model.laterThisMonth'), tasks: later })
      for (const [ym, ts] of [...months.entries()].sort((a, b) => cmpStr(a[0], b[0]))) {
        const first = `${ym}-01`
        out.push({ id: `month:${ym}`, title: ym.slice(0, 4) === today.slice(0, 4) ? formatDate(first, 'month') : formatDate(first, 'monthYear'), tasks: ts })
      }
      return sorted(out)
    }
    case 'scheduled':
      return sorted(bucketBy(tasks, (t) => {
        const d = dueDay(t, o.zone) ?? today
        return [{ id: `day:${d}`, title: dayTitle(d, today), order: d }]
      }).map((s) => ({ ...s, dropDay: s.id.slice(4), prefill: { due: s.id.slice(4) }, tone: s.id.slice(4) < today ? ('danger' as const) : undefined })))
    case 'nodate':
    case 'all':
      return sorted(groupExplicit(tasks, 'list', o))
    case 'completed': {
      const weekStart = startOfWeekISO(today, o.weekStart ?? 1)
      const order = ['today', 'yesterday', 'week', 'earlier']
      const titles: Record<string, string> = { today: tr('common.today'), yesterday: tr('common.yesterday'), week: tr('tasks.model.thisWeek'), earlier: tr('tasks.model.earlier') }
      const by = new Map<string, TaskItem[]>()
      for (const t of tasks) {
        const d = dayOf(t.completed_at ?? null, false, o.zone)
        const k = !d ? 'earlier' : d === today ? 'today' : d === addDaysISO(today, -1) ? 'yesterday' : d >= weekStart ? 'week' : 'earlier'
        by.set(k, [...(by.get(k) ?? []), t])
      }
      const newest = (a: TaskItem, b: TaskItem) => cmpStr(b.completed_at ?? '', a.completed_at ?? '')
      return order.filter((k) => by.get(k)?.length).map((k) => ({ id: k, title: titles[k], tasks: by.get(k)!.sort(newest) }))
    }
    default:
      return [{ id: 'all', title: '', tasks: [...tasks].sort(cmp) }]
  }
}

function addMonthsISOStr(ym: string): string {
  const y = Number(ym.slice(0, 4))
  const m = Number(ym.slice(5, 7))
  return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`
}

// ---------------------------------------------------------------- subtasks

export interface RowEntry {
  task: TaskItem
  depth: 0 | 1
  /** The parent is not in this view: the row shows "↳ parent" in its meta line. */
  orphan?: boolean
  /** A deeper tree from another client, shown flat under its top parent. */
  nested?: boolean
}

/**
 * Puts subtasks under their parents (one level). A subtask whose parent
 * is not in the section shows on its own; a deeper subtask shows under its top parent with a
 * "nested" hint. `expanded` says which parents show their subtasks; `children` adds subtasks the
 * view does not hold itself (completed ones, loaded on expand).
 */
export function nestRows(tasks: TaskItem[], expanded: Set<string> | 'all', children: Record<string, TaskItem[]> = {}): RowEntry[] {
  const inView = new Map(tasks.map((t) => [t.uid, t]))
  const topOf = (t: TaskItem): { top: string | null; depth: number } => {
    let p = t.parent ?? null
    let depth = 0
    let top: string | null = null
    const seen = new Set<string>()
    while (p && inView.has(p) && !seen.has(p)) {
      seen.add(p)
      top = p
      depth++
      p = inView.get(p)!.parent ?? null
    }
    return { top, depth }
  }
  const kids = new Map<string, { t: TaskItem; nested: boolean }[]>()
  const roots: TaskItem[] = []
  for (const t of tasks) {
    const { top, depth } = topOf(t)
    if (top) kids.set(top, [...(kids.get(top) ?? []), { t, nested: depth > 1 }])
    else roots.push(t)
  }
  const out: RowEntry[] = []
  for (const r of roots) {
    out.push({ task: r, depth: 0, orphan: !!r.parent })
    if (expanded !== 'all' && !expanded.has(r.uid)) continue
    const mine = kids.get(r.uid) ?? []
    const extra = (children[r.uid] ?? []).filter((c) => !inView.has(c.uid))
    for (const k of mine) out.push({ task: k.t, depth: 1, nested: k.nested })
    for (const c of extra) out.push({ task: c, depth: 1 })
  }
  return out
}

// ---------------------------------------------------------------- the due chip

export type ChipTone = 'overdue' | 'urgent' | 'normal' | 'deferred' | 'done'

export interface DueChip {
  text: string
  tone: ChipTone
  /** For the tooltip: "Due Fri 2 Oct, 3:00 PM". */
  title: string
}

function shortDay(day: ISODate, today: ISODate): string {
  const n = diffDaysISO(today, day)
  if (n === 0) return tr('common.today')
  if (n === 1) return tr('common.tomorrow')
  if (n === -1) return tr('common.yesterday')
  if (n > 1 && n < 7) return formatDate(day, 'weekday')
  return day.slice(0, 4) === today.slice(0, 4) ? formatDate(day, 'short') : formatDate(day, 'shortYear')
}

/** "3d", "2h", "15m": how long ago. */
function agoText(ms: number, days: number): string {
  if (days >= 1) return `${days}d`
  const h = Math.floor(ms / 3_600_000)
  if (h >= 1) return `${h}h`
  return `${Math.max(1, Math.floor(ms / 60_000))}m`
}

/**
 * The due chip: "Overdue 2d" (red), "Today 3 PM", "Tomorrow", "Fri",
 * "12 Oct"; "Starts Thu" (muted) when deferred. A deadline within two days is urgent (red text),
 * like the mail rows' deadline chips. Null when the task has neither due nor future start.
 */
export function dueChip(t: TaskItem, now: Date, prefs: Pick<DatePrefs, 'zone' | 'hour12'>): DueChip | null {
  const zone = prefs.zone
  const today = todayISO(zone, now)
  if (isDeferred(t, now, zone) && !isDone(t)) {
    const s = startDay(t, zone)!
    return { text: tr('tasks.model.starts', { shortDay: shortDay(s, today) }), tone: 'deferred', title: tr('tasks.model.starts2', { date: formatDate(s, 'day') }) }
  }
  if (!t.due) return null
  const day = dueDay(t, zone)!
  const timed = !(t.due_all_day || /^\d{4}-\d{2}-\d{2}$/.test(t.due))
  const at = timed ? parseISO(t.due, zone) : null
  const time = at ? formatTime(at, prefs, { compact: true }) : ''
  const title = tr('tasks.model.due', { date: formatDate(day, 'day'), value: at ? `, ${formatTime(at, prefs)}` : '' })
  if (isDone(t)) return { text: shortDay(day, today) + (time ? ` ${time}` : ''), tone: 'done', title }
  if (isOverdue(t, now, zone)) {
    const days = diffDaysISO(day, today)
    const ms = now.getTime() - (at ?? dueInstant(t, zone)!).getTime()
    return { text: tr('tasks.model.overdue2', { agoText: agoText(ms, days) }), tone: 'overdue', title }
  }
  const n = diffDaysISO(today, day)
  const text = shortDay(day, today) + (time && n < 7 ? ` ${time}` : '')
  return { text, tone: n <= 2 ? 'urgent' : 'normal', title }
}

// ---------------------------------------------------------------- search (task.search)

export interface TaskQuery {
  text: string[]
  list?: string
  tags: string[]
  dueBefore?: ISODate
  dueAfter?: ISODate
  done?: boolean
  overdue?: boolean
  hasEmail?: boolean
  priority?: PriorityLevel
}

const PRI_WORDS: Record<string, PriorityLevel> = { high: 3, hi: 3, '3': 3, medium: 2, med: 2, '2': 2, low: 1, lo: 1, '1': 1, none: 0, '0': 0 }

/** A relative date word for `due:` ("fri", "today", "tom", "2026-10-09"), read in the zone. */
function searchDay(word: string, now: Date, zone: string): ISODate | null {
  const today = todayISO(zone, now)
  const w = word.toLowerCase()
  if (/^\d{4}-\d{2}-\d{2}$/.test(w)) return w
  if (w === 'today') return today
  if (w === 'tom' || w === 'tomorrow') return addDaysISO(today, 1)
  if (w === 'yesterday') return addDaysISO(today, -1)
  const wd = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'].findIndex((d) => w.startsWith(d))
  if (wd >= 0) {
    const cur = new Date(`${today}T12:00:00Z`).getUTCDay()
    return addDaysISO(today, ((wd - cur + 7) % 7) || 7)
  }
  const m = /^\+?(\d+)([dw])$/.exec(w)
  if (m) return addDaysISO(today, Number(m[1]) * (m[2] === 'w' ? 7 : 1))
  return null
}

/** Search operators: list:, tag: (or @tag), due:<fri, is:done, is:overdue, has:email, pri:high. */
export function parseTaskQuery(q: string, now: Date, zone: string): TaskQuery {
  const out: TaskQuery = { text: [], tags: [] }
  for (const m of q.matchAll(/(\S+?):"([^"]*)"|"([^"]*)"|(\S+)/g)) {
    const op = m[1]?.toLowerCase()
    const quoted = m[2] ?? m[3]
    const word = m[4]
    if (op) {
      applyOp(out, op, quoted!, now, zone)
      continue
    }
    if (quoted != null) {
      out.text.push(quoted.toLowerCase())
      continue
    }
    const at = word.indexOf(':')
    if (word.startsWith('@') && word.length > 1) out.tags.push(word.slice(1).toLowerCase())
    else if (at > 0 && applyOp(out, word.slice(0, at).toLowerCase(), word.slice(at + 1), now, zone)) continue
    else out.text.push(word.toLowerCase())
  }
  return out
}

function applyOp(out: TaskQuery, op: string, v: string, now: Date, zone: string): boolean {
  switch (op) {
    case 'list':
      out.list = v.toLowerCase()
      return true
    case 'tag':
      out.tags.push(v.replace(/^@/, '').toLowerCase())
      return true
    case 'is':
      if (v === 'done' || v === 'completed') out.done = true
      else if (v === 'open') out.done = false
      else if (v === 'overdue') out.overdue = true
      else return false
      return true
    case 'has':
      if (v === 'email') out.hasEmail = true
      else return false
      return true
    case 'pri':
    case 'priority':
      if (!(v.toLowerCase() in PRI_WORDS)) return false
      out.priority = PRI_WORDS[v.toLowerCase()]
      return true
    case 'due': {
      const m = /^([<>]=?)?(.+)$/.exec(v)
      if (!m) return false
      const d = searchDay(m[2], now, zone)
      if (!d) return false
      if (!m[1]) {
        out.dueAfter = addDaysISO(d, -1)
        out.dueBefore = addDaysISO(d, 1)
      } else if (m[1].startsWith('<')) out.dueBefore = m[1] === '<=' ? addDaysISO(d, 1) : d
      else out.dueAfter = m[1] === '>=' ? addDaysISO(d, -1) : d
      return true
    }
  }
  return false
}

export function isEmptyQuery(q: TaskQuery): boolean {
  return !q.text.length && !q.tags.length && q.list == null && q.dueBefore == null && q.dueAfter == null && q.done == null
    && !q.overdue && !q.hasEmail && q.priority == null
}

export function matchesQuery(t: TaskItem, q: TaskQuery, o: ViewOpts & { lists: TaskList[] }): boolean {
  const hay = `${t.summary}\n${t.notes ?? ''}\n${(t.categories ?? []).join(' ')}`.toLowerCase()
  if (!q.text.every((w) => hay.includes(w))) return false
  if (!q.tags.every((tag) => (t.categories ?? []).some((c) => c.toLowerCase() === tag))) return false
  if (q.list != null) {
    const name = o.lists.find((l) => l.id === t.list)?.name.toLowerCase() ?? ''
    if (!name.startsWith(q.list)) return false
  }
  if (q.done != null && isDone(t) !== q.done) return false
  if (q.overdue && !isOverdue(t, o.now, o.zone)) return false
  if (q.hasEmail && (t.conversation_id == null || t.conversation_id === '')) return false
  if (q.priority != null && priorityLevel(t.priority) !== q.priority) return false
  if (q.dueBefore != null || q.dueAfter != null) {
    const d = dueDay(t, o.zone)
    if (!d) return false
    if (q.dueBefore != null && !(d < q.dueBefore)) return false
    if (q.dueAfter != null && !(d > q.dueAfter)) return false
  }
  return true
}

// ---------------------------------------------------------------- manual order

/**
 * Where a task sits before a move: its parent ("" at the top level) and the tasks of its level above
 * it and below it, the nearest first.
 */
export interface TaskPlace {
  parent: string
  above: string[]
  below: string[]
}

/** The place of [t] among [siblings] (the rows of its level, in the order shown). */
export function placeOf(t: TaskItem, siblings: TaskItem[]): TaskPlace {
  const uids = siblings.map((x) => x.uid)
  const i = uids.indexOf(t.uid)
  if (i < 0) return { parent: t.parent ?? '', above: [], below: [] }
  return { parent: t.parent ?? '', above: uids.slice(0, i).reverse(), below: uids.slice(i + 1) }
}

export interface MoveChange {
  list?: string
  before?: string
  after?: string
  parent?: string
  sort_order?: number
}

/**
 * The move that undoes [m] (task.reorder, task.reparent, task.move_list): the old list, the old parent,
 * and the old neighbours, sent through the same POST /move. Null when there is nothing to put back.
 * [moved] are the tasks of the same batch not yet back in place: the nearest neighbour that is not
 * one of them is used (after the one above, else before the one below).
 */
export function moveBack(before: TaskItem, m: MoveChange, from?: TaskPlace, moved: Set<string> = new Set()): MoveChange | null {
  const out: MoveChange = {}
  if (m.list && m.list !== before.list) out.list = before.list
  const reparented = m.parent !== undefined && (m.parent || '') !== (before.parent ?? '')
  if (reparented) out.parent = before.parent ?? ''
  if (from && (m.before || m.after || m.sort_order !== undefined || reparented)) {
    const after = from.above.find((u) => !moved.has(u))
    const next = after ? undefined : from.below.find((u) => !moved.has(u))
    if (after) out.after = after
    else if (next) out.before = next
    if (before.sort_order != null) out.sort_order = before.sort_order
  } else if (m.sort_order !== undefined && before.sort_order != null) out.sort_order = before.sort_order
  return out.list || out.parent !== undefined || out.after || out.before ? out : null
}

/** sort_order for a task dropped between two neighbours (either may be missing). */
export function midpoint(before: number | null | undefined, after: number | null | undefined): number {
  const STEP = 1024
  if (before == null && after == null) return 0
  if (before == null) return Math.floor(after! - STEP)
  if (after == null) return Math.floor(before + STEP)
  if (after - before > 1) return Math.floor((before + after) / 2)
  return before + 1
}

/** Whether a list's manual order needs renumbering before a move (missing or equal values). */
export function needsRenumber(tasks: TaskItem[]): boolean {
  const seen = new Set<number>()
  for (const t of tasks) {
    if (t.sort_order == null || seen.has(t.sort_order)) return true
    seen.add(t.sort_order)
  }
  return false
}

// ---------------------------------------------------------------- bulk undo

export type BulkOp = 'complete' | 'reopen' | 'reschedule' | 'move' | 'tag' | 'untag' | 'priority' | 'delete'

/** The fields a bulk op changes, as a PATCH body for one task. */
export function bulkPatch(op: BulkOp, t: TaskItem, args: { due?: string | null; due_all_day?: boolean; list?: string; tag?: string; priority?: number }): Record<string, unknown> | null {
  switch (op) {
    case 'complete':
      return { completed: true }
    case 'reopen':
      return { completed: false }
    case 'reschedule':
      return args.due ? { due: args.due } : { clear_due: true }
    case 'move':
      return { list: args.list }
    case 'tag': {
      const has = (t.categories ?? []).some((c) => c.toLowerCase() === args.tag!.toLowerCase())
      return has ? null : { categories: [...(t.categories ?? []), args.tag!] }
    }
    case 'untag':
      return { categories: (t.categories ?? []).filter((c) => c.toLowerCase() !== args.tag!.toLowerCase()) }
    case 'priority':
      return { priority: args.priority ?? 0 }
    case 'delete':
      return null
  }
}

/**
 * What puts one task back as it was before a bulk op (one undo for the batch): a PATCH
 * body, or a create for a deleted task.
 */
export function inverseOf(op: BulkOp, before: TaskItem): { patch?: Record<string, unknown>; recreate?: TaskItem } {
  switch (op) {
    case 'complete':
    case 'reopen':
      return { patch: { completed: isDone(before) } }
    case 'reschedule':
      return { patch: before.due ? { due: before.due } : { clear_due: true } }
    case 'move':
      return { patch: { list: before.list } }
    case 'tag':
    case 'untag':
      return { patch: { categories: before.categories ?? [] } }
    case 'priority':
      return { patch: { priority: before.priority ?? 0 } }
    case 'delete':
      return { recreate: before }
  }
}

/** Shift a due by whole days, keeping its time (bulk "Shift by +1 day / +1 week"). */
export function shiftDue(t: TaskItem, days: number, zone: string): string | null {
  if (!t.due) return null
  if (t.due_all_day || /^\d{4}-\d{2}-\d{2}$/.test(t.due)) return addDaysISO(t.due.slice(0, 10), days)
  const d = parseISO(t.due, zone)
  if (!d) return null
  const day = addDaysISO(toISODate(d, zone), days)
  const hm = formatTime(d, { zone, hour12: false })
  const instant = parseISO(`${day}T${hm}`, zone)!
  return instant.toISOString()
}

/** The partial-failure line: "11 of 12 done · 1 changed elsewhere". */
export function bulkReport(verb: string, ok: number, failed: { detail?: string; conflict?: boolean }[]): string {
  const total = ok + failed.length
  if (!failed.length) return tr('tasks.model.tasks', { count: ok, verb })
  const conflicts = failed.filter((f) => f.conflict).length
  const other = failed.length - conflicts
  const parts = [tr('tasks.model.ofDone', { ok, total })]
  if (conflicts) parts.push(tr('tasks.model.changedElsewhere', { conflicts }))
  if (other) parts.push(tr('tasks.model.failed', { other }))
  return parts.join(' · ')
}

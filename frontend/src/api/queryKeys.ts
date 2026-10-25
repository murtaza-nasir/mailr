// Query keys for the Calendar and Tasks apps. Every calendar key starts with 'calendar', every
// task key with 'tasks' and every reminder key with 'reminders', so one prefix invalidation
// (api/events.ts, on a live change) refreshes everything that shows them. The older panels
// use the same prefixes (['calendar', 'agenda', from], ['tasks'], ['tasks', 'completed']).
// Reached as qk.calendar / qk.tasks / qk.reminders (api/hooks.ts).

/** Filters a task list query carries (GET /tasks). */
export interface TaskListParams {
  smart?: string
  list?: string
  tag?: string
  q?: string
  sort?: string
  status?: string
  cursor?: string
}

export const calendarKeys = {
  all: ['calendar'] as const,
  /** Occurrences in [from, to), ISO instants; `calendars` is the visible set when the view filters. */
  range: (from: string, to: string, calendars?: string) => ['calendar', 'range', from, to, calendars ?? 'visible'] as const,
  /** The rail panel's one-day agenda (shape kept from CalendarPanel). */
  agenda: (from: string) => ['calendar', 'agenda', from] as const,
  /** One occurrence (occ = canonical occurrence string), or the master when occ is absent. */
  event: (uid: string, occ?: string | null) => ['calendar', 'event', uid, occ ?? null] as const,
  calendars: ['calendar', 'calendars'] as const,
  collections: ['calendar', 'collections'] as const,
  defaults: ['calendar', 'defaults'] as const,
  search: (q: string) => ['calendar', 'search', q] as const,
  freebusy: (from: string, to: string) => ['calendar', 'freebusy', from, to] as const,
  syncStatus: ['calendar', 'sync-status'] as const,
}

export const taskKeys = {
  all: ['tasks'] as const,
  list: (p: TaskListParams) => ['tasks', 'list', p] as const,
  task: (uid: string) => ['tasks', 'task', uid] as const,
  counts: ['tasks', 'counts'] as const,
  tags: ['tasks', 'tags'] as const,
  /** Task lists (VTODO collections). */
  lists: ['tasks', 'lists'] as const,
  /** Tasks shown on the calendar grid for [from, to). */
  onCalendar: (from: string, to: string) => ['tasks', 'calendar', from, to] as const,
}

export const reminderKeys = {
  all: ['reminders'] as const,
  /** Fired and missed reminders (the bell tray). */
  active: ['reminders', 'active'] as const,
  settings: ['reminders', 'settings'] as const,
}

/**
 * The key prefixes a change to each live entity makes stale. A changed event or task also
 * changes which reminders are due; a changed calendar (collection: renamed, recoloured,
 * hidden, removed) changes what both apps show.
 */
export const CHANGE_KEYS: Record<string, readonly (readonly string[])[]> = {
  event: [calendarKeys.all, reminderKeys.active],
  task: [taskKeys.all, reminderKeys.active],
  calendar: [calendarKeys.all, taskKeys.all, reminderKeys.all],
  reminder: [reminderKeys.all],
}

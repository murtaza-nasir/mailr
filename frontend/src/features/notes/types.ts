// The shapes of the notes API. A note is text or a checklist.

export type NoteKind = 'text' | 'checklist'
export type OrderMode = 'learned' | 'manual'
export type AccessLevel = 'view' | 'edit' | 'share' | 'owner'

export interface Person {
  id?: number
  name?: string
  kind?: string
}

export interface Qty {
  amount: string
  unit: string | null
  text?: string
}

export interface ViewerState {
  pinned: boolean
  archived: boolean
  labels: string[]
  order: string | null
  seen_version?: number | null
  /** When this viewer last opened it (the "changed since you looked" dot). */
  seen_at?: string | null
  /** A table only: the date column this viewer shows on their calendar (tables/calendar.py). */
  calendar_column?: string | null
}

/** Who else sees a note or a table (notes/acl.py `summary`): "Shared with 2 people", initials. */
export interface SharingSummary {
  count: number
  people: { kind: 'user' | 'group'; name: string }[]
}

/** The share levels, as the dialog names them. */
export type ShareLevel = 'view' | 'edit' | 'share'


/** One of the viewer's reminders on a note (notes/reminders.py): key n|<id>|abs|<instant>. */
export interface NoteReminder {
  key: string
  at: string
  state: 'upcoming' | 'fired' | 'snoozed'
  snoozed_until: string | null
}

/** A template (notes/templates.py): built-in or the user's own; "b-…" and "u…" ids. */
export interface NoteTemplate {
  id: string
  kind: 'text' | 'checklist' | 'table'
  name: string
  builtin: boolean
  summary: string
  content: Record<string, unknown>
  created_at: string | null
}

export interface NoteLink {
  id: number
  target_kind: 'conversation' | 'event' | 'task' | 'recording'
  target_ref: Record<string, unknown>
  title?: string | null
  /** hidden: the viewer cannot see the target, so the title is a placeholder. */
  state?: 'ok' | 'hidden'
}

export interface Section {
  id: string
  name: string
  order: string
  order_mode: OrderMode
  collapsed: boolean
  trips?: number
  unchecked?: number
}

export interface Item {
  id: string
  section_id: string | null
  text: string
  key?: string
  qty: Qty | null
  extra: string | null
  checked: boolean
  checked_at: string | null
  checked_by?: Person | null
  order: string
  rank: number | null
  added_by?: Person | null
  added_at: string
  updated_by?: Person | null
  updated_at?: string
  version?: number
  task_uid?: string | null
}

export interface PreviewItem {
  id: string
  text: string
  qty: Qty | null
  section: string | null
}

export interface Note {
  id: string
  kind: NoteKind
  title: string
  body: string
  color: string | null
  shopping: boolean
  checked_at_bottom: boolean
  suggest_usual: boolean
  local_only: boolean
  top_order_mode?: OrderMode
  /** GET /notes/{id}: the top group (items with no section). */
  top?: { order_mode: OrderMode; collapsed: boolean; trips: number; unchecked: number }
  owner?: { kind: 'user' | 'group'; id?: number; name?: string }
  created_by?: Person
  created_at: string
  updated_at: string
  updated_by?: Person | null
  deleted_at: string | null
  purge_at?: string | null
  version: number
  level: AccessLevel
  state: ViewerState
  counts?: { items: number; unchecked: number; checked: number; sections: number }
  links?: NoteLink[]
  preview?: { items: PreviewItem[]; more: number; checked: number }
  sections?: Section[]
  items?: Item[]
  /** Search results: the matching items. */
  matches?: { item_id: string | null; text: string }[]
  sharing?: SharingSummary
  /** Someone else changed it since this viewer last opened it. */
  changed?: boolean
  /** The viewer may move it to Trash (the owner; for a group note its creator or an admin). */
  can_delete?: boolean
  /** The viewer may turn Local models only off (the owner only; notes/localonly.py). */
  can_lower_local_only?: boolean
  /** A repeating list (notes/repeat.py): its rule, the next and the last reset. */
  repeat?: { rule: string; next: string | null; last: string | null; zone?: string | null } | null
  /** The viewer's own reminders on it, handled ones left out; soonest first. */
  reminders?: NoteReminder[]
  /** When it reminds next (a snooze or the next time), else the latest that fired. */
  next_reminder?: string | null
}

export interface NoteLabel {
  name: string
  color: string | null
  order: string
  count: number
}

export interface Undo {
  method: string
  path: string
  body: unknown
  label: string
}

export interface UsualItem {
  key: string
  text: string
  qty: Qty | null
  section_id: string | null
  section: string | null
  days_since: number
  interval: number
  count: number
}

export interface Revision {
  rev: number
  at: string
  by: Person | null
  source: 'edit' | 'assistant' | 'agent' | 'restore' | 'import' | 'convert' | 'repeat'
  summary: string
  content?: { kind: NoteKind; title: string; body: string; color: string | null; sections: Section[]; items: Item[] }
}

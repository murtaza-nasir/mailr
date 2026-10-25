// The Calendar app's view of the API objects (section 2). Every field the
// contract marks new is optional here: the app runs against servers from before the mirror too,
// and fills the gaps itself (see normalizeEvent in data.ts).

import { t } from '../../i18n'

export type PartStat = 'ACCEPTED' | 'DECLINED' | 'TENTATIVE' | 'NEEDS-ACTION' | 'DELEGATED' | string

export interface Attendee {
  email: string
  name?: string
  partstat?: PartStat
  role?: string
  rsvp?: boolean
  comment?: string
}

/** An occurrence (GET /calendar/agenda) or a master (GET /calendar/events/{uid}). */
export interface ApiEvent {
  id?: number
  uid: string
  occurrence?: string
  recurrence_id?: string | null
  summary: string
  location?: string
  description?: string
  start: string
  end: string | null
  all_day?: boolean
  tz?: string | null
  end_tz?: string | null
  status?: string
  transparent?: boolean
  class?: string
  color?: string | null
  calendar_color?: string | null
  categories?: string[]
  url?: string | null
  sequence?: number
  etag?: string | null
  recurring?: boolean
  is_override?: boolean
  /** The master only (GET /calendar/events/{uid}): changed occurrences that are not cancelled. */
  override_count?: number
  rrule?: string | null
  organizer?: string | null
  organizer_name?: string
  attendees?: number
  attendee_list?: Attendee[]
  my_status?: string | null
  is_organizer?: boolean
  reminders?: number[]
  /** Marked "no reminders" (X-MAILR-NO-REMINDER): the user chose none, so not even the default fires. */
  no_reminders?: boolean
  calendar?: string
  calendar_id?: number
  connector_id?: number
  href?: string
  conversation_id?: number | string | null
  local_only?: boolean
}

/** An event as the views use it: times parsed, colour and identity resolved. */
export interface CalEvent extends ApiEvent {
  /** Unique per occurrence: `${uid}|${occurrence}`. */
  key: string
  occurrence: string
  startAt: Date
  /** Exclusive end. All-day: the start of the day after the last day, in the display zone. */
  endAt: Date
  allDay: boolean
  /** All-day first and last day (inclusive), ISO dates; timed events: the dates they touch in the zone. */
  firstDay: string
  lastDay: string
  colour: string
  readOnly: boolean
}

export interface SyncInfo {
  state?: string
  last_sync_at?: string | null
  error?: string | null
}

export interface Collection {
  id: string
  url?: string
  calendar_id?: number
  connector_id: number
  name: string
  description?: string
  color?: string | null
  events?: boolean
  tasks?: boolean
  components?: string[]
  read_only?: boolean
  /** A calendar link the user subscribed to (calendar.subscribe, backend calendar/webcal.py): read-only. */
  subscription?: { url: string; refresh_hours: number } | null
  tz?: string | null
  hidden?: boolean
  busy?: boolean
  notify?: boolean
  sort_order?: number
  local_only?: boolean
  sync?: SyncInfo
  error?: string
  /** Minutes, or separate lists for timed and all-day events. */
  default_reminders?: number[] | { timed?: number[]; all_day?: number[] } | null
  color_local?: boolean
  /** A group calendar (calendar/groups.py), or a calendar another person shares with this user
   *  (calendar/sharing.py, kind "user"). The user hides it; only its owner (or an admin, for a
   *  group calendar) renames or deletes it. */
  shared?: SharedInfo | null
  /** This user's own calendar, shared with other people (calendar/sharing.py). */
  sharing?: { people: number; others: number } | null
  /** "Share…" is offered: an own calendar of the mailr calendar account, or one shared with the share level. */
  shareable?: boolean
}

export type SharedInfo = GroupShared | UserShared

export interface GroupShared {
  kind: 'group'
  group: string
  access: 'read' | 'write'
  editor?: boolean
  group_calendar_id?: number
}

export type ShareLevel = 'view' | 'edit' | 'share'

export interface UserShared {
  kind: 'user'
  owner: string
  owner_id?: number
  level: ShareLevel
  access: 'read' | 'write'
  can_share?: boolean
}

/** "Shared with the Family group" or "Shared by Rosa", plus "view only" when this user cannot write. */
export function sharedLabel(c: Pick<Collection, 'shared' | 'read_only'>): string {
  if (!c.shared) return ''
  const who = c.shared.kind === 'user' ? t('calendar.types.sharedBy', { owner: c.shared.owner }) : t('calendar.types.sharedWithTheGroup', { group: c.shared.group })
  return `${who}${c.read_only ? t('calendar.types.viewOnly') : ''}`
}

/** Who renames or deletes a calendar this user only shares. */
export function ownerNote(c: Pick<Collection, 'shared'>): string {
  if (!c.shared) return ''
  return c.shared.kind === 'user'
    ? t('calendar.types.onlyRenamesOrDeletesIt', { owner: c.shared.owner })
    : t('calendar.types.onlyAnAdministratorRenamesOr')
}

/** "Shared with 2 people" under the owner's own calendar. */
export function sharingLabel(c: Pick<Collection, 'sharing'>): string {
  const n = c.sharing?.people ?? 0
  const o = c.sharing?.others ?? 0
  if (!n && !o) return ''
  if (!n) return t('calendar.types.sharedWithAGroup')
  return t('calendar.types.sharedWithPeople', { count: n, value: o ? t('calendar.types.andAGroup') : '' })
}

export const SHARE_LEVELS: { id: ShareLevel; label: string; desc: string }[] = [
  { id: 'view', label: t('common.view'), desc: t('calendar.types.seesEveryEventChangesNothing') },
  { id: 'edit', label: t('common.edit2'), desc: t('calendar.types.addsChangesAndDeletesEvents') },
  { id: 'share', label: t('calendar.types.editAndShare'), desc: t('calendar.types.editsAndSharesItWith') },
]

export interface Account {
  id: number
  name: string
  builtin?: boolean
  url?: string
  username?: string
  status?: string
  status_detail?: string
}

export interface SyncStatus {
  state: string
  last_sync_at: string | null
  connectors: {
    id: number
    name: string
    status: string
    detail?: string | null
    /** "auth": the server stopped accepting the password; Retry cannot help. */
    error_kind?: string | null
    last_sync_at?: string | null
    collections?: { calendar_id: number; url: string; name: string; state: string; error?: string | null }[]
  }[]
}

export interface AgendaResponse {
  from?: string
  to?: string
  connected?: boolean
  events: ApiEvent[]
  sync?: SyncInfo
}

export interface ApiTask {
  uid: string
  summary: string
  due: string | null
  due_all_day?: boolean
  status: string
  completed?: boolean
  list?: string
  list_id?: number
  etag?: string | null
  priority?: number
  /** A table's dated row shown with the tasks (tableDates.tsx): read-only, opens the row. */
  table_row?: { table: string; row: string; table_name: string; column: string; color: string | null }
}

export interface ConflictBody {
  error: 'conflict' | 'exists'
  detail: string
  kind?: 'event' | 'task'
  server?: ApiEvent
}

export type Scope = 'this' | 'following' | 'all'

/** The fields an event write may carry (POST /calendar/events, PATCH /calendar/events/{uid}). */
export interface EventWrite {
  summary?: string
  start?: string
  end?: string | null
  all_day?: boolean
  tz?: string
  location?: string
  description?: string
  calendar?: string
  recurrence?: string | null
  attendees?: (string | { email: string; name?: string; role?: string })[]
  reminders?: number[]
  transparent?: boolean
  class?: string
  color?: string | null
  url?: string | null
  status?: string
  send_updates?: boolean
  /** A note for the guests, in the update or cancellation mail. */
  message?: string
  conversation_id?: number | string | null
  /** The sending identity: it becomes ORGANIZER. */
  identity_id?: number
  uid?: string
  /** Live editing: the version the change was made from (the server merges what changed since). */
  base_version?: number | null
}

export interface SharePerson {
  id: number
  name: string
  email: string
}

/** GET /calendar/collections/{id}/shares (calendar/sharing.py `describe`). */
export interface ShareState {
  calendar_id: number
  name: string
  own: boolean
  owner: SharePerson
  policy: 'anyone' | 'groups' | 'nobody'
  levels: ShareLevel[]
  people: (SharePerson & { level: ShareLevel; disabled: boolean; you: boolean; outside_policy: boolean })[]
  others: number
  candidates: SharePerson[]
  max: number
}

export interface ShareNotice {
  path: string
  name: string
  by: string
  owner: string
  at: string
}

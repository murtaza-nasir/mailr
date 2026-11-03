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
  /** A block from a phone calendar (backend calendar/phonecal.py): read-only; times only unless phone_details. */
  phone_busy?: boolean
  /** A phone calendar's block with the phone's title and place (the calendar sends details). */
  phone_details?: boolean
  /** A busy time of a calendar shared with this user as free/busy only (calendar/shared_busy.py): read-only, times only. */
  shared_busy?: boolean
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
  /** The class of `error` (backend calendar/sync_errors.py), which syncProblemText explains. */
  error_kind?: string | null
}

/** Where a calendar comes from (backend mirror.source_of), for the grouped lists. */
export interface CollectionSource {
  kind: 'builtin' | 'account' | 'shared' | 'phone' | 'link'
  /** account: its name and the host of its address. */
  name?: string
  host?: string | null
  /** phone: the phone's name. */
  device?: string
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
  /** A phone calendar's busy times (backend calendar/phonecal.py): read-only, sent by the Android app. */
  phone?: PhoneInfo | null
  tz?: string | null
  hidden?: boolean
  busy?: boolean
  notify?: boolean
  sort_order?: number
  local_only?: boolean
  sync?: SyncInfo
  source?: CollectionSource
  /** An account whose calendars could not be read: the technical text, its kind and host. */
  error?: string
  error_kind?: string | null
  error_host?: string | null
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

/** Where a phone calendar's busy times come from and when they were last sent. */
export interface PhoneInfo {
  key: string
  device: string
  synced_at: string | null
  stale: boolean
  /** The phone sends titles and places too, not busy times only. */
  details?: boolean
}

export type SharedInfo = GroupShared | UserShared

export interface GroupShared {
  kind: 'group'
  group: string
  access: 'read' | 'write'
  editor?: boolean
  group_calendar_id?: number
}

/** freebusy exists on Stalwart 1.x only; the Share dialog offers the levels the server lists. */
export type ShareLevel = 'freebusy' | 'view' | 'edit' | 'share'

export interface UserShared {
  kind: 'user'
  owner: string
  owner_id?: number
  /** What this user can do, by all their grants (their own and their groups'). */
  level: ShareLevel
  access: 'freebusy' | 'read' | 'write'
  can_share?: boolean
  /** Set when a group gives more than this user's own grant: the own level and the groups. */
  own_level?: ShareLevel | null
  via?: string[]
}

/** What a level lets someone do, as a short phrase: "can edit". */
export function levelPhrase(l: ShareLevel): string {
  return {
    freebusy: t('calendar.types.seesFreeBusyOnly'),
    view: t('calendar.types.canView'),
    edit: t('calendar.types.canEdit'),
    share: t('calendar.types.canEditAndShare'),
  }[l]
}

/** "can edit (through Family)": the access a group gives, more than the person's own grant. */
export function throughLabel(level: ShareLevel, via: string[] | undefined): string {
  return t('calendar.types.levelThrough', { level: levelPhrase(level), groups: (via ?? []).join(', ') })
}

/** "Shared with the Family group" or "Shared by Rosa", plus "view only" when this user cannot write,
 *  "free/busy only", or the access a group of theirs gives ("can edit (through Family)"). */
export function sharedLabel(c: Pick<Collection, 'shared' | 'read_only'>): string {
  if (!c.shared) return ''
  if (c.shared.kind === 'user') {
    const who = t('calendar.types.sharedBy', { owner: c.shared.owner })
    if (c.shared.level === 'freebusy') return `${who}${t('calendar.types.freeBusyOnlySuffix')}`
    if (c.shared.via?.length) return `${who} · ${throughLabel(c.shared.level, c.shared.via)}`
    return `${who}${c.read_only ? t('calendar.types.viewOnly') : ''}`
  }
  return `${t('calendar.types.sharedWithTheGroup', { group: c.shared.group })}${c.read_only ? t('calendar.types.viewOnly') : ''}`
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
  { id: 'freebusy', label: t('calendar.types.freeBusyOnly'), desc: t('calendar.types.seesOnlyWhenYouAreBusy') },
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
    /** The class of the failure (backend calendar/sync_errors.py). "auth": the server stopped
     *  accepting the password; Retry cannot help. */
    error_kind?: string | null
    /** The host of the account's address, for the message. */
    error_host?: string | null
    last_sync_at?: string | null
    collections?: { calendar_id: number; url: string; name: string; state: string; error?: string | null; error_kind?: string | null }[]
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
  people: (SharePerson & {
    level: ShareLevel
    disabled: boolean
    you: boolean
    outside_policy: boolean
    /** Set when a group of theirs gives more than `level` (calendar/sharing.py `access_of`). */
    access?: { level: ShareLevel; can_share: boolean; via: string[] }
  })[]
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

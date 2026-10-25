// The full event editor (web.editor): title; when (typed date and time fields, all-day, a
// duration hint, time zones); repeat; guests and the Find-a-time tab;
// where (location, video link, map link); calendar; colour; show as;
// private; reminders; description; the linked email. Save (Ctrl/⌘+Enter), Cancel
// (Esc; asks only when something changed), Delete, Duplicate. The draft is kept on this device
// until it is saved or discarded. A large modal in Classic, Focus and Desk; it fills the
// reading pane in Office and is a full-height sheet in Cards.

import { Ban, CalendarSearch, ChevronDown, Clock, Globe, Mail, MapPin, Repeat, Text, Users, Video, X, Palette, Bell, CalendarDays, Eye } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router'
import { ConfirmDialog } from '../../components/Dialog'
import { MenuItem, Popover } from '../../components/Popover'
import { addDaysISO, diffDaysISO, formatDate, formatRange, minutesOfDay, toISODate, type ISODate } from '../../lib/dates'
import { eventPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { useCompose, emptyPayload } from '../../stores/compose'
import { toast, toastError } from '../../stores/ui'
import { useMe } from '../../api/hooks'
import { conflictLine, guestCount, timesFor, toWrite } from './actions'
import { displayColour } from './colour'
import { GUEST_GROUPS, groupCounts, guestRecipients, type GuestGroup } from './emailGuests'
import { useCal } from './context'
import { calApi, defaultReminders, reminderField, useDefaults, userDefaultReminders } from './data'
import { useReminderSettings } from '../reminders/api'
import { ColourPicker, DateField, GuestField, ReminderChips, RepeatPicker, TimeField, type Guest, type GuestFieldHandle } from './fields'
import { PlaceOptions } from '../people/PlaceOptions'
import { FindTimePanel } from './FindTime'
import { formatDuration, gridInstant } from './geometry'
import { askSendUpdates, type SendKind } from './Prompts'
import { videoLink } from './QuickCreate'
import { useCalUI, type EditorInit } from './store'
import type { ApiEvent, CalEvent, EventWrite } from './types'
import { normalizeEvent } from './data'
import { registerEditor } from '../../lib/live/editors'
import { LiveDoc, type ConflictState } from '../../lib/live/machine'
import { byName } from '../../lib/live/store'
import { fetchLive, reportTyping, useLiveItem } from '../../lib/live/useLiveItem'
import { ConflictBar, EditingBanner } from '../live/LiveBars'
import { t as tr } from '../../i18n'

export type EditorMode = 'modal' | 'pane' | 'sheet'

interface Form {
  summary: string
  allDay: boolean
  startDay: ISODate
  startMin: number
  endDay: ISODate
  endMin: number
  zone: string
  rrule: string | null
  guests: Guest[]
  location: string
  url: string
  calendar: string
  color: string | null
  transparent: boolean
  private: boolean
  reminders: number[]
  /** A new event whose reminders nobody changed: the editor shows the defaults (the calendar's,
   *  else the user's) and the create leaves `reminders` out, so the server writes those same
   *  defaults into the event. */
  remindersAuto?: boolean
  description: string
  conversationId: number | string | null
  /** The sending identity of a new event (its ORGANIZER); null for the default. */
  identityId: number | null
}

const DRAFT = (key: string) => `mailr.calendar.draft.${key}`

function readDraft(key: string): Form | null {
  try {
    const raw = localStorage.getItem(DRAFT(key))
    return raw ? (JSON.parse(raw) as Form) : null
  } catch {
    return null
  }
}

function writeDraft(key: string, f: Form | null) {
  try {
    if (f) localStorage.setItem(DRAFT(key), JSON.stringify(f))
    else localStorage.removeItem(DRAFT(key))
  } catch {
    /* private mode */
  }
}

/** The form an editor opens with: the event (edit, duplicate) or the seed from quick create. */
export function initialForm(init: EditorInit, zone: string, defaults: { calendar?: string | null; duration: number; now: Date }): Form {
  const e = init.event
  if (e) {
    const allDay = e.allDay
    return {
      summary: init.mode === 'duplicate' ? e.summary : e.summary, allDay,
      startDay: allDay ? e.firstDay : toISODate(e.startAt, zone), startMin: allDay ? 9 * 60 : minutesOfDay(e.startAt, zone),
      endDay: allDay ? e.lastDay : toISODate(e.endAt, zone), endMin: allDay ? 10 * 60 : minutesOfDay(e.endAt, zone),
      zone, rrule: e.rrule ?? null,
      guests: (e.attendee_list ?? []).map((a) => ({ email: a.email, name: a.name, partstat: init.mode === 'duplicate' ? undefined : a.partstat, optional: a.role === 'OPT-PARTICIPANT' })),
      location: e.location ?? '', url: e.url ?? '', calendar: e.calendar ?? defaults.calendar ?? '', color: e.color ?? null,
      transparent: !!e.transparent, private: ['PRIVATE', 'CONFIDENTIAL'].includes((e.class ?? '').toUpperCase()),
      reminders: e.reminders ?? [], description: e.description ?? '', conversationId: e.conversation_id ?? null,
      // A copy of an event without reminders of its own (not marked "no reminders") starts from the
      // defaults, as a new event does: [] would mark the copy silent.
      remindersAuto: init.mode === 'duplicate' && !(e.reminders ?? []).length && !e.no_reminders ? true : undefined,
      identityId: null,
    }
  }
  const s = init.seed ?? {}
  const start = s.start ?? defaults.now
  const allDay = !!s.allDay
  const startDay = typeof start === 'string' ? start : toISODate(start, zone)
  const end = s.end ?? (typeof start === 'string' ? addDaysISO(start, 1) : new Date(start.getTime() + defaults.duration * 60_000))
  return {
    summary: s.summary ?? '', allDay, startDay,
    startMin: typeof start === 'string' ? 9 * 60 : minutesOfDay(start, zone),
    endDay: typeof end === 'string' ? (allDay ? addDaysISO(end, -1) : end) : toISODate(end, zone),
    endMin: typeof end === 'string' ? 10 * 60 : minutesOfDay(end, zone),
    zone, rrule: s.rrule ?? null, guests: s.guests ?? [], location: s.location ?? '',
    url: s.video ? 'pending-video' : '', calendar: s.calendar ?? defaults.calendar ?? '', color: null, transparent: false, private: false,
    reminders: s.reminders ?? [], remindersAuto: s.reminders === undefined,
    description: s.description ?? '', conversationId: s.conversationId ?? null,
    identityId: s.identityId ?? null,
  }
}

/** The instants (or dates) a form describes; a time in the spring-forward gap moves forward. */
export function formTimes(f: Form): { start: Date | ISODate; end: Date | ISODate; gap: string | null } {
  if (f.allDay) return { start: f.startDay, end: addDaysISO(f.endDay < f.startDay ? f.startDay : f.endDay, 1), gap: null }
  const start = gridInstant(f.startDay, f.startMin, f.zone)
  const end = gridInstant(f.endDay, f.endMin, f.zone)
  const got = minutesOfDay(start, f.zone)
  const gap = got !== f.startMin ? tr('calendar.eventEditor.doesNotExistOnThe', { hm: hm(f.startMin), date: formatDate(f.startDay, 'short'), hm2: hm(got) }) : null
  return { start, end, gap }
}

const hm = (m: number) => `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`

export function EventEditor({ init, mode }: { init: EditorInit; mode: EditorMode }) {
  const cal = useCal()
  const nav = useNavigate()
  const { prefs, actions } = cal
  const defaults = useDefaults().data
  const ev = init.event
  const draftKey = init.mode === 'edit' && ev ? ev.key : 'new'
  const initialBase = useMemo(() => initialForm(init, prefs.zone, { calendar: defaults?.calendar, duration: prefs.defaultDuration, now: nextSlot(cal.now) }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [init])
  // Live editing (lib/live): the form of the version this editor is based on. A change saved
  // elsewhere (the assistant, another device) moves it; the form follows when unchanged here.
  const [base, setBase] = useState<Form>(initialBase)
  const restored = useMemo(() => {
    const d = readDraft(draftKey)
    return d && JSON.stringify(d) !== JSON.stringify(base) && !init.seed ? d : null
  }, [draftKey, base, init.seed])
  const [f, setF] = useState<Form>(restored ?? base)
  const [notice, setNotice] = useState<string | null>(restored ? tr('calendar.eventEditor.draftRestored') : null)
  const [tab, setTab] = useState<'details' | 'findtime'>(init.tab ?? 'details')
  const [zones, setZones] = useState(false)
  const [asking, setAsking] = useState(false)
  const [saving, setSaving] = useState(false)
  const title = useRef<HTMLInputElement>(null)
  const set = (patch: Partial<Form>) => setF((cur) => ({ ...cur, ...patch }))
  const dirty = JSON.stringify(f) !== JSON.stringify(base)
  const close = () => useCalUI.getState().set({ editor: null })

  // ----- live editing -----
  const liveUid = init.mode === 'edit' && ev ? ev.uid : null
  const docRef = useRef<LiveDoc<Form & Record<string, unknown>> | null>(null)
  if (docRef.current === null) docRef.current = new LiveDoc<Form & Record<string, unknown>>({ description: 'lines' })
  const doc = docRef.current
  const [conflict, setConflict] = useState<ConflictState<Form & Record<string, unknown>> | null>(null)
  const fRef = useRef(f)
  fRef.current = f
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  /** The editor's form for a stored copy of this event (the occurrence keeps its own times). */
  const formOf = (content: Record<string, unknown>): Form => {
    if (!ev) return base
    const series = !!ev.recurring
    const merged = { ...ev, ...content, occurrence: ev.occurrence, ...(series ? { start: ev.start, end: ev.end, all_day: ev.all_day } : {}) } as ApiEvent
    const ce = normalizeEvent(merged, { zone: prefs.zone, collections: cal.collectionByUrl }) ?? ev
    return initialForm({ mode: 'edit', event: ce }, prefs.zone, { calendar: defaults?.calendar, duration: prefs.defaultDuration, now: nextSlot(cal.now) })
  }
  const onRemote = (theirs: { version: number | null; content: Record<string, unknown>; by?: string | null }, quiet = false) => {
    const form = formOf(theirs.content) as Form & Record<string, unknown>
    const d = doc.onRemote({ version: theirs.version, content: form, by: theirs.by }, fRef.current as Form & Record<string, unknown>, dirtyRef.current)
    if (d.type === 'replace') {
      setBase(form)
      setF(form)
      if (quiet) return
      // ACT event.live_update @web.editor: Undo puts the editor back (Save writes it as a new version).
      toast({ message: tr('calendar.eventEditor.updatedBy', { byName: byName(theirs.by) }), actionLabel: tr('common.undo'), duration: 10_000, onAction: () => setF(d.before as Form) })
    } else if (d.type === 'merge') {
      setBase(form)
      setF(d.content as Form)
      toast({ message: tr('calendar.eventEditor.changedThisEventTooBoth', { replace: byName(theirs.by).replace(/^./, (c) => c.toUpperCase()) }) })
    } else if (d.type === 'conflict') setConflict(d.conflict)
  }
  const editing = useLiveItem({ kind: 'event', id: liveUid, onRemote: (t) => onRemote(t), version: () => doc.version })
  // The version the editor starts from (the grid's copy has none): read it, and take a newer copy.
  useEffect(() => {
    if (!liveUid) return
    let alive = true
    fetchLive('event', liveUid).then((got) => {
      if (!alive || doc.base) return
      const form = formOf(got.content)
      if (JSON.stringify(form) === JSON.stringify(base)) doc.setBase(got.version, form as Form & Record<string, unknown>)
      else {
        // The copy the editor opened with is older: take the stored one (merged into any typing).
        doc.setBase(null, base as Form & Record<string, unknown>)
        onRemote({ version: got.version, content: got.content, by: got.by }, true)
      }
    }).catch(() => {})
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveUid])
  // The assistant sees what this editor shows, unsaved changes included (no autosave here: saving
  // an event with guests mails them, so it waits for Save).
  useEffect(() => {
    if (!liveUid) return
    return registerEditor('event-editor', {
      kind: 'event', id: () => liveUid, flush: async () => {},
      snapshot: () => {
        const cur = fRef.current
        const t = formTimes(cur)
        return {
          kind: 'event', id: liveUid, base_version: doc.version, dirty: dirtyRef.current || !!conflict, focused: true,
          fields: { summary: cur.summary, location: cur.location, description: cur.description, all_day: cur.allDay,
            start: typeof t.start === 'string' ? t.start : t.start.toISOString(), end: typeof t.end === 'string' ? t.end : t.end.toISOString() },
        }
      },
      focus: () => title.current?.focus(),
    })
  }, [liveUid, doc, conflict])
  useEffect(() => {
    if (liveUid && dirty) reportTyping('event', liveUid)
  }, [f, liveUid, dirty])

  useEffect(() => {
    title.current?.focus()
  }, [])
  useEffect(() => {
    // The video link from quick create ("with video") is made once, here.
    if (f.url === 'pending-video') set({ url: videoLink(prefs.videoTemplate) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const t = setTimeout(() => writeDraft(draftKey, dirty ? f : null), 400)
    return () => clearTimeout(t)
  }, [f, dirty, draftKey])
  useEffect(() => {
    if (!f.calendar && defaults?.calendar) set({ calendar: defaults.calendar })
  }, [defaults?.calendar, f.calendar])

  const identities = useMe().data?.identities ?? []
  const defaultIdentity = (identities.find((i) => i.is_default) ?? identities[0])?.id ?? null
  const writable = cal.collections.filter((c) => c.events !== false && !c.read_only && !c.error)
  const col = cal.collectionByUrl.get(f.calendar)
  const reminderSettings = useReminderSettings().data
  const shownReminders = f.remindersAuto ? (defaultReminders(col, f.allDay) ?? userDefaultReminders(reminderSettings, f.allDay)) : f.reminders
  const times = formTimes(f)
  const duration = f.allDay ? (diffDaysISO(f.startDay, f.endDay) + 1) * 1440 : Math.round(((times.end as Date).getTime() - (times.start as Date).getTime()) / 60_000)
  const endBad = !f.allDay ? duration <= 0 : f.endDay < f.startDay
  const clash = !f.allDay && !endBad ? conflictLine(cal.busy, times.start as Date, times.end as Date, ev?.key, prefs) : null

  // Changing the start moves the end and keeps the duration; changing the end changes the duration.
  const setStart = (day: ISODate, min: number) => {
    if (f.allDay) {
      const span = diffDaysISO(f.startDay, f.endDay)
      set({ startDay: day, endDay: addDaysISO(day, Math.max(0, span)) })
      return
    }
    const dur = Math.max(0, duration)
    const endAt = gridInstant(day, min, f.zone).getTime() + dur * 60_000
    const end = new Date(endAt)
    set({ startDay: day, startMin: ((min % 1440) + 1440) % 1440, endDay: toISODate(end, f.zone), endMin: minutesOfDay(end, f.zone) })
  }
  const setEnd = (day: ISODate, min: number) => {
    let endDay = day
    const endMin = ((min % 1440) + 1440) % 1440
    if (min >= 1440) endDay = addDaysISO(day, Math.floor(min / 1440))
    if (!f.allDay && endDay === f.startDay && endMin <= f.startMin && min < 1440) {
      // An end before the start on the same day moves to the next day.
      endDay = addDaysISO(f.startDay, 1)
      setNotice(tr('calendar.eventEditor.theEndMovedTo', { date: formatDate(endDay, 'day') }))
    }
    set({ endDay, endMin })
  }

  const payload = (): EventWrite => payloadOf(f)
  const payloadOf = (f: Form): EventWrite => {
    const t = formTimes(f)
    return {
      summary: f.summary.trim() || tr('common.noTitle'),
      ...timesFor(t.start, t.end, f.allDay),
      tz: f.allDay ? undefined : f.zone === prefs.zone ? prefs.homeZone : f.zone,
      location: f.location, description: f.description, calendar: f.calendar || undefined,
      recurrence: f.rrule ?? '', attendees: f.guests.map((g) => ({ email: g.email, name: g.name, role: g.optional ? 'OPT-PARTICIPANT' : 'REQ-PARTICIPANT' })),
      reminders: reminderField(f), transparent: f.transparent, class: f.private ? 'PRIVATE' : '', color: f.color, url: f.url || null,
    }
  }

  // Save first takes the guest text still in the field (an Enter may be waiting for the
  // suggestions): an address becomes a guest and Save goes on with it on the next render; other
  // text stops Save, and the field says why.
  const guestRef = useRef<GuestFieldHandle>(null)
  const [saveAfter, setSaveAfter] = useState(false)
  useEffect(() => {
    if (!saveAfter) return
    setSaveAfter(false)
    void save()
  }, [saveAfter]) // eslint-disable-line react-hooks/exhaustive-deps -- once, with the form that holds the new guests

  const save = async () => {
    if (saving || endBad) return
    const pending = guestRef.current?.commit()
    if (pending === null) return
    if (pending && pending !== f.guests) {
      setSaveAfter(true)
      return
    }
    const t = formTimes(f)
    if (t.gap) setNotice(t.gap)
    const body = payload()
    setSaving(true)
    try {
      if (init.mode === 'edit' && ev) {
        // ACT event.edit @web.editor: only the fields that changed are sent.
        // Only what changed since the version this editor is based on (live editing: the server
        // merges it with what others saved since then).
        const was = base
        const changes: EventWrite = {}
        const timesChanged = was.allDay !== f.allDay || was.startDay !== f.startDay || was.endDay !== f.endDay || (!f.allDay && (was.startMin !== f.startMin || was.endMin !== f.endMin)) || was.zone !== f.zone
        if (timesChanged) Object.assign(changes, timesFor(t.start, t.end, f.allDay))
        if (was.summary !== f.summary) changes.summary = body.summary
        if (was.location !== f.location) changes.location = f.location
        if (was.description !== f.description) changes.description = f.description
        if (was.rrule !== f.rrule) changes.recurrence = f.rrule ?? ''
        if (was.calendar !== f.calendar) changes.calendar = f.calendar
        if (was.color !== f.color) changes.color = f.color ?? '' // ACT event.set_color @web.editor
        if (was.transparent !== f.transparent) changes.transparent = f.transparent
        if (was.private !== f.private) changes.class = f.private ? 'PRIVATE' : 'PUBLIC'
        if (JSON.stringify(was.reminders) !== JSON.stringify(f.reminders)) changes.reminders = f.reminders
        if (was.url !== f.url) changes.url = f.url
        const guestsChanged = JSON.stringify(was.guests.map((g) => [g.email, !!g.optional])) !== JSON.stringify(f.guests.map((g) => [g.email, !!g.optional]))
        if (guestsChanged) changes.attendees = body.attendees
        // ACT event.move_calendar @web.editor: a new calendar moves the whole series through the move route
        // (UID kept), before the other changes are written to it.
        let target: CalEvent = ev
        if (changes.calendar) {
          delete changes.calendar
          let moved: Awaited<ReturnType<typeof calApi.move>>
          try {
            moved = await calApi.move(ev.uid, f.calendar, ev.etag)
          } catch (e) {
            toastError(e, tr('calendar.eventEditor.couldNotMoveTheEvent'))
            return
          }
          if (moved) {
            target = { ...ev, etag: moved.etag ?? ev.etag, calendar: f.calendar }
            if (!Object.keys(changes).length) toast({ message: tr('calendar.eventEditor.movedTo', { itemName: cal.collectionByUrl.get(f.calendar)?.name ?? tr('calendar.eventEditor.theCalendar') }) })
            actions.refresh()
          } else changes.calendar = f.calendar
        }
        if (!Object.keys(changes).length) {
          writeDraft(draftKey, null)
          close()
          return
        }
        const same = (a: Guest, b: Guest) => a.email.toLowerCase() === b.email.toLowerCase()
        const newGuests = f.guests.filter((g) => !was.guests.some((w) => same(w, g))).length
        const removed = was.guests.filter((w) => !f.guests.some((g) => same(w, g))).length
        const mailing = ['start', 'end', 'summary', 'location', 'recurrence', 'attendees', 'all_day', 'url'].some((k) => k in changes)
        if (mailing && (f.guests.length || removed) && ev.is_organizer !== false) {
          // A change of the guest list alone mails only the added guests (ACT event.invite @web.editor: the
          // invitation) and the removed ones (ACT event.uninvite @web.editor: a cancellation); any other change
          // goes to every guest.
          const onlyGuests = guestsChanged && Object.keys(changes).length === 1
          const ask: [number, SendKind] = !onlyGuests ? [f.guests.length + removed, 'update']
            : !removed ? [newGuests, 'invite'] : !newGuests ? [removed, 'uninvite'] : [newGuests + removed, 'update']
          setAsking(true)
          const send = await askSendUpdates(...ask)
          setAsking(false)
          if (!send) return
          changes.send_updates = send.send
          if (send.send && send.message) changes.message = send.message
        }
        const ruleOnly = Object.keys(changes).every((k) => k === 'recurrence' || k === 'send_updates')
        if (doc.version != null) changes.base_version = doc.version
        // The fields as they were, for the toast's Undo: the same keys, from the form as it opened.
        const before = payloadOf(was)
        const undo: EventWrite = {}
        for (const k of Object.keys(changes) as (keyof EventWrite)[]) {
          if (k === 'send_updates' || k === 'message' || k === 'base_version') continue
          ;(undo as Record<string, unknown>)[k] = k === 'color' ? before.color ?? '' : before[k] ?? (k === 'url' ? null : '')
        }
        const out = await actions.update(target, changes, { ruleChange: ruleOnly, undo: changes.calendar ? null : undo })
        if (out) {
          writeDraft(draftKey, null)
          close()
        }
        return
      }
      // ACT event.create @web.editor (also Duplicate: a new UID, no RSVP state, guests invited only on save)
      if (f.guests.length) {
        setAsking(true)
        const send = await askSendUpdates(f.guests.length, 'invite')
        setAsking(false)
        if (!send) return
        body.send_updates = send.send
        if (send.send && send.message) body.message = send.message
      }
      const full: EventWrite = {
        ...body, recurrence: f.rrule ?? undefined, attendees: f.guests.length ? body.attendees : undefined, color: f.color ?? undefined, url: f.url || undefined,
        ...(f.conversationId ? { conversation_id: f.conversationId } : {}),
        // The organizer identity ("From"), when there is a choice.
        ...(f.identityId ? { identity_id: f.identityId } : {}),
      }
      const out = await actions.create(full)
      if (out) {
        writeDraft(draftKey, null)
        close()
        init.onSaved?.(out, full)
      }
    } finally {
      setSaving(false)
    }
  }

  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const cancel = () => {
    if (dirty) setConfirmDiscard(true)
    else {
      writeDraft(draftKey, null)
      close()
    }
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      void save()
    } else if (e.key === 'Escape' && !asking && !confirmDiscard) {
      e.preventDefault()
      e.stopPropagation()
      cancel()
    }
  }

  const heading = init.mode === 'edit' ? tr('common.editEvent') : init.mode === 'duplicate' ? tr('calendar.eventEditor.duplicateEvent') : tr('common.newEvent')
  const zonesList = useMemo(() => {
    try {
      return (Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf('timeZone')
    } catch {
      return [prefs.zone]
    }
  }, [prefs.zone])

  const content = (
    <div className={`cal-editor m-${mode}`} role="dialog" aria-modal={mode !== 'pane'} aria-label={heading} data-testid="cal-editor" onKeyDown={onKey}>
      <header className="cal-editor-head">
        <button type="button" className="icon-btn sm" aria-label={tr('common.close')} onClick={cancel}><X size={20} /></button>
        <input
          ref={title}
          className="cal-editor-title"
          dir="auto"
          placeholder={tr('calendar.eventEditor.addTitle')}
          aria-label={tr('common.title')}
          value={f.summary}
          onChange={(e) => set({ summary: e.target.value })}
        />
        <button type="button" className="btn filled" onClick={() => void save()} disabled={saving || endBad || cal.readOnly || !f.calendar} title="Ctrl+Enter" data-testid="cal-editor-save">
          {f.guests.length && (init.mode !== 'edit' || !ev?.attendee_list?.length) ? tr('calendar.eventEditor.saveInvite') : tr('common.save')}
        </button>
      </header>
      <EditingBanner kind="event" editing={editing} />
      {conflict && (
        <ConflictBar kind="event" conflict={conflict}
          onMine={() => {
            // ACT event.resolve_conflict @web.editor
            if (doc.useMine()) setBase(doc.base!.content as Form)
            setConflict(null)
          }}
          onTheirs={() => {
            const t = doc.useTheirs()
            setConflict(null)
            if (t) {
              setBase(t as Form)
              setF(t as Form)
            }
          }} />
      )}
      {notice && (
        <div className="cal-notice" role="status">
          <span>{notice}</span>
          {notice === tr('calendar.eventEditor.draftRestored') && <button type="button" className="btn text sm" onClick={() => { setF(base); setNotice(null); writeDraft(draftKey, null) }}>{tr('calendar.eventEditor.discardDraft')}</button>}
          <button type="button" className="icon-btn xs" aria-label={tr('common.dismiss')} onClick={() => setNotice(null)}><X size={14} /></button>
        </div>
      )}
      <div className="cal-editor-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'details'} className={tab === 'details' ? 'on' : ''} onClick={() => setTab('details')}>{tr('calendar.eventEditor.eventDetails')}</button>
        <button type="button" role="tab" aria-selected={tab === 'findtime'} className={tab === 'findtime' ? 'on' : ''} onClick={() => setTab('findtime')}><CalendarSearch size={16} aria-hidden />{' '}{tr('common.findATime')}</button>
      </div>
      <div className="cal-editor-body">
        {tab === 'findtime' ? (
          <FindTimePanel
            duration={f.allDay ? prefs.defaultDuration : Math.max(15, duration)}
            guests={f.guests}
            exclude={ev?.key}
            onPick={(s) => {
              set({ allDay: false, startDay: toISODate(s.start, f.zone), startMin: minutesOfDay(s.start, f.zone), endDay: toISODate(s.end, f.zone), endMin: minutesOfDay(s.end, f.zone) })
              setTab('details')
              setNotice(tr('calendar.eventEditor.timeSet', { range: formatRange(s.start, s.end, false, prefs) }))
            }}
          />
        ) : (
          <>
            <Row icon={<Clock />} label={tr('common.when')}>
              <div className="cal-when">
                <DateField value={f.startDay} onChange={(d) => setStart(d, f.startMin)} label={tr('calendar.eventEditor.startDate')} now={cal.now} zone={f.zone} weekStart={prefs.weekStart} />
                {!f.allDay && <TimeField value={f.startMin} onChange={(m) => setStart(f.startDay, m)} label={tr('calendar.eventEditor.startTime')} step={prefs.snap} hour12={prefs.hour12} />}
                <span className="cal-when-dash">–</span>
                {!f.allDay && (
                  <TimeField
                    value={f.endMin}
                    onChange={(m) => setEnd(f.startDay, m)}
                    label={tr('calendar.eventEditor.endTime')}
                    step={prefs.snap}
                    hour12={prefs.hour12}
                    durationFrom={f.endDay === f.startDay ? f.startMin : undefined}
                    invalid={endBad}
                  />
                )}
                <DateField value={f.endDay} onChange={(d) => setEnd(d, f.endMin)} label={tr('calendar.eventEditor.endDate')} now={cal.now} zone={f.zone} weekStart={prefs.weekStart} invalid={endBad} />
              </div>
              <div className="cal-when-meta">
                <label className="cal-check"><input type="checkbox" className="checkbox" checked={f.allDay} onChange={(e) => set({ allDay: e.target.checked })} />{' '}{tr('common.allDay')}</label>
                <span className={`cal-duration${endBad ? ' bad' : ''}`}>{endBad ? tr('calendar.eventEditor.theEndIsBeforeThe') : formatDuration(duration)}</span>
                {!f.allDay && <button type="button" className="btn text sm" onClick={() => setZones(!zones)}><Globe size={14} aria-hidden />{' '}{tr('calendar.eventEditor.timeZones')}</button>}
              </div>
              {zones && !f.allDay && (
                <label className="cal-field inline"><span>{tr('common.timeZone')}</span>
                  <select className="input" value={f.zone} onChange={(e) => set({ zone: e.target.value })}>
                    {zonesList.map((z) => <option key={z} value={z}>{z.replace(/_/g, ' ')}</option>)}
                  </select>
                </label>
              )}
              {times.gap && <p className="cal-note warn">{times.gap}</p>}
              {clash && <p className="cal-clash">{clash}</p>}
            </Row>
            <Row icon={<Repeat />} label={tr('common.repeat')}>
              <RepeatPicker value={f.rrule} onChange={(r) => set({ rrule: r })} date={f.startDay} />
            </Row>
            <Row icon={<Users />} label={tr('calendar.eventEditor.guests')}>
              {init.mode !== 'edit' && identities.length > 1 && (
                // The identity that sends the invitations and becomes ORGANIZER.
                <label className="cal-field inline cal-from"><span>{tr('common.from')}</span>
                  <select className="input" aria-label={tr('common.from')} value={f.identityId ?? defaultIdentity ?? ''} onChange={(e) => set({ identityId: Number(e.target.value) || null })}>
                    {identities.map((i) => <option key={i.id} value={i.id}>{i.name ? `${i.name} <${i.email}>` : i.email}</option>)}
                  </select>
                </label>
              )}
              <GuestField guests={f.guests} onChange={(g) => set({ guests: g })} handle={guestRef} />
              {f.guests.length > 0 && <button type="button" className="btn text sm" onClick={() => setTab('findtime')}><CalendarSearch size={14} aria-hidden />{' '}{tr('common.findATime')}</button>}
            </Row>
            <Row icon={<MapPin />} label={tr('common.location')}>
              <input className="input" list="cal-places" placeholder={tr('calendar.eventEditor.addLocation')} aria-label={tr('common.location')} value={f.location} onChange={(e) => set({ location: e.target.value })} />
              <datalist id="cal-places">{[...new Set(cal.events.map((e) => e.location).filter(Boolean))].slice(0, 30).map((l) => <option key={l} value={l!} />)}<PlaceOptions guests={f.guests} skip={cal.events.map((e) => e.location)} /></datalist>
            </Row>
            <Row icon={<Video />} label={tr('calendar.eventEditor.video')}>
              {f.url ? (
                <span className="cal-video">
                  <a href={f.url} target="_blank" rel="noreferrer">{f.url.replace(/^https?:\/\//, '')}</a>
                  <button type="button" className="icon-btn xs" aria-label={tr('calendar.eventEditor.removeVideoLink')} onClick={() => set({ url: '' })}><X size={14} /></button>
                </span>
              ) : (
                <button type="button" className="btn tonal sm" onClick={() => set({ url: videoLink(prefs.videoTemplate) })}>{tr('calendar.eventEditor.addVideoLink')}</button> // ACT event.add_video @web.editor
              )}
            </Row>
            <Row icon={<CalendarDays />} label={tr('common.calendar')}>
              <div className="cal-calpick" role="radiogroup" aria-label={tr('common.calendar')}>
                {writable.map((c) => (
                  <label key={c.url ?? c.id} className={`cal-calopt${f.calendar === (c.url ?? c.id) ? ' on' : ''}`}>
                    <input type="radio" name="cal" checked={f.calendar === (c.url ?? c.id)} onChange={() => set({ calendar: c.url ?? c.id })} />
                    <span className="cal-dot" style={{ background: displayColour(cal.colourOf(c), cal.dark) }} />
                    {c.name}
                  </label>
                ))}
              </div>
            </Row>
            <Row icon={<Palette />} label={tr('common.colour')}>
              <ColourPicker value={f.color} onChange={(c) => set({ color: c })} calendarColour={displayColour(cal.colourOf(col), cal.dark)} dark={cal.dark} />
            </Row>
            <Row icon={<Eye />} label={tr('calendar.eventEditor.showAs')}>
              <div className="cal-erow-inline">
              <div className="cal-seg" role="radiogroup" aria-label={tr('calendar.eventEditor.showAs')}>
                <button type="button" role="radio" aria-checked={!f.transparent} className={!f.transparent ? 'on' : ''} onClick={() => set({ transparent: false })}>{tr('calendar.eventEditor.busy')}</button>
                <button type="button" role="radio" aria-checked={f.transparent} className={f.transparent ? 'on' : ''} onClick={() => set({ transparent: true })}>{tr('calendar.eventEditor.free')}</button>
              </div>
              <div className="cal-seg" role="radiogroup" aria-label={tr('calendar.eventEditor.visibility')}>
                <button type="button" role="radio" aria-checked={!f.private} className={!f.private ? 'on' : ''} onClick={() => set({ private: false })}>{tr('common.default')}</button>
                <button type="button" role="radio" aria-checked={f.private} className={f.private ? 'on' : ''} onClick={() => set({ private: true })}>{tr('calendar.eventEditor.private')}</button>
              </div>
              </div>
            </Row>
            <Row icon={<Bell />} label={tr('common.reminders')}>
              <ReminderChips value={shownReminders} onChange={(r) => set({ reminders: r, remindersAuto: false })} allDay={f.allDay} morningHour={prefs.morningHour} />
            </Row>
            <Row icon={<Text />} label={tr('common.description')}>
              <textarea className="input cal-textarea" rows={4} placeholder={tr('calendar.eventEditor.addDescription')} aria-label={tr('common.description')} value={f.description} onChange={(e) => set({ description: e.target.value })} />
              {/https?:\/\//.test(f.description) && (
                <p className="cal-desc-preview">{f.description.split(/(https?:\/\/[^\s<>"]+)/g).map((p, i) => (i % 2 ? <a key={i} href={p} target="_blank" rel="noreferrer">{p}</a> : p))}</p>
              )}
            </Row>
            {f.conversationId && (
              // ACT event.open_email @web.editor
              <Row icon={<Mail />} label={tr('common.email')}>
                <span className="cal-mailchip">
                  <button type="button" onClick={() => { close(); nav(mailPath('inbox', Number(f.conversationId))) }}>{tr('calendar.eventEditor.fromAnEmail')}</button>
                  <button type="button" className="icon-btn xs" aria-label={tr('calendar.eventEditor.unlinkTheEmail')} onClick={() => set({ conversationId: null })}><X size={14} /></button>
                </span>
              </Row>
            )}
          </>
        )}
      </div>
      <footer className="cal-editor-foot">
        {init.mode === 'edit' && ev && (
          <>
            <button type="button" className="btn text danger" onClick={() => { writeDraft(draftKey, null); close(); void actions.remove(ev) }}>{tr('common.delete2')}</button>
            {/* ACT event.duplicate @web.editor */}
            <button type="button" className="btn text" onClick={() => useCalUI.getState().set({ editor: { mode: 'duplicate', event: { ...ev, ...formAsEvent(f, ev) } } })}>{tr('common.duplicate')}</button>
            {/* ACT event.cancel @web.editor: the organizer cancels it for everyone, with a message. */}
            {guestCount(ev) > 0 && ev.is_organizer !== false && (ev.status ?? '').toUpperCase() !== 'CANCELLED' && !ev.readOnly && (
              <button type="button" className="btn text danger" onClick={() => { close(); void actions.cancel(ev) }}><Ban size={16} aria-hidden />{' '}{tr('common.cancelEvent')}</button>
            )}
          </>
        )}
        {f.guests.length > 0 && <EmailGuests f={f} ev={ev} onOpen={close} />}
        <span className="cal-spacer" />
        <button type="button" className="btn text" onClick={cancel}>{tr('common.cancel')}</button>
        <button type="button" className="btn filled" onClick={() => void save()} disabled={saving || endBad || cal.readOnly || !f.calendar}>{tr('common.save')}</button>
      </footer>
      <ConfirmDialog
        open={confirmDiscard}
        title={tr('calendar.eventEditor.discardChanges')}
        body={tr('calendar.eventEditor.yourChangesToThisEvent')}
        confirmLabel={tr('common.discard')}
        danger
        onClose={() => setConfirmDiscard(false)}
        onConfirm={() => { setConfirmDiscard(false); writeDraft(draftKey, null); close() }}
      />
    </div>
  )

  if (mode === 'pane') return <div className="cal-editor-pane">{content}</div>
  return createPortal(
    <div className={`scrim cal-scrim ${mode === 'sheet' ? 'sheet' : 'editor'}`} onMouseDown={(e) => { if (e.target === e.currentTarget) cancel() }}>
      {content}
    </div>,
    document.body,
  )
}

function Row({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="cal-erow">
      <span className="cal-erow-icon" aria-hidden title={label}>{icon}</span>
      <div className="cal-erow-body">{children}</div>
    </div>
  )
}

/** The next round half-hour (a new event with no slot). */
function nextSlot(now: Date): Date {
  const t = new Date(now.getTime())
  t.setSeconds(0, 0)
  t.setMinutes(t.getMinutes() < 30 ? 30 : 60)
  return t
}

/** The form's values as an event, for Duplicate from inside the editor. */
function formAsEvent(f: Form, ev: CalEvent): Partial<CalEvent> {
  const w = toWrite({ ...ev, summary: f.summary, location: f.location, description: f.description })
  return { summary: w.summary, location: f.location, description: f.description }
}

/**
 * ACT event.email_guests @web.editor from the editor: compose to all the guests, those who said yes, or those
 * who have not answered (the guests as the form has them). The editor closes; its draft stays on
 * this device until it is saved or discarded.
 */
function EmailGuests({ f, ev, onOpen }: { f: Form; ev?: CalEvent; onOpen: () => void }) {
  const me = useMe().data
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const mine = [me?.user?.email ?? '', ...(me?.identities ?? []).map((i) => i.email)]
  const people = { organizer: ev?.organizer ?? null, organizer_name: ev?.organizer_name, attendee_list: f.guests.map((g) => ({ email: g.email, name: g.name, partstat: g.partstat })) }
  const counts = groupCounts(people, mine)
  const open = (which: GuestGroup) => {
    setAnchor(null)
    const title = f.summary.trim() || tr('common.noTitle')
    const link = ev ? `<a href="${location.origin}${eventPath(ev.uid, ev.occurrence)}">${title}</a>` : title
    onOpen()
    useCompose.getState().open({ payload: { ...emptyPayload(), to: guestRecipients(people, which, mine), subject: title, html: `<p>${link}</p>` } })
  }
  return (
    <>
      <button type="button" className="btn text" aria-haspopup="menu" onClick={(e) => setAnchor(e.currentTarget)}>
        <Mail size={16} aria-hidden />{' '}{tr('calendar.eventEditor.emailGuests')}{' '}<ChevronDown size={14} aria-hidden />
      </button>
      <Popover anchor={anchor} open={!!anchor} onClose={() => setAnchor(null)} label={tr('calendar.eventEditor.emailGuests')} className="cal-editor-pop">
        {GUEST_GROUPS.map((g) => (
          <MenuItem key={g.id} disabled={!counts[g.id]} onSelect={() => open(g.id)}>{g.label} · {counts[g.id]}</MenuItem>
        ))}
      </Popover>
    </>
  )
}

// The calendar's verbs as the app runs them: each one asks the series question and whether to
// send updates when needed, writes through calApi with the version it saw, handles a change made
// elsewhere (the conflict sheet), and leaves an undo toast. Grid drags, keys, the detail and the
// editor all call these, so a change behaves the same whichever way it was made.

import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'
import { useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { addDaysISO, diffDaysISO, formatDate, formatRange, minutesOfDay, toISODate, utcIsoformat } from '../../lib/dates'
import { eventPath } from '../../lib/routes'
import { toast, toastError } from '../../stores/ui'
import { calApi, ConflictError, isGone, overridesLoaded, patchCached } from './data'
import { timeSpan } from './eventInfo'
import { gridInstant, needsScope, overlapping, singleOccurrence, type Busy } from './geometry'
import type { CalPrefs } from './prefs'
import { askCancel, askConflict, askScope, askSendUpdates, type IcsCopy } from './Prompts'
import type { ApiEvent, CalEvent, EventWrite, Scope } from './types'
import { t } from '../../i18n'

const UNDO_MS = 7000

let lastUndo: (() => void) | null = null

/** `z`: the last action's undo, while its toast is up. */
export function runLastUndo(): boolean {
  const fn = lastUndo
  if (!fn) return false
  lastUndo = null
  fn()
  return true
}

function offerUndo(message: string, undo: (() => void) | null, open?: () => void) {
  lastUndo = undo
  const run = undo ? () => { lastUndo = null; undo() } : undefined
  const id = open
    ? toast({ message, actionLabel: t('common.open'), onAction: open, ...(run ? { secondaryLabel: t('common.undo'), onSecondary: run } : {}), duration: UNDO_MS })
    : toast({ message, actionLabel: undo ? t('common.undo') : undefined, onAction: run, duration: UNDO_MS })
  if (undo) setTimeout(() => { if (lastUndo === undo) lastUndo = null }, UNDO_MS)
  return id
}

/** Guests other than me: the people a change would mail. */
export function guestCount(e: Pick<ApiEvent, 'attendee_list' | 'attendees' | 'is_organizer' | 'organizer'>): number {
  if (e.organizer && e.is_organizer === false) return 0
  // The organizer is often listed as an attendee too; that is me, not a guest.
  const org = (e.organizer ?? '').toLowerCase()
  return e.attendee_list ? e.attendee_list.filter((a) => a.email.toLowerCase() !== org).length : e.attendees ?? 0
}

function canWrite(): boolean {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    toast({ message: t('calendar.actions.offlineTheCalendarIsRead'), error: true })
    return false
  }
  return true
}

/** The times of a timed or all-day write. */
export function timesFor(start: Date | string, end: Date | string, allDay: boolean): Pick<EventWrite, 'start' | 'end' | 'all_day'> {
  if (allDay) return { start: start as string, end: end as string, all_day: true }
  return { start: utcIsoformat(start as Date), end: utcIsoformat(end as Date), all_day: false }
}

function describeTimes(start: Date | string, end: Date | string, allDay: boolean, p: CalPrefs): string {
  return formatRange(start, end, allDay, p)
}

/** "Overlaps Team sync 3–4 PM" (time conflicts), or null. */
export function conflictLine(busy: Busy[], start: Date, end: Date, exclude: string | undefined, p: CalPrefs): string | null {
  if (!p.conflicts) return null
  const hits = overlapping(busy, start, end, exclude)
  if (!hits.length) return null
  const first = hits[0]
  const when = timeSpan(first.start, first.end, p)
  const more = hits.length > 1 ? t('calendar.actions.andMore', { value: hits.length - 1 }) : ''
  return t('calendar.actions.overlaps', { summary: first.summary || t('common.noTitle'), when, value: first.calendarName ? ` (${first.calendarName})` : '', more })
}

export function busyOf(events: CalEvent[], collectionsBusy: (url?: string) => boolean = () => true): Busy[] {
  return events.map((e) => ({
    key: e.key, summary: e.summary, start: e.startAt, end: e.endAt, allDay: e.allDay, transparent: e.transparent || !collectionsBusy(e.calendar),
    declined: (e.my_status ?? '').toUpperCase() === 'DECLINED', cancelled: (e.status ?? '').toUpperCase() === 'CANCELLED',
  }))
}

/** Field values for the conflict sheet ("Time: Tue 6 Oct, 3:00 – 4:00 PM"). */
function fieldLines(e: Partial<ApiEvent>, keys: string[], p: CalPrefs): string[] {
  const out: string[] = []
  for (const k of keys) {
    if (k === 'start' || k === 'end') {
      if (!out.some((l) => l.startsWith(t('calendar.actions.time', { times: '' }))) && e.start) out.push(t('calendar.actions.time', { times: describeTimes(e.start, e.end ?? e.start, !!e.all_day, p) }))
    } else if (k === 'summary') out.push(t('calendar.actions.title', { summary: e.summary || t('common.noTitle') }))
    else if (k === 'location') out.push(t('calendar.actions.location', { location: e.location || '—' }))
    else if (k === 'description') out.push(t('calendar.actions.description', { description: (e.description || '—').slice(0, 120) }))
    else if (k === 'calendar') out.push(t('calendar.actions.calendarChanged'))
    else if (k !== 'all_day' && k !== 'tz') out.push(t('calendar.actions.changed', { k: k[0].toUpperCase(), k2: k.slice(1) }))
  }
  return out
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

interface WriteCtx {
  qc: QueryClient
  prefs: CalPrefs
}

/**
 * PATCH with conflict handling: a 409 reads the server copy; when the fields I changed are untouched
 * there, the change is applied again to the fresh copy; otherwise the user picks.
 */
async function patchWithConflict(c: WriteCtx, ev: ApiEvent, changes: EventWrite, scope: Scope, occurrence: string | null, gone: 'ask' | 'throw' = 'ask'): Promise<ApiEvent | null> {
  try {
    return await calApi.patch({ uid: ev.uid, changes, etag: ev.etag, scope, occurrence })
  } catch (e) {
    // A drop on an event deleted elsewhere is reverted with a toast (the caller's); an edit asks.
    if (isGone(e) && gone === 'throw') throw e
    if (isGone(e)) {
      const pick = await askConflict({ title: ev.summary, deleted: true, theirs: [], mine: [] })
      if (pick === 'restore') {
        const { uid: _u, etag: _e, ...rest } = toWrite({ ...ev, ...(changes as Partial<ApiEvent>) })
        void _u
        void _e
        return await calApi.create(rest)
      }
      return null
    }
    if (!(e instanceof ConflictError)) throw e
    // A retry after the choice below replaces the server's copy: no merge base (live editing).
    if (changes.base_version != null) {
      const { base_version: _bv, ...forced } = changes
      void _bv
      changes = forced
    }
    let server: ApiEvent
    try {
      server = await calApi.get(ev.uid)
    } catch (g) {
      if (isGone(g)) return patchWithConflict(c, { ...ev, etag: null }, changes, scope, occurrence)
      throw g
    }
    const keys = Object.keys(changes).filter((k) => k !== 'send_updates')
    const touched = keys.filter((k) => !same((server as unknown as Record<string, unknown>)[k], (ev as unknown as Record<string, unknown>)[k]))
    if (!touched.length) return await calApi.patch({ uid: ev.uid, changes, etag: server.etag, scope, occurrence })
    const pick = await askConflict({
      title: ev.summary, deleted: false, theirs: fieldLines(server, touched, c.prefs), mine: fieldLines({ ...ev, ...(changes as Partial<ApiEvent>) }, touched, c.prefs),
    })
    if (pick === 'mine') return await calApi.patch({ uid: ev.uid, changes, etag: server.etag, scope, occurrence })
    if (pick === 'both') {
      const { uid: _u, ...rest } = toWrite({ ...ev, ...(changes as Partial<ApiEvent>) })
      void _u
      return await calApi.create({ ...rest, attendees: undefined })
    }
    return null
  }
}

/** An event as a create payload (Duplicate, Keep both, Restore as new). */
export function toWrite(e: Partial<ApiEvent>): EventWrite & { etag?: string } {
  return {
    summary: e.summary || t('common.noTitle'), start: e.start, end: e.end ?? undefined, all_day: !!e.all_day, location: e.location || undefined,
    description: e.description || undefined, calendar: e.calendar, recurrence: e.rrule || undefined,
    // An event with no reminders of its own and no "no reminders" mark goes without `reminders`, so
    // the copy gets the default (as the original does) rather than being marked silent by [].
    reminders: e.reminders?.length || e.no_reminders ? e.reminders ?? [] : undefined,
    transparent: e.transparent || undefined, class: e.class || undefined, color: e.color || undefined,
    attendees: e.attendee_list?.map((a) => ({ email: a.email, name: a.name, role: a.role })),
    tz: e.tz && e.tz !== 'UTC' ? e.tz : undefined,
  }
}

/** The master's start moved by the same wall-clock change as one occurrence (scope "all"). */
async function shiftedMaster(ev: CalEvent, start: Date | string, end: Date | string, allDay: boolean, zone: string): Promise<{ master: ApiEvent; changes: EventWrite }> {
  const master = await calApi.get(ev.uid)
  if (allDay || ev.allDay) {
    const dDays = diffDaysISO(ev.firstDay, typeof start === 'string' ? start : toISODate(start, zone))
    const ms = master.start.slice(0, 10)
    const span = typeof start === 'string' && typeof end === 'string' ? diffDaysISO(start, end) : 1
    if (allDay) return { master, changes: { start: addDaysISO(ms, dDays), end: addDaysISO(addDaysISO(ms, dDays), span), all_day: true } }
  }
  const s = start as Date
  const e = end as Date
  const dDays = diffDaysISO(toISODate(ev.startAt, zone), toISODate(s, zone))
  const dMin = minutesOfDay(s, zone) - minutesOfDay(ev.startAt, zone)
  const mStart = new Date(master.start)
  const day = addDaysISO(toISODate(mStart, zone), dDays)
  const newStart = gridInstant(day, minutesOfDay(mStart, zone) + dMin, zone)
  const newEnd = new Date(newStart.getTime() + (e.getTime() - s.getTime()))
  return { master, changes: { start: utcIsoformat(newStart), end: utcIsoformat(newEnd), all_day: false } }
}

export function useCalActions(prefs: CalPrefs, busy: Busy[]) {
  const qc = useQueryClient()
  const nav = useNavigate()
  return useMemo(() => {
    const c: WriteCtx = { qc, prefs }
    const refresh = () => {
      void qc.invalidateQueries({ queryKey: ['calendar'] })
      void qc.invalidateQueries({ queryKey: ['reminders', 'active'] })
    }

    /**
     * Scope for a change to a series (null: the user backed out). "All events" says how many
     * changed occurrences keep their own time: the master's override_count, read while the
     * prompt is up (the loaded weeks count until it answers).
     */
    const scopeFor = async (ev: ApiEvent, verb: string, ics?: IcsCopy): Promise<Scope | null> => {
      if (!needsScope({ recurring: !!ev.recurring, single: singleOccurrence(ev.rrule) })) return 'all'
      const loaded = overridesLoaded(qc, ev.uid)
      const exceptions = typeof ev.override_count === 'number' ? ev.override_count
        : calApi.get(ev.uid).then((m) => m.override_count ?? loaded, () => loaded)
      return askScope(verb, exceptions, ics)
    }

    /** Send updates? (null: Back). Only when there are guests to tell. */
    const sendFor = async (ev: ApiEvent, kind: 'update' | 'cancel', ics?: IcsCopy): Promise<{ send: boolean; message: string } | null> => {
      const n = guestCount(ev)
      if (!n) return { send: true, message: '' }
      return askSendUpdates(n, kind, ics)
    }

    /** The event's .ics copy, offered before a delete or a cancel is confirmed. */
    const icsOf = (ev: ApiEvent): IcsCopy => ({ uid: ev.uid, summary: ev.summary })
    const withMessage = (send: { send: boolean; message: string }): Pick<EventWrite, 'send_updates' | 'message'> =>
      ({ send_updates: send.send, ...(send.send && send.message ? { message: send.message } : {}) })

    /**
     * ACT event.reschedule @web.calendar, ACT event.resize @web.calendar, ACT event.edit @web.calendar (timed ↔ all-day). New times are instants
     * for a timed result and ISO dates (end exclusive) for an all-day one.
     */
    const reschedule = async (ev: CalEvent, start: Date | string, end: Date | string, allDay: boolean, verb = t('common.move')): Promise<boolean> => {
      if (ev.readOnly) {
        toast({ message: t('common.readOnlyCalendar'), error: true })
        return false
      }
      if (!canWrite()) return false
      const scope = await scopeFor(ev, verb)
      if (!scope) return false
      const send = await sendFor(ev, 'update')
      if (!send) return false
      const times = timesFor(start, end, allDay)
      const prev = timesFor(ev.allDay ? ev.firstDay : ev.startAt, ev.allDay ? addDaysISO(ev.lastDay, 1) : ev.endAt, ev.allDay)
      // The grid moves at once; the server answer (or an error) settles it.
      patchCached(qc, ev.key, (e) => ({ ...e, start: times.start!, end: times.end ?? null, all_day: allDay }))
      try {
        let target: ApiEvent = ev
        let changes: EventWrite = { ...times, ...withMessage(send) }
        let undoChanges: EventWrite = { ...prev, send_updates: send.send }
        if (scope === 'all' && ev.recurring) {
          const m = await shiftedMaster(ev, start, end, allDay, prefs.zone)
          target = m.master
          changes = { ...m.changes, ...withMessage(send) }
          undoChanges = { start: m.master.start, end: m.master.end, all_day: !!m.master.all_day, send_updates: send.send }
        }
        const out = await patchWithConflict(c, target, changes, scope, ev.recurring && scope !== 'all' ? ev.occurrence : null, 'throw')
        refresh()
        if (!out) return false
        const where = describeTimes(times.start!, times.end ?? times.start!, allDay, prefs)
        const s = allDay ? null : new Date(times.start!)
        const line = s ? conflictLine(busy, s, new Date(times.end!), ev.key, prefs) : null
        const canUndo = scope !== 'following'
        offerUndo(t('calendar.actions.text', { value: (verb === t('calendar.calendarApp.resize') || verb === t('calendar.timeGrid.resize')) ? t('calendar.actions.changedTo') : t('calendar.actions.movedTo'), where, value2: line ? ` · ${line}` : '' }), canUndo ? () => {
          void calApi.patch({ uid: out.uid, changes: undoChanges, etag: out.etag, scope, occurrence: ev.recurring && scope !== 'all' ? ev.occurrence : null })
            .then(refresh, (e) => toastError(e, t('common.couldNotUndo')))
        } : null)
        return true
      } catch (e) {
        refresh()
        if (isGone(e)) toast({ message: t('calendar.actions.thisEventWasDeletedElsewhere'), error: true })
        else toastError(e, t('calendar.actions.couldNotMoveTheEvent'))
        return false
      }
    }

    /** ACT event.duplicate @web.calendar: a copy as a new event (new UID, no RSVP state, guests not invited). */
    const duplicateAt = async (ev: CalEvent, start: Date | string, end: Date | string, allDay: boolean): Promise<boolean> => {
      if (!canWrite()) return false
      const full = ev.recurring ? { ...ev, rrule: null } : ev
      const body = { ...toWrite(full), ...timesFor(start, end, allDay), attendees: undefined, recurrence: undefined }
      try {
        const out = await calApi.create(body)
        refresh()
        offerUndo(t('calendar.actions.duplicatedTo', { times: describeTimes(body.start!, body.end ?? body.start!, allDay, prefs) }), () => {
          void calApi.remove(out.uid, { etag: out.etag }).then(refresh, (e) => toastError(e, t('common.couldNotUndo')))
        })
        return true
      } catch (e) {
        toastError(e, t('calendar.actions.couldNotDuplicateTheEvent'))
        return false
      }
    }

    /** ACT event.delete @web.calendar (ACT event.cancel @web.sheet when I organise and there are guests): delayed 7 s, with undo. */
    const remove = async (ev: CalEvent): Promise<boolean> => {
      if (ev.readOnly) {
        toast({ message: t('common.readOnlyCalendar'), error: true })
        return false
      }
      if (!canWrite()) return false
      const scope = await scopeFor(ev, t('common.delete2'), icsOf(ev))
      if (!scope) return false
      const send = await sendFor(ev, 'cancel', icsOf(ev))
      if (!send) return false
      // Hide it now; the delete goes out when the undo window closes.
      const snapshot = qc.getQueriesData({ queryKey: ['calendar', 'range'] })
      if (scope === 'this' || !ev.recurring) patchCached(qc, ev.key, () => null)
      else {
        qc.setQueriesData<{ events: ApiEvent[] }>({ queryKey: ['calendar', 'range'] }, (old) => old && {
          ...old, events: old.events.filter((e) => e.uid !== ev.uid || (scope === 'following' && (e.occurrence ?? e.start) < ev.occurrence)),
        })
      }
      let undone = false
      const timer = setTimeout(() => {
        if (undone) return
        calApi.remove(ev.uid, { scope, occurrence: ev.recurring ? ev.occurrence : null, etag: ev.etag, sendUpdates: send.send, message: send.send ? send.message : '' })
          .then(refresh, (e) => {
            refresh()
            if (!(e instanceof ApiError && e.status === 404)) toastError(e, t('calendar.actions.couldNotDeleteTheEvent'))
          })
      }, UNDO_MS)
      const n = guestCount(ev)
      offerUndo(n && send.send ? t('calendar.actions.eventDeletedCancellationGoesTo', { count: n }) : t('calendar.actions.eventDeleted'), () => {
        undone = true
        clearTimeout(timer)
        for (const [key, data] of snapshot) qc.setQueryData(key, data)
        refresh()
      })
      return true
    }

    /**
     * ACT event.cancel: the organizer cancels an event that has guests. Scope for a series, then a
     * message; the event shows as cancelled at once and the cancel goes out when the 7 s undo
     * window closes (CANCEL through the outbox and its approval rules). The event stays, marked
     * cancelled.
     */
    const cancel = async (ev: CalEvent): Promise<boolean> => {
      if (ev.readOnly) {
        toast({ message: t('common.readOnlyCalendar'), error: true })
        return false
      }
      if (!canWrite()) return false
      const scope = await scopeFor(ev, t('common.cancel'), icsOf(ev))
      if (!scope) return false
      const n = guestCount(ev)
      const ask = await askCancel(ev.summary || t('common.noTitle'), n, icsOf(ev))
      if (!ask) return false
      const snapshot = qc.getQueriesData({ queryKey: ['calendar', 'range'] })
      const mark = (e: ApiEvent): ApiEvent => ({ ...e, status: 'CANCELLED' })
      if (scope === 'this' || !ev.recurring) patchCached(qc, ev.key, mark)
      else {
        qc.setQueriesData<{ events: ApiEvent[] }>({ queryKey: ['calendar', 'range'] }, (old) => old && {
          ...old, events: old.events.map((e) => e.uid !== ev.uid || (scope === 'following' && (e.occurrence ?? e.start) < ev.occurrence) ? e : mark(e)),
        })
      }
      let undone = false
      const timer = setTimeout(() => {
        if (undone) return
        calApi.cancel(ev.uid, { scope, occurrence: ev.recurring && scope !== 'all' ? ev.occurrence : null, etag: ev.etag, message: ask.message })
          .then(refresh, (e) => {
            refresh()
            toastError(e, t('calendar.actions.couldNotCancelTheEvent'))
          })
      }, UNDO_MS)
      offerUndo(t('calendar.actions.eventCancelledCancellationGoesTo', { count: n }), () => {
        undone = true
        clearTimeout(timer)
        for (const [key, data] of snapshot) qc.setQueryData(key, data)
        refresh()
      })
      return true
    }

    /** Save an event's fields from the editor or detail (ACT event.edit, ACT event.set_color @web.sheet @web.editor …). */
    const update = async (ev: CalEvent | ApiEvent, changes: EventWrite, o: { verb?: string; quiet?: boolean; guestsChanged?: boolean; ruleChange?: boolean; undo?: EventWrite | null; label?: string } = {}): Promise<ApiEvent | null> => {
      if ('readOnly' in ev && ev.readOnly) {
        toast({ message: t('common.readOnlyCalendar'), error: true })
        return null
      }
      if (!canWrite()) return null
      const occ = 'occurrence' in ev ? ev.occurrence ?? null : null
      let scope: Scope = 'all'
      if (!o.ruleChange) {
        const s = await scopeFor(ev, o.verb ?? t('calendar.actions.change'))
        if (!s) return null
        scope = s
      }
      const mailing = ['start', 'end', 'summary', 'location', 'recurrence', 'attendees', 'all_day', 'url'].some((k) => k in changes)
      if (mailing && changes.send_updates === undefined) {
        const send = await sendFor(ev, 'update')
        if (!send) return null
        changes = { ...changes, ...withMessage(send) }
      }
      try {
        let target: ApiEvent = ev
        if (scope === 'all' && ev.recurring && 'startAt' in ev && changes.start) {
          // The series keeps its first date; it moves by the same change as this occurrence.
          const allDay = changes.all_day ?? ev.allDay
          const m = await shiftedMaster(ev, allDay ? changes.start : new Date(changes.start), allDay ? changes.end! : new Date(changes.end ?? changes.start), allDay, prefs.zone)
          target = m.master
          changes = { ...changes, ...m.changes }
        }
        const shifted = target !== ev
        const out = await patchWithConflict(c, target, changes, scope, ev.recurring && scope !== 'all' ? occ : null)
        refresh()
        // The editor's Save offers Undo with the fields as they were (sent without mail to the guests).
        // Not for "this and following" (the series was split) or a series moved by one occurrence.
        const back = o.undo && scope !== 'following' && !shifted ? o.undo : null
        if (out && !o.quiet) {
          // ACT event.edit @web.editor: Undo restores the fields the save changed. The detail's
          // reminders and colour pass their own label ("Reminders changed", "Colour changed").
          offerUndo(o.label ?? t('calendar.actions.eventSaved'), back ? () => {
            void calApi.patch({ uid: out.uid, changes: { ...back, send_updates: false }, etag: out.etag, scope, occurrence: ev.recurring && scope !== 'all' ? occ : null })
              .then(refresh, (e) => toastError(e, t('common.couldNotUndo')))
          } : null)
        }
        return out
      } catch (e) {
        refresh()
        toastError(e, t('calendar.actions.couldNotSaveTheEvent'))
        return null
      }
    }

    /** ACT event.create @web.calendar */
    const create = async (body: EventWrite): Promise<ApiEvent | null> => {
      if (!canWrite()) return null
      try {
        const out = await calApi.create(body)
        refresh()
        const when = body.start ? describeTimes(body.start, body.end ?? body.start, !!body.all_day, prefs) : ''
        // Open and Undo, as every create toast.
        offerUndo(t('calendar.actions.eventCreated', { value: when ? ` · ${when}` : '' }), () => {
          void calApi.remove(out.uid, { etag: out.etag, sendUpdates: false }).then(refresh, (e) => toastError(e, t('common.couldNotUndo')))
        }, () => nav(eventPath(out.uid, out.occurrence ?? out.start)))
        return out
      } catch (e) {
        toastError(e, t('calendar.actions.couldNotCreateTheEvent'))
        return null
      }
    }

    /**
     * ACT event.rsvp @web.sheet @web.calendar: a series asks the series question (this occurrence, this and following, all; the REPLY
     * carries the RECURRENCE-ID), and the reply goes to the organizer.
     */
    const rsvp = async (ev: CalEvent, response: 'accepted' | 'tentative' | 'declined', comment = '') => {
      if (!canWrite()) return
      const scope = await scopeFor(ev, t('calendar.actions.replyTo'))
      if (!scope) return
      const occ = ev.recurring && scope !== 'all' ? ev.occurrence : null
      const before = ev.my_status
      patchCached(qc, ev.key, (e) => ({ ...e, my_status: response.toUpperCase() }))
      try {
        await calApi.rsvp(ev.uid, response, comment, scope, occ)
        refresh()
        const label = response === 'accepted' ? t('common.yes') : response === 'tentative' ? t('common.maybe') : t('common.no')
        const which = scope === 'this' && ev.recurring ? t('calendar.actions.toThisEvent') : scope === 'following' ? t('calendar.actions.toThisAndFollowingEvents') : ''
        offerUndo(t('calendar.actions.replied', { label, which }), before && before !== 'NEEDS-ACTION'
          ? () => void calApi.rsvp(ev.uid, before.toLowerCase() as 'accepted', '', scope, occ).then(refresh)
          : null)
      } catch (e) {
        refresh()
        toastError(e, t('calendar.actions.couldNotSendTheReply'))
      }
    }

    /** ACT event.move_calendar @web.sheet: the whole series moves; undo moves it back. */
    const moveCalendar = async (ev: CalEvent, calendar: string, name: string) => {
      if (!canWrite()) return
      const from = ev.calendar
      try {
        const out = await calApi.move(ev.uid, calendar, ev.etag)
        if (!out) {
          await update(ev, { calendar }, { verb: t('common.move'), ruleChange: true, quiet: true })
          return
        }
        refresh()
        offerUndo(t('calendar.actions.movedTo2', { itemName: name }), from ? () => void calApi.move(ev.uid, from, out.etag).then(refresh, (e) => toastError(e, t('common.couldNotUndo'))) : null)
      } catch (e) {
        refresh()
        toastError(e, t('calendar.actions.couldNotMoveTheEvent'))
      }
    }

    return { reschedule, duplicateAt, remove, cancel, update, create, rsvp, refresh, moveCalendar }
  }, [qc, prefs, busy, nav])
}

export type CalActions = ReturnType<typeof useCalActions>

/** "Tue 6 Oct" for toasts. */
export const dayLabel = (d: string) => formatDate(d, 'day')

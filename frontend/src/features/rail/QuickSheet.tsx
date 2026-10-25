// The quick-action sheet on the web (surface web.sheet), and the task and
// event editors (web.editor). Same actions, presets and undo as Android; a popover here.

import { useQueryClient } from '@tanstack/react-query'
import { AlarmClockPlus, ArrowLeft, CalendarClock, Check, Mail, Pencil, Trash2, X } from 'lucide-react'
import { useState, type CSSProperties, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import type { CalendarEvent, Task } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Popover } from '../../components/Popover'
import { describeMoment, offsetLabel, offsets, OFFSETS, type Choice } from '../../lib/quickChoices'
import { overdueText, type SheetSpec } from '../../lib/whenSheet'
import { smartOptions, summarizeRRule } from '../../lib/rrule'
import { taskPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { toast, toastError } from '../../stores/ui'
import { WhenSheet } from '../reminders/WhenSheet'
import { useReminderPrefs } from '../reminders/ReminderSheet'
import { useDefaults as useTaskDefaults, useTaskLists } from '../tasks/api'
import { t } from '../../i18n'

type Target = { kind: 'task'; task: Task } | { kind: 'event'; event: CalendarEvent }
type Mode = 'actions' | 'reschedule' | 'offset'

const isDateOnly = (s: string | null | undefined) => !!s && s.length === 10
const enc = encodeURIComponent

function localIso(d: Date): string {
  const off = -d.getTimezoneOffset()
  const sign = off >= 0 ? '+' : '-'
  const pad = (n: number) => String(Math.floor(Math.abs(n))).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00${sign}${pad(off / 60)}:${pad(off % 60)}`
}
const localDate = (d: Date) => localIso(d).slice(0, 10)

function useRefresh() {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: ['tasks'] })
    void qc.invalidateQueries({ queryKey: ['calendar'] })
  }
}

/** The when picker on the web: preset chips, then a day and a time; "No time" for tasks. */
function WhenPicker({ initial, presets, confirm, allowNoTime, initiallyDateOnly, onPick, onBack }: {
  initial: Date; presets: Choice[]; confirm: string; allowNoTime: boolean; initiallyDateOnly: boolean
  onPick: (at: Date, dateOnly: boolean) => void
  /** Back to the when sheet's presets. */
  onBack?: () => void
}) {
  const now = new Date()
  const [day, setDay] = useState(localDate(initial))
  const [time, setTime] = useState(localIso(initial).slice(11, 16))
  const [dateOnly, setDateOnly] = useState(initiallyDateOnly)
  const at = new Date(`${day}T${time}:00`)
  return (
    <div className="qs-picker">
      {presets.length > 0 && <div className="qs-chips">
        {presets.map((c) => (
          <button key={c.id} type="button" className="chip qs-chip" onClick={() => onPick(c.at, dateOnly && !['later', 'tonight'].includes(c.id))}>
            <b>{c.label}</b><span>{describeMoment(c.at, now, dateOnly && !['later', 'tonight'].includes(c.id))}</span>
          </button>
        ))}
      </div>}
      <div className="qs-when">
        <input type="date" className="input" value={day} min={localDate(now)} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label={t('common.day')} />
        <input type="time" className="input" step={300} value={time} disabled={dateOnly} onChange={(e) => e.target.value && setTime(e.target.value)} aria-label={t('common.time')} />
        {allowNoTime && (
          <label className="qs-notime"><input type="checkbox" className="checkbox" checked={dateOnly} onChange={(e) => setDateOnly(e.target.checked)} />{' '}{t('rail.quickSheet.noTime')}</label>
        )}
      </div>
      <div className="qs-result">{describeMoment(at, now, dateOnly)}</div>
      <div className="ws-foot">
        {onBack && <button type="button" className="btn text" onClick={onBack}>{t('rail.quickSheet.backToPresets')}</button>}
        <span className="ws-foot-gap" />
        <button type="button" className="btn filled" disabled={Number.isNaN(at.getTime())} onClick={() => onPick(at, dateOnly)}>{confirm}</button>
      </div>
    </div>
  )
}

/** The popover for one task or event occurrence. */
export function QuickSheet({ target, anchor, onClose, onEdit }: { target: Target; anchor: HTMLElement | null; onClose: () => void; onEdit: () => void }) {
  const refresh = useRefresh()
  const nav = useNavigate()
  const [mode, setMode] = useState<Mode>('actions')
  const prefs = useReminderPrefs()
  const now = new Date()

  const task = target.kind === 'task' ? target.task : null
  const event = target.kind === 'event' ? target.event : null
  const due = task?.due ? new Date(isDateOnly(task.due) ? `${task.due}T09:00:00` : task.due) : null
  const dateOnly = isDateOnly(task?.due)
  const start = event ? new Date(event.all_day ? `${event.start.slice(0, 10)}T00:00:00` : event.start) : null
  const end = event?.end ? new Date(event.end) : null
  const started = !!start && start <= now
  const title = task?.summary || event?.summary || '(untitled)'
  const conv = task?.conversation_id ?? event?.conversation_id ?? null

  /** Runs an action, closes, and offers Undo. */
  const act = async (message: string, fn: () => Promise<unknown>, undo?: () => Promise<unknown>) => {
    onClose()
    try {
      await fn()
      refresh()
      toast({ message, ...(undo ? { actionLabel: t('common.undo'), onAction: () => void undo().then(refresh).catch((e) => toastError(e, t('common.couldNotUndo'))) } : {}) })
    } catch (e) {
      toastError(e, t('rail.quickSheet.couldNotDoThat'))
    }
  }

  // ACT task.complete @web.sheet
  const complete = () => act(t('rail.quickSheet.done', { title }),
    () => api.patch(`/tasks/${enc(task!.uid)}`, { completed: true }),
    () => api.patch(`/tasks/${enc(task!.uid)}`, { completed: false }))
  // ACT task.reschedule @web.sheet @web.editor
  const rescheduleTask = (at: Date, only: boolean) => act(t('rail.quickSheet.due', { moment: describeMoment(at, new Date(), only) }),
    () => api.patch(`/tasks/${enc(task!.uid)}`, { due: only ? localDate(at) : localIso(at) }),
    () => api.patch(`/tasks/${enc(task!.uid)}`, task!.due ? { due: task!.due } : { clear_due: true }))
  // ACT event.reschedule @web.sheet @web.editor (this occurrence, same length)
  const rescheduleEvent = (at: Date) => {
    const len = start && end ? end.getTime() - start.getTime() : 60 * 60_000
    const scope = event!.recurring ? { scope: 'this', occurrence: event!.recurrence_id ?? event!.start } : {}
    const newEnd = new Date(at.getTime() + len)
    return act(t('rail.quickSheet.movedTo', { moment: describeMoment(at, new Date()) }),
      () => api.patch(`/calendar/events/${enc(event!.uid)}`, { start: localIso(at), end: localIso(newEnd), ...scope }),
      () => api.patch(`/calendar/events/${enc(event!.uid)}`, { start: event!.start, end: event!.end, ...(event!.recurring ? { scope: 'this', occurrence: localIso(at) } : {}) }))
  }
  // ACT event.remind_before @web.sheet (a one-off reminder the phone fires)
  const remindBefore = (m: number) => act(t('rail.quickSheet.yourPhoneRemindsYou', { offsetLabel: offsetLabel(m).toLowerCase() }),
    () => api.post('/reminders/add', { kind: 'e', uid: event!.uid, occurrence: event!.recurrence_id ?? event!.start, minutes: m, handle: [] }))
  // ACT task.delete @web.sheet @web.editor, ACT event.delete @web.sheet @web.editor
  const remove = () => task
    ? act(t('rail.quickSheet.taskDeleted'), () => api.del(`/tasks/${enc(task.uid)}`))
    : act(t('rail.quickSheet.eventDeleted'), () => api.del(`/calendar/events/${enc(event!.uid)}` +
        (event!.recurring ? `?scope=this&occurrence=${enc(event!.recurrence_id ?? event!.start)}` : '')))
  // ACT event.rsvp @web.sheet
  const rsvp = (response: string) => act(response === 'accepted' ? t('rail.quickSheet.going') : response === 'declined' ? t('rail.quickSheet.notGoing') : t('common.maybe'),
    () => api.post(`/calendar/events/${enc(event!.uid)}/rsvp`, { response }))

  let whenLine: string
  if (task) whenLine = due ? t('rail.quickSheet.due', { moment: describeMoment(due, now, dateOnly) }) : t('common.noDueDate')
  else if (event?.all_day) whenLine = t('rail.quickSheet.allDay', { moment: describeMoment(start!, now, true) })
  else {
    const mins = Math.round((start!.getTime() - now.getTime()) / 60_000)
    whenLine = mins > 0 && mins <= 90 ? t('rail.quickSheet.startsInMin', { mins }) : started ? t('common.inProgress') : t('rail.quickSheet.starts', { moment: describeMoment(start!, now) })
  }
  if (event?.location) whenLine += ` · ${event.location}`
  const overdue = task && due && task.status !== 'completed' ? overdueText(due, now, dateOnly) : null
  const spec = (verb: SheetSpec['verb']): SheetSpec => ({ verb, kind: task ? 'task' : 'event', now, anchor: task ? due : start, dateOnly, morningHour: prefs.morningHour })

  const primary: { label: string; icon: ReactNode; onClick: () => void }[] = task
    ? [{ label: t('common.done'), icon: <Check size={18} />, onClick: () => void complete() },
       { label: t('common.reschedule'), icon: <CalendarClock size={18} />, onClick: () => setMode('reschedule') }]
    : [
        ...(!started && start && offsets(now, start).length ? [{ label: t('rail.quickSheet.remindBefore'), icon: <AlarmClockPlus size={18} />, onClick: () => setMode('offset') }] : []),
        ...(!started ? [{ label: t('common.reschedule'), icon: <CalendarClock size={18} />, onClick: () => setMode('reschedule') }] : []),
      ]

  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={title} placement="left-start" minWidth={320}>
      <div className="qs">
        <div className="qs-head">
          {mode !== 'actions' && <button type="button" className="icon-btn sm" aria-label={t('common.back')} onClick={() => setMode('actions')}><ArrowLeft size={18} /></button>}
          <div>
            <div className="qs-title">{title}</div>
            <div className="qs-when-line">{whenLine}{overdue && <span className="ws-badge overdue">{overdue}</span>}</div>
          </div>
        </div>
        {mode === 'actions' && (
          <>
            {primary.length > 0 && (
              <div className="ws-acts" style={{ '--n': primary.length } as CSSProperties}>
                {primary.map((b, i) => <button key={b.label} type="button" className={`ws-act${i === 0 ? ' ws-main' : ''}`} onClick={b.onClick}>{b.icon}{b.label}</button>)}
              </div>
            )}
            <div className="ws-sec">
              <button type="button" className="btn text sm" onClick={() => { onClose(); onEdit() }}><Pencil size={16} />{' '}{t('common.edit2')}</button>
              {/* ACT task.open_email @web.sheet, ACT event.open_email @web.sheet */}
              {conv != null && <button type="button" className="btn text sm" onClick={() => { onClose(); nav(mailPath('all', conv)) }}><Mail size={16} />{' '}{t('common.email')}</button>}
              <span className="ws-sec-gap" />
              <button type="button" className="btn text sm ws-danger" onClick={() => void remove()}><Trash2 size={16} />{' '}{t('common.delete2')}</button>
            </div>
            {event?.my_status && !event.is_organizer && (
              <div className="ws-sec">
                <span className="muted">{t('common.going')}</span>
                {[['accepted', t('common.yes')], ['tentative', t('common.maybe')], ['declined', t('common.no')]].map(([v, l]) => (
                  <button key={v} type="button" className="btn text sm" aria-pressed={event.my_status?.toLowerCase() === v} onClick={() => void rsvp(v)}>{l}</button>
                ))}
              </div>
            )}
          </>
        )}
        {/* ACT task.reschedule: the when sheet; the due moves. */}
        {mode === 'reschedule' && task && (
          <WhenSheet spec={spec('reschedule')} confirm={t('common.reschedule')} groupLabel={t('rail.quickSheet.rescheduleTo')}
            onPick={(at, only) => void rescheduleTask(at, only)}
            full={(close) => (
              <WhenPicker initial={due ?? new Date(now.getTime() + 86_400_000)} presets={[]} confirm={t('common.reschedule')}
                allowNoTime initiallyDateOnly={dateOnly} onPick={(at, only) => void rescheduleTask(at, only)} onBack={close} />
            )} />
        )}
        {/* ACT event.reschedule: the when sheet; the length stays. */}
        {mode === 'reschedule' && event && start && (
          <WhenSheet spec={spec('reschedule')} confirm={t('common.move')} groupLabel={t('rail.quickSheet.moveTo')}
            onPick={(at) => void rescheduleEvent(at)}
            full={(close) => (
              <WhenPicker initial={start} presets={[]} confirm={t('common.move')} allowNoTime={false} initiallyDateOnly={false}
                onPick={(at) => void rescheduleEvent(at)} onBack={close} />
            )} />
        )}
        {/* ACT event.remind_before */}
        {mode === 'offset' && start && (
          <WhenSheet spec={spec('remind')} confirm={t('common.remindMe')} groupLabel={t('rail.quickSheet.remindMeBeforeItStarts')}
            onPick={(at, _d, tile) => void remindBefore(tile?.minutes ?? Math.max(0, Math.round((start.getTime() - at.getTime()) / 60_000)))} />
        )}
      </div>
    </Popover>
  )
}

/** Delete from an editor (ACT task.delete, ACT event.delete on web.editor): the footer's left end. */
function DeleteButton({ what, onDelete, onDone }: { what: string; onDelete: () => Promise<unknown>; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  return (
    <button type="button" className="btn text qs-delete" disabled={busy} onClick={() => {
      setBusy(true)
      onDelete().then(() => { toast({ message: t('rail.quickSheet.deleted', { what: what[0].toUpperCase(), what2: what.slice(1) }) }); onDone() })
        .catch((e) => toastError(e, t('rail.quickSheet.couldNotDeleteThe', { what }))).finally(() => setBusy(false))
    }}><Trash2 size={16} />{' '}{t('common.delete2')}</button>
  )
}

/** A task as the editor reads it: the task routes add the rule and the absolute reminders. */
type EditTask = Task & { rrule?: string | null; repeat_from?: string | null; reminders_at?: string[] | null }

/**
 * web.editor for tasks: ACT task.edit @web.sheet @web.editor, ACT task.reschedule (the due fields), ACT task.move_list @web.editor
 * (the list; subtasks follow through POST /tasks/{uid}/move), ACT task.set_repeat @web.editor (a smart repeat
 * for the due day, from the due or from completion), ACT task.remind_at @web.editor (reminders before the due,
 * and at a moment). Create when `task` is null (ACT task.create @web.calendar), from a suggestion (`seed`) and
 * linked to a conversation when one is given (ACT mail.to_task @web.reader @web.list).
 */
export function TaskEditor({ task: t0, onClose, seed, conversationId, heading, saveLabel, createdLabel, onCreated }: {
  task: Task | null
  onClose: () => void
  seed?: { summary?: string; notes?: string; due?: string | null; list?: string | null }
  conversationId?: number
  heading?: string
  saveLabel?: string
  /** The toast after a create from something else (an email, a note), with Open and Undo. */
  createdLabel?: string
  /** A new task was saved (its uid): a note links it. */
  onCreated?: (uid: string) => void
}) {
  const task = t0 as EditTask | null
  const nav = useNavigate()
  const refresh = useRefresh()
  const lists = (useTaskLists().data ?? []).filter((l) => !l.read_only)
  const defaults = useTaskDefaults().data
  const [summary, setSummary] = useState(task?.summary ?? seed?.summary ?? '')
  const [notes, setNotes] = useState(task?.notes ?? seed?.notes ?? '')
  const [day, setDay] = useState(task?.due ? task.due.slice(0, 10) : seed?.due ? seed.due.slice(0, 10) : '')
  const [time, setTime] = useState(task?.due && !isDateOnly(task.due) ? localIso(new Date(task.due)).slice(11, 16) : '')
  const [priority, setPriority] = useState(task?.priority ?? 0)
  const [reminders, setReminders] = useState<number[]>(task?.reminders ?? [])
  const [remindAt, setRemindAt] = useState<string[]>(task?.reminders_at ?? [])
  const [list, setList] = useState<string>(task?.list ?? seed?.list ?? '')
  const [rrule, setRrule] = useState<string | null>(task?.rrule ?? null)
  const [repeatFrom, setRepeatFrom] = useState<string>(task?.repeat_from ?? 'due')
  const [busy, setBusy] = useState(false)
  const listValue = list || defaults?.task_list || lists[0]?.id || ''
  const anchorDay = day || localDate(new Date())
  const due = day ? (time ? localIso(new Date(`${day}T${time}:00`)) : day) : rrule ? anchorDay : null
  const save = async () => {
    setBusy(true)
    try {
      const body: Record<string, unknown> = { summary: summary.trim(), notes, priority, reminders: due ? reminders : [], ...(due ? { due } : task ? { clear_due: true } : {}) }
      if (task) {
        if ((task.rrule ?? null) !== rrule) body.recurrence = rrule ?? ''
        if (rrule && (task.repeat_from ?? 'due') !== repeatFrom) body.repeat_from = repeatFrom
        if (JSON.stringify(task.reminders_at ?? []) !== JSON.stringify(remindAt)) body.reminders_at = remindAt
        await api.patch(`/tasks/${enc(task.uid)}`, body)
        // A new list moves the task (its subtasks follow).
        if (listValue && task.list && listValue !== task.list) await api.post(`/tasks/${enc(task.uid)}/move`, { list: listValue })
      } else {
        // A new task leaves "no priority" out (the server takes 1-9 there).
        const { priority: pri, clear_due: _c, ...rest } = body as typeof body & { clear_due?: boolean }
        const create: Record<string, unknown> = {
          ...rest, ...(pri ? { priority: pri } : {}), ...(listValue ? { list: listValue } : {}),
          ...(rrule ? { recurrence: rrule, repeat_from: repeatFrom } : {}), ...(remindAt.length ? { reminders_at: remindAt } : {}),
        }
        const made = await api.post<{ uid?: string; task?: { uid?: string } } | null>('/tasks', conversationId ? { ...create, conversation_id: conversationId } : create)
        const uid = made?.uid ?? made?.task?.uid ?? null
        if (uid) onCreated?.(uid)
        // Made from an email or a note: Open and Undo, as every create toast.
        if (conversationId || createdLabel) {
          toast({
            message: createdLabel ?? t('rail.quickSheet.taskAddedFromTheEmail'),
            ...(uid ? {
              actionLabel: t('common.open'), onAction: () => nav(taskPath(uid)),
              secondaryLabel: t('common.undo'), onSecondary: () => void api.del(`/tasks/${enc(uid)}`).then(refresh, (e) => toastError(e, t('common.couldNotUndo'))),
            } : {}),
            duration: 7000,
          })
        }
      }
      refresh()
      onClose()
    } catch (e) {
      toastError(e, t('rail.quickSheet.couldNotSaveTheTask'))
    } finally {
      setBusy(false)
    }
  }
  const options = smartOptions(anchorDay).filter((o) => o.id !== 'custom')
  const known = !rrule || options.some((o) => o.rrule === rrule)
  return (
    <Dialog open onClose={onClose} title={heading ?? (task ? t('rail.quickSheet.editTask') : t('common.newTask'))}
      footer={<>{task && <DeleteButton what="task" onDelete={() => api.del(`/tasks/${enc(task.uid)}`)} onDone={() => { refresh(); onClose() }} />}
        <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
        <button type="button" className="btn filled" disabled={!summary.trim() || busy} onClick={() => void save()}>{saveLabel ?? t('common.save')}</button></>}>
      <div className="qs-form">
        <label>{t('common.title')}<input className="input" autoFocus value={summary} onChange={(e) => setSummary(e.target.value)} /></label>
        <div className="qs-row">
          <label>{t('common.due')}<input type="date" className="input" value={day} onChange={(e) => setDay(e.target.value)} /></label>
          <label>{t('common.time')}<input type="time" className="input" step={300} value={time} disabled={!day} onChange={(e) => setTime(e.target.value)} /></label>
          <label>{t('common.priority')}
            <select className="input" value={priority} onChange={(e) => setPriority(Number(e.target.value))}>
              <option value={0}>{t('common.none')}</option><option value={1}>{t('common.high')}</option><option value={5}>{t('common.medium')}</option><option value={9}>{t('common.low')}</option>
            </select>
          </label>
        </div>
        <div className="qs-row">
          {lists.length > 0 && (
            <label>{t('common.list')}
              <select className="input" value={listValue} onChange={(e) => setList(e.target.value)}>
                {lists.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            </label>
          )}
          <label>{t('common.repeat')}
            <select className="input" value={rrule ?? ''} onChange={(e) => setRrule(e.target.value || null)}>
              {options.map((o) => <option key={o.id} value={o.rrule ?? ''}>{o.label}</option>)}
              {!known && <option value={rrule!}>{summarizeRRule(rrule, anchorDay)}</option>}
            </select>
          </label>
          {rrule && (
            <label>{t('rail.quickSheet.nextOneCountsFrom')}
              <select className="input" value={repeatFrom} onChange={(e) => setRepeatFrom(e.target.value)}>
                <option value="due">{t('rail.quickSheet.theDueDate')}</option><option value="completion">{t('rail.quickSheet.completion')}</option>
              </select>
            </label>
          )}
        </div>
        <ReminderChips value={reminders} onChange={setReminders} disabled={!due} anchorLabel="due" />
        <RemindAtField value={remindAt} onChange={setRemindAt} />
        <label>{t('common.notes')}<textarea className="textarea" rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
      </div>
    </Dialog>
  )
}

/** ACT task.remind_at at a moment: absolute reminders (reminders_at), with or without a due. */
function RemindAtField({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [at, setAt] = useState('')
  const now = new Date()
  const add = () => {
    const d = new Date(at)
    if (Number.isNaN(d.getTime())) return
    const iso = d.toISOString()
    if (!value.includes(iso)) onChange([...value, iso].sort())
    setAt('')
  }
  return (
    <fieldset className="qs-reminders">
      <legend>{t('rail.quickSheet.remindersAtASetTime')}</legend>
      <div className="qs-chips">
        {value.map((v) => (
          <span key={v} className="chip" data-testid="remind-at">
            {describeMoment(new Date(v), now)}
            <button type="button" className="icon-btn xs" aria-label={t('rail.quickSheet.removeTheReminderAt', { moment: describeMoment(new Date(v), now) })} onClick={() => onChange(value.filter((x) => x !== v))}><X size={12} /></button>
          </span>
        ))}
        <input type="datetime-local" className="input" step={300} value={at} aria-label={t('common.remindMeAt')} onChange={(e) => setAt(e.target.value)} />
        <button type="button" className="btn text sm" disabled={!at} onClick={add}>{t('common.add')}</button>
      </div>
    </fieldset>
  )
}

/** web.editor for events: ACT event.edit @web.sheet @web.editor, ACT event.reschedule (this occurrence for a repeating event). */
export function EventEditor({ event, onClose }: { event: CalendarEvent; onClose: () => void }) {
  const refresh = useRefresh()
  const s0 = new Date(event.start)
  const e0 = event.end ? new Date(event.end) : new Date(s0.getTime() + 3_600_000)
  const [summary, setSummary] = useState(event.summary)
  const [location, setLocation] = useState(event.location ?? '')
  const [description, setDescription] = useState(event.description ?? '')
  const [start, setStart] = useState(localIso(s0).slice(0, 16))
  const [end, setEnd] = useState(localIso(e0).slice(0, 16))
  const [reminders, setReminders] = useState<number[]>(event.reminders ?? [])
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await api.patch(`/calendar/events/${enc(event.uid)}`, {
        summary, location, description, reminders,
        ...(event.all_day ? {} : { start: localIso(new Date(start)), end: localIso(new Date(end)) }),
        ...(event.recurring ? { scope: 'this', occurrence: event.recurrence_id ?? event.start } : {}),
      })
      refresh()
      onClose()
    } catch (e) {
      toastError(e, t('rail.quickSheet.couldNotSaveTheEvent'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open onClose={onClose} title={event.recurring ? t('rail.quickSheet.editThisOccurrence') : t('common.editEvent')}
      footer={<><DeleteButton what={event.recurring ? 'occurrence' : 'event'} onDone={() => { refresh(); onClose() }}
          onDelete={() => api.del(`/calendar/events/${enc(event.uid)}` +
            (event.recurring ? `?scope=this&occurrence=${enc(event.recurrence_id ?? event.start)}` : ''))} />
        <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
        <button type="button" className="btn filled" disabled={!summary.trim() || busy} onClick={() => void save()}>{t('common.save')}</button></>}>
      <div className="qs-form">
        <label>{t('common.title')}<input className="input" autoFocus value={summary} onChange={(e) => setSummary(e.target.value)} /></label>
        {!event.all_day && (
          <div className="qs-row">
            <label>{t('common.starts')}<input type="datetime-local" className="input" step={300} value={start} onChange={(e) => {
              const len = new Date(end).getTime() - new Date(start).getTime()
              setStart(e.target.value)
              setEnd(localIso(new Date(new Date(e.target.value).getTime() + len)).slice(0, 16))
            }} /></label>
            <label>{t('common.ends')}<input type="datetime-local" className="input" step={300} value={end} min={start} onChange={(e) => setEnd(e.target.value)} /></label>
          </div>
        )}
        <label>{t('common.location')}<input className="input" value={location} onChange={(e) => setLocation(e.target.value)} /></label>
        <ReminderChips value={reminders} onChange={setReminders} anchorLabel="start" />
        <label>{t('common.notes')}<textarea className="textarea" rows={4} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
      </div>
    </Dialog>
  )
}

/** Reminder offsets in an editor: a multi-select of chips. */
function ReminderChips({ value, onChange, disabled, anchorLabel }: { value: number[]; onChange: (v: number[]) => void; disabled?: boolean; anchorLabel: string }) {
  return (
    <fieldset className="qs-reminders" disabled={disabled}>
      <legend>{t('rail.quickSheet.reminders', { value: disabled ? t('rail.quickSheet.setADueDateFirst') : '' })}</legend>
      <div className="qs-chips">
        {OFFSETS.map((m) => (
          <button key={m} type="button" className="chip" aria-pressed={value.includes(m)}
            onClick={() => onChange(value.includes(m) ? value.filter((x) => x !== m) : [...value, m].sort((a, b) => a - b))}>
            {m === 0 ? t('rail.quickSheet.at', { anchorLabel }) : offsetLabel(m)}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

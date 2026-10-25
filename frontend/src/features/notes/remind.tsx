// Reminders on notes: absolute times only, on the shared reminder
// contract (key n|<id>|abs|<instant>). The menu offers the presets (Later today,
// Tomorrow, Next week) and Pick… (a date and a time). A reminder is the viewer's own: a sharee's
// reminder reminds only them. Snooze and Dismiss happen in the reminder tray, as for events and tasks.

import { useQueryClient } from '@tanstack/react-query'
import { Bell, BellOff, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useSettings } from '../../api/hooks'
import { MenuHeader, MenuItem, MenuSep } from '../../components/Popover'
import { addDaysISO, formatDate, formatTime, fromZoned, parseISODate, resolveDatePrefs, todayISO, toISODate, weekdayOf, zoneParts, type DatePrefs } from '../../lib/dates'
import { setReminders } from './actions'
import type { Note, NoteReminder } from './types'
import { t } from '../../i18n'

export interface RemindPrefs extends Pick<DatePrefs, 'zone' | 'hour12'> {
  morningHour: number
}

export function useRemindPrefs(): RemindPrefs {
  const s = useSettings() as unknown as { timezone?: string; week_start?: number | string; time_format?: string; morning_hour?: number }
  return useMemo(() => ({ ...resolveDatePrefs(s), morningHour: s.morning_hour ?? 9 }), [s])
}

export interface RemindPreset {
  id: 'later' | 'tomorrow' | 'week'
  label: string
  at: Date
}

const at = (day: string, hour: number, zone: string) => {
  const d = parseISODate(day)
  return fromZoned({ ...d, hour, minute: 0 }, zone)
}

/** The reminder presets: Later today (18:00, offered before 17:00; else 20:00 before 19:00),
 *  Tomorrow (the morning hour) and Next week (Monday at the morning hour). */
export function remindPresets(now: Date, p: RemindPrefs): RemindPreset[] {
  const today = todayISO(p.zone, now)
  const hour = zoneParts(now, p.zone).hour
  const out: RemindPreset[] = []
  if (hour < 17) out.push({ id: 'later', label: t('common.laterToday'), at: at(today, 18, p.zone) })
  else if (hour < 19) out.push({ id: 'later', label: t('common.laterToday'), at: at(today, 20, p.zone) })
  out.push({ id: 'tomorrow', label: t('common.tomorrow'), at: at(addDaysISO(today, 1), p.morningHour, p.zone) })
  const wd = weekdayOf(today) // 0 = Sunday
  out.push({ id: 'week', label: t('common.nextWeek'), at: at(addDaysISO(today, ((8 - wd) % 7) || 7), p.morningHour, p.zone) })
  return out
}

/** "Today, 6:00 PM", "Tomorrow, 9:00 AM", "Mon 12 Oct, 9:00 AM". */
export function remindLabel(iso: string, now: Date, p: Pick<DatePrefs, 'zone' | 'hour12'>): string {
  const d = new Date(iso)
  const day = toISODate(d, p.zone)
  const today = todayISO(p.zone, now)
  const word = day === today ? t('common.today') : day === addDaysISO(today, 1) ? t('common.tomorrow') : day === addDaysISO(today, -1) ? t('common.yesterday') : formatDate(d, 'day', p.zone)
  return `${word}, ${formatTime(d, p)}`
}

/** The chip text of a note's reminder: the next time, "Snoozed until …", or the time that fired. */
export function reminderChip(r: NoteReminder, now: Date, p: Pick<DatePrefs, 'zone' | 'hour12'>): string {
  if (r.state === 'snoozed' && r.snoozed_until) return t('notes.remind.snoozedUntil', { remindLabel: remindLabel(r.snoozed_until, now, p) })
  return remindLabel(r.at, now, p)
}

/** The value of a datetime-local input, read in the user's zone. */
export function fromLocalInput(v: string, zone: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(v)
  if (!m) return null
  return fromZoned({ year: +m[1], month: +m[2], day: +m[3], hour: +m[4], minute: +m[5] }, zone)
}

export function toLocalInput(d: Date, zone: string): string {
  const z = zoneParts(d, zone)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${z.year}-${pad(z.month)}-${pad(z.day)}T${pad(z.hour)}:${pad(z.minute)}`
}

/** The Reminder menu of a note (the editor's toolbar, a card's ⋮ menu): presets, Pick…, Remove.
 *  ACT note.remind @web.note @web.notes */
export function RemindMenu({ note, onDone }: { note: Pick<Note, 'id' | 'reminders'>; onDone: () => void }) {
  const qc = useQueryClient()
  const p = useRemindPrefs()
  const now = new Date()
  const [pick, setPick] = useState(false)
  const presets = remindPresets(now, p)
  const [v, setV] = useState(() => toLocalInput(presets.find((x) => x.id === 'tomorrow')!.at, p.zone))
  const current = note.reminders ?? []
  const set = (d: Date) => {
    onDone()
    void setReminders(qc, note.id, [d.toISOString()], t('notes.remind.reminder', { remindLabel: remindLabel(d.toISOString(), now, p) }))
  }
  const chosen = fromLocalInput(v, p.zone)
  const past = !chosen || chosen.getTime() <= Date.now()
  if (pick) {
    return (
      <form className="nt-remind-pick" aria-label={t('notes.remind.pickADateAndTime')} onSubmit={(e) => {
        e.preventDefault()
        if (chosen && !past) set(chosen)
      }}>
        <label className="field">
          <span>{t('common.remindMeAt')}</span>
          <input type="datetime-local" value={v} data-autofocus onChange={(e) => setV(e.target.value)} />
        </label>
        {past && <p className="muted nt-small" role="alert">{t('notes.remind.pickATimeThatHas')}</p>}
        <div className="nt-remind-actions">
          <button type="button" className="btn text sm" onClick={() => setPick(false)}>{t('common.back')}</button>
          <button type="submit" className="btn filled sm" disabled={past}>{t('notes.remind.setReminder')}</button>
        </div>
      </form>
    )
  }
  return (
    <>
      <MenuHeader>{t('common.remindMe')}</MenuHeader>
      {presets.map((x) => (
        <MenuItem key={x.id} icon={<Bell size={16} />} onSelect={() => set(x.at)} shortcut={x.id === 'week' ? remindLabel(x.at.toISOString(), now, p) : formatTime(x.at, p)}>
          {x.label}
        </MenuItem>
      ))}
      <MenuItem icon={<Bell size={16} />} onSelect={() => setPick(true)}>{t('notes.remind.pickADateAndTime2')}</MenuItem>
      {current.length > 0 && (
        <>
          <MenuSep />
          <MenuItem icon={<BellOff size={16} />} onSelect={() => {
            onDone()
            void setReminders(qc, note.id, [], t('notes.remind.remindersRemoved', { count: current.length }))
          }}>{t('notes.remind.removeTheReminders', { count: current.length })}</MenuItem>
        </>
      )}
    </>
  )
}

/** The reminder chips of an open note: the time, and × to remove it (with Undo). */
export function ReminderChips({ note, readOnly }: { note: Pick<Note, 'id' | 'reminders'>; readOnly: boolean }) {
  const qc = useQueryClient()
  const p = useRemindPrefs()
  const now = new Date()
  const list = note.reminders ?? []
  if (!list.length) return null
  return (
    <div className="nt-reminders" aria-label={t('common.reminders')} data-testid="note-reminders">
      {list.map((r) => {
        const text = reminderChip(r, now, p)
        return (
          <span key={r.key} className={`nt-link-chip nt-remind-chip${r.state === 'fired' ? ' fired' : ''}`} title={r.state === 'fired' ? t('notes.remind.thisReminderHasGoneOff') : undefined}>
            <span className="nt-remind-time"><Bell size={14} aria-hidden /> {text}</span>
            {!readOnly && (
              <button type="button" aria-label={t('notes.remind.removeTheReminder', { text })} data-tip={t('notes.remind.removeReminder')} onClick={() => {
                void setReminders(qc, note.id, list.filter((x) => x.key !== r.key).map((x) => x.at), t('notes.remind.reminderRemoved'))
              }}><X size={12} /></button>
            )}
          </span>
        )
      })}
    </div>
  )
}

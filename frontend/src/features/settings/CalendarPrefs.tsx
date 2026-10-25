// Settings → You → Region (time zone, clock, first day of the week, date order, currency) and
// Settings → Calendar & tasks, the preferences: working hours and default event length, reminder defaults (GET/PUT /reminders/settings, which the
// phone plans with too), new invitations (`calendar_add_new_invites`) and the
// completed copy of a repeating task. Every control saves on change, with Undo (save.ts).
// ACT invite.auto_add @web.settings, ACT calendar.default_reminders @web.settings, ACT notification.subscribe @web.settings

import { useMemo } from 'react'
import { useSettings } from '../../api/hooks'
import type { Settings } from '../../api/types'
import { browserZone, resolveDatePrefs, WEEKDAY_LONG, WEEKDAY_SHORT } from '../../lib/dates'
import { offsetLabel } from '../../lib/quickChoices'
import { useReminderSettings } from '../reminders/api'
import { Check, Radio, Section, SetRow } from './common'
import { useSaveReminders, useSaveSettings } from './save'
import { LanguageRow } from './language'
import { isolate, t } from '../../i18n'
import { useAssistantName } from '../rail/assistant/useAssistantName'

/** The account settings these sections read; all optional (lib/dates and features/calendar/prefs.ts read them). */
interface CalSettings {
  timezone?: string
  week_start?: number | string
  time_format?: string
  calendar_working_hours?: { start: string; end: string; days: number[] }
  calendar_default_duration?: number
  calendar_add_new_invites?: 'off' | 'needs_action'
  date_order?: 'mdy' | 'dmy'
  currency?: string
  tasks?: { keep_completed_copy?: boolean } & Record<string, unknown>
}

function zones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf('timeZone')
  } catch {
    return [browserZone()]
  }
}

/** Working hours as calendar_working_hours, which the web calendar and find-a-time on the server both
 * read, with ISO weekdays (1 = Monday … 7 = Sunday). */
export function workingHoursPatch(start: string, end: string, days: number[]): Record<string, unknown> {
  const wh = { start, end, days: [...new Set(days)].sort((a, b) => a - b) }
  return { calendar_working_hours: wh }
}

function useCalSettings() {
  const s = useSettings() as unknown as CalSettings & Settings
  const saveSettings = useSaveSettings()
  const save = (patch: Record<string, unknown>) => void saveSettings(patch as Partial<Settings>)
  return { s, save }
}

const ISO_DAYS = [1, 2, 3, 4, 5, 6, 7]

/** Settings → You → Region: the one time zone, the clock, the first day of the week,
 *  how 12/10 reads, and the currency of a new table column. Automatic follows this browser. */
export function RegionSection() {
  const assistantName = useAssistantName()
  // ACT settings.region @web.settings
  const { s, save } = useCalSettings()
  const auto = useMemo(() => resolveDatePrefs(null), [])
  const all = useMemo(zones, [])
  const ws = typeof s.week_start === 'string' ? s.week_start.toLowerCase() : s.week_start
  const wsValue = ws == null || ws === '' ? 'auto' : String(typeof ws === 'number' ? ws : ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'].indexOf(ws))
  return (
    <Section title={t('settings.calendarPrefs.region')} desc={t('settings.calendarPrefs.howMailrReadsAndShows', { name: assistantName })}>
      <LanguageRow />
      <SetRow k={t('settings.calendarPrefs.timeZone')} sub={t('settings.calendarPrefs.yourHomeZoneTimesYou', { name: assistantName })}>
        <select className="select" style={{ maxWidth: 320 }} aria-label={t('common.timeZone')} value={s.timezone ?? ''} onChange={(e) => save({ timezone: e.target.value || null })}>
          <option value="">{t('settings.calendarPrefs.automatic2', { zone: auto.zone })}</option>
          {all.map((z) => <option key={z} value={z}>{z.replace(/_/g, ' ')}</option>)}
        </select>
      </SetRow>
      <SetRow k={t('settings.calendarPrefs.clock')}>
        <div className="row" role="radiogroup" aria-label={t('settings.calendarPrefs.clock2')}>
          <Radio name="tf" value="" current={s.time_format === '12h' || s.time_format === '24h' ? s.time_format : ''} onChange={() => save({ time_format: null })} label={t('settings.calendarPrefs.automatic', { value: auto.hour12 ? '3:00 PM' : '15:00' })} />
          <Radio name="tf" value="12h" current={s.time_format ?? ''} onChange={() => save({ time_format: '12h' })} label={isolate('3:00 PM')} />
          <Radio name="tf" value="24h" current={s.time_format ?? ''} onChange={() => save({ time_format: '24h' })} label="15:00" />
        </div>
      </SetRow>
      <SetRow k={t('settings.calendarPrefs.weekStartsOn')}>
        <select className="select" style={{ width: 200 }} aria-label={t('settings.calendarPrefs.weekStartsOn2')} value={wsValue} onChange={(e) => save({ week_start: e.target.value === 'auto' ? null : Number(e.target.value) })}>
          <option value="auto">{t('settings.calendarPrefs.automatic3', { weekdayLong: WEEKDAY_LONG[auto.weekStart] })}</option>
          {[1, 0, 6].map((d) => <option key={d} value={String(d)}>{WEEKDAY_LONG[d]}</option>)}
        </select>
      </SetRow>
      {/* Tables: how 12/10 reads in typed and imported dates. */}
      <SetRow k={t('settings.calendarPrefs.datesLike1210')} sub={t('settings.calendarPrefs.howTablesAndCsvImports')}>
        <div className="row" role="radiogroup" aria-label={t('settings.calendarPrefs.dateOrder')}>
          <Radio name="do" value="mdy" current={s.date_order ?? 'mdy'} onChange={() => save({ date_order: 'mdy' })} label={t('settings.calendarPrefs.monthFirstDec10')} />
          <Radio name="do" value="dmy" current={s.date_order ?? 'mdy'} onChange={() => save({ date_order: 'dmy' })} label={t('settings.calendarPrefs.dayFirst12Oct')} />
        </div>
      </SetRow>
      <SetRow k={t('settings.calendarPrefs.currency')} sub={t('settings.calendarPrefs.theCurrencyOfANew')}>
        <select className="select" style={{ width: 200 }} aria-label={t('common.currency')} value={s.currency ?? 'USD'} onChange={(e) => save({ currency: e.target.value })}>
          {['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'INR', 'PKR', 'CHF', 'CNY', 'MXN', 'BRL', 'SEK', 'NZD', 'SGD', 'ZAR'].map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </SetRow>
    </Section>
  )
}

/** Settings → Calendar & tasks: working hours and the length of a new event. */
export function WorkingHoursSection() {
  const { s, save } = useCalSettings()
  const wh = s.calendar_working_hours ?? { start: '09:00', end: '17:00', days: [1, 2, 3, 4, 5] }
  const days = wh.days.map((d) => (d === 0 ? 7 : d))
  return (
    <Section title={t('settings.calendarPrefs.workingHoursAndNewEvents')} desc={t('settings.calendarPrefs.timeZoneClockAndThe')}>
      <SetRow k={t('settings.calendarPrefs.workingHours')} sub={t('settings.calendarPrefs.shadedOutsideInTheCalendar')}>
        <div className="stack" style={{ gap: 8 }}>
          <div className="row" style={{ gap: 8 }}>
            <input type="time" className="input" style={{ width: 130 }} aria-label={t('settings.calendarPrefs.workingHoursStart')} step={900} value={wh.start}
              onChange={(e) => e.target.value && save(workingHoursPatch(e.target.value, wh.end, days))} />
            <span>{t('settings.calendarPrefs.to')}</span>
            <input type="time" className="input" style={{ width: 130 }} aria-label={t('settings.calendarPrefs.workingHoursEnd')} step={900} value={wh.end}
              onChange={(e) => e.target.value && e.target.value > wh.start && save(workingHoursPatch(wh.start, e.target.value, days))} />
          </div>
          <div className="row cp-days" role="group" aria-label={t('settings.calendarPrefs.workingDays')}>
            {ISO_DAYS.map((d) => {
              const on = days.includes(d)
              return (
                <button key={d} type="button" className={`chip${on ? ' selected' : ''}`} aria-pressed={on}
                  onClick={() => {
                    const next = on ? days.filter((x) => x !== d) : [...days, d]
                    if (next.length) save(workingHoursPatch(wh.start, wh.end, next))
                  }}>
                  {WEEKDAY_SHORT[d % 7]}
                </button>
              )
            })}
          </div>
        </div>
      </SetRow>
      <SetRow k={t('settings.calendarPrefs.newEventsLast')}>
        <select className="select" style={{ width: 160 }} aria-label={t('settings.calendarPrefs.defaultEventLength')} value={s.calendar_default_duration ?? 60} onChange={(e) => save({ calendar_default_duration: Number(e.target.value) })}>
          {[15, 30, 45, 60, 90, 120].map((m) => <option key={m} value={m}>{m < 60 ? t('settings.calendarPrefs.minutes', { m }) : m === 60 ? t('common.n1Hour') : t('settings.calendarPrefs.hours', { value: m / 60 })}</option>)}
        </select>
      </SetRow>
    </Section>
  )
}

const TIMED = [null, 0, 5, 10, 15, 30, 60, 120, 1440]

export function RemindersSection() {
  const q = useReminderSettings()
  const saveReminders = useSaveReminders()
  const save = { mutate: (p: Parameters<typeof saveReminders>[0]) => void saveReminders(p) }
  const s = q.data
  const morning = s?.morning_hour ?? 9
  const allDay: { v: number | null; label: string }[] = [
    { v: null, label: t('common.none') },
    { v: -morning * 60, label: t('settings.calendarPrefs.onTheDayAt00', { morning }) },
    { v: 1440 - morning * 60, label: t('settings.calendarPrefs.n1DayBeforeAt00', { morning }) },
    { v: 2880 - morning * 60, label: t('settings.calendarPrefs.n2DaysBeforeAt00', { morning }) },
  ]
  return (
    <Section title={t('settings.calendarPrefs.reminderDefaults')} desc={t('settings.calendarPrefs.usedWhenAnEventOr')}>
      <SetRow k={t('settings.calendarPrefs.timedEvents')}>
        <select className="select" style={{ width: 200 }} aria-label={t('settings.calendarPrefs.defaultReminderForTimedEvents')} disabled={!s}
          value={s?.default_timed_event == null ? 'none' : String(s.default_timed_event)}
          onChange={(e) => save.mutate({ default_timed_event: e.target.value === 'none' ? null : Number(e.target.value) })}>
          {TIMED.map((m) => <option key={String(m)} value={m == null ? 'none' : String(m)}>{m == null ? t('common.none') : m === 0 ? t('settings.calendarPrefs.atTheStart') : offsetLabel(m)}</option>)}
        </select>
      </SetRow>
      <SetRow k={t('settings.calendarPrefs.allDayEvents')}>
        <select className="select" style={{ width: 240 }} aria-label={t('settings.calendarPrefs.defaultReminderForAllDay')} disabled={!s}
          value={s?.default_all_day_event == null ? 'none' : String(s.default_all_day_event)}
          onChange={(e) => save.mutate({ default_all_day_event: e.target.value === 'none' ? null : Number(e.target.value) })}>
          {allDay.map((o) => <option key={String(o.v)} value={o.v == null ? 'none' : String(o.v)}>{o.label}</option>)}
          {s?.default_all_day_event != null && !allDay.some((o) => o.v === s.default_all_day_event) && (
            <option value={String(s.default_all_day_event)}>{t('settings.calendarPrefs.minutesBeforeTheDay', { defaultAllDayEvent: s.default_all_day_event })}</option>
          )}
        </select>
      </SetRow>
      <SetRow k={t('settings.calendarPrefs.tasks')}>
        <Check checked={s?.default_task ?? true} disabled={!s} onChange={(v) => save.mutate({ default_task: v })} label={t('settings.calendarPrefs.remindMeWhenATask')} desc={t('settings.calendarPrefs.aTaskWithOnlyA')} />
      </SetRow>
      <SetRow k={t('settings.calendarPrefs.morningHour')} sub={t('settings.calendarPrefs.tomorrowMorningAndDateOnly')}>
        <select className="select" style={{ width: 120 }} aria-label={t('settings.calendarPrefs.morningHour2')} disabled={!s} value={morning}
          onChange={(e) => save.mutate({ morning_hour: Number(e.target.value) })}>
          {[5, 6, 7, 8, 9, 10, 11].map((h) => <option key={h} value={h}>{h}:00</option>)}
        </select>
      </SetRow>
    </Section>
  )
}

export function InvitesAndTasksSection() {
  const { s, save } = useCalSettings()
  const mode = s.calendar_add_new_invites === 'needs_action' ? 'needs_action' : 'off'
  const keepCopy = s.tasks?.keep_completed_copy !== false
  return (
    <Section title={t('settings.calendarPrefs.invitationsAndRepeatingTasks')}>
      <SetRow k={t('settings.calendarPrefs.newInvitations')} sub={t('settings.calendarPrefs.invitationsAreNeverAcceptedFor')}>
        <div className="stack" role="radiogroup" aria-label={t('settings.calendarPrefs.newInvitations2')}>
          {/* ACT invite.auto_add */}
          <Radio name="invites" value="off" current={mode} onChange={() => save({ calendar_add_new_invites: 'off' })} label={t('settings.calendarPrefs.addToMyCalendarWhen')} />
          <Radio name="invites" value="needs_action" current={mode} onChange={() => save({ calendar_add_new_invites: 'needs_action' })}
            label={t('settings.calendarPrefs.addToMyCalendarAt')} desc={t('settings.calendarPrefs.onlyInvitationsSentByTheir')} />
        </div>
      </SetRow>
      <SetRow k={t('settings.calendarPrefs.repeatingTasks')}>
        <Check checked={keepCopy} onChange={(v) => save({ tasks: { ...(s.tasks ?? {}), keep_completed_copy: v } })}
          label={t('settings.calendarPrefs.keepACompletedCopyEach')} desc={t('settings.calendarPrefs.theCopyShowsUnderCompleted')} />
      </SetRow>
    </Section>
  )
}

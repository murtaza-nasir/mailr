// Snooze and schedule-send presets (Gmail's choices).

import { t, dateLocale } from '../i18n'
import { clockOpts } from './dates'

export interface TimeOption {
  id: string
  label: string
  at: Date
}

function at(d: Date, hour: number, minute = 0): Date {
  const x = new Date(d)
  x.setHours(hour, minute, 0, 0)
  return x
}

function addDays(d: Date, n: number): Date {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}

export function snoozeOptions(now: Date = new Date()): TimeOption[] {
  const opts: TimeOption[] = []
  const later = at(now, 18)
  if (now.getHours() < 17) opts.push({ id: 'later', label: t('common.laterToday'), at: later })
  opts.push({ id: 'tomorrow', label: t('common.tomorrow'), at: at(addDays(now, 1), 8) })
  const day = now.getDay() // 0 Sun .. 6 Sat
  if (day >= 1 && day <= 4) {
    const toSat = 6 - day
    opts.push({ id: 'weekend', label: t('common.thisWeekend'), at: at(addDays(now, toSat), 8) })
  }
  const toMon = ((8 - day) % 7) || 7
  opts.push({ id: 'nextweek', label: t('common.nextWeek'), at: at(addDays(now, toMon), 8) })
  return opts
}

export function scheduleOptions(now: Date = new Date()): TimeOption[] {
  const opts: TimeOption[] = [
    { id: 'tomorrow-am', label: t('common.tomorrowMorning'), at: at(addDays(now, 1), 8) },
    { id: 'tomorrow-pm', label: t('lib.snooze.tomorrowAfternoon'), at: at(addDays(now, 1), 13) },
  ]
  const day = now.getDay()
  const toMon = ((8 - day) % 7) || 7
  // Tomorrow already covers Monday when today is Sunday.
  if (toMon > 1) opts.push({ id: 'monday', label: t('lib.snooze.mondayMorning'), at: at(addDays(now, toMon), 8) })
  return opts
}

/** mail.follow_up presets: "remind me if no reply" in 1, 2, 3 or 7 days, at 9:00. */
export function followUpOptions(now: Date = new Date()): TimeOption[] {
  return [
    { id: '1d', label: t('common.tomorrow'), at: at(addDays(now, 1), 9) },
    { id: '2d', label: t('lib.snooze.in2Days'), at: at(addDays(now, 2), 9) },
    { id: '3d', label: t('lib.snooze.in3Days'), at: at(addDays(now, 3), 9) },
    { id: '7d', label: t('lib.snooze.inAWeek'), at: at(addDays(now, 7), 9) },
  ]
}

export function formatOptionTime(d: Date, now: Date = new Date()): string {
  const sameWeek = d.getTime() - now.getTime() < 6 * 86_400_000
  const day = sameWeek ? d.toLocaleDateString(dateLocale('en-US'), { weekday: 'short' }) : d.toLocaleDateString(dateLocale('en-US'), { month: 'short', day: 'numeric' })
  return `${day}, ${d.toLocaleTimeString(dateLocale('en-US'), { hour: 'numeric', minute: '2-digit', ...clockOpts() })}`
}

/** Value for <input type="datetime-local"> */
export function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

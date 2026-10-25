// A routine's schedule (a cron line, backend agent/orders.py) in words: "Weekdays at 8:00",
// "Mondays and Fridays at 16:00", "On the 1st of each month at 9:00". A schedule these words cannot
// say reads "Custom schedule", with the cron line kept for the tooltip.

import { appLocale, t } from '../../../i18n'
import { clockOpts } from '../../../lib/dates'

function timeOf(h: number, m: number): string {
  return new Date(2026, 0, 5, h, m).toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })
}

function dayName(d: number): string {
  // 2026-01-04 is a Sunday: cron day 0.
  return new Date(2026, 0, 4 + (d % 7)).toLocaleDateString(appLocale(), { weekday: 'long' })
}

function days(field: string): number[] | null {
  const out = new Set<number>()
  for (const part of field.split(',')) {
    const range = /^(\d)-(\d)$/.exec(part)
    if (range) {
      for (let d = +range[1]; d <= +range[2]; d++) out.add(d % 7)
    } else if (/^\d$/.test(part)) out.add(+part % 7)
    else return null
  }
  return [...out].sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7))
}

export function scheduleWords(cron: string): string {
  const f = cron.trim().split(/\s+/)
  if (f.length !== 5 || !/^\d+$/.test(f[0]) || !/^\d+$/.test(f[1]) || f[3] !== '*') return t('settings.assistant.scheduleWords.custom')
  const at = timeOf(+f[1], +f[0])
  const [, , dom, , dow] = f
  if (dom !== '*') {
    if (dow !== '*' || !/^\d+$/.test(dom)) return t('settings.assistant.scheduleWords.custom')
    return t('settings.assistant.scheduleWords.monthly', { day: +dom, time: at })
  }
  if (dow === '*') return t('settings.assistant.scheduleWords.daily', { time: at })
  const ds = days(dow)
  if (!ds) return t('settings.assistant.scheduleWords.custom')
  if (ds.join() === '1,2,3,4,5') return t('settings.assistant.scheduleWords.weekdays', { time: at })
  if (ds.join() === '6,0') return t('settings.assistant.scheduleWords.weekends', { time: at })
  const names = new Intl.ListFormat(appLocale(), { type: 'conjunction' }).format(ds.map(dayName))
  return t('settings.assistant.scheduleWords.onDays', { days: names, time: at })
}

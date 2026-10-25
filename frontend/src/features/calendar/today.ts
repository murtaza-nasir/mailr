// The words and the day line of the Today view (TodayView.tsx), kept apart so they can be tested.

/** The greeting for the hour of the day (0–23). */
import { t } from '../../i18n'

export function greeting(hour: number): string {
  if (hour < 5) return t('calendar.today.upLate')
  if (hour < 12) return t('calendar.today.goodMorning')
  if (hour < 18) return t('calendar.today.goodAfternoon')
  return t('calendar.today.goodEvening')
}

/** Which part of the day the small picture shows. */
export type DayPart = 'night' | 'morning' | 'afternoon' | 'evening'
export function dayPart(hour: number): DayPart {
  if (hour < 5 || hour >= 22) return 'night'
  if (hour < 12) return 'morning'
  if (hour < 18) return 'afternoon'
  return 'evening'
}

/** One plain sentence of what the day holds: "3 events, 2 tasks due and 1 reply to wait for." */
export function summary(c: { events: number; tasks: number; followups: number; approvals: number; reminders: number }, isToday: boolean): string {
  const parts: string[] = []
  if (c.events) parts.push(t('calendar.today.events', { count: c.events }))
  if (c.tasks) parts.push(t('calendar.today.tasksDue', { count: c.tasks }))
  if (c.followups) parts.push(t('calendar.today.repliesToWaitFor', { count: c.followups }))
  if (c.approvals) parts.push(t('calendar.today.requestsToDecide', { count: c.approvals }))
  if (c.reminders) parts.push(t('calendar.today.missedReminders', { count: c.reminders }))
  if (!parts.length) return isToday ? t('calendar.today.nothingIsPlannedAndNothing') : t('calendar.today.nothingIsPlannedForThis')
  const last = parts.pop()!
  return t('calendar.today.sentence', { text: parts.length ? t('calendar.today.and', { parts: parts.join(', '), last }) : last })
}

/** The hours the day line spans: the working hours, widened to 8:00–20:00 and to the events. */
export function lineSpan(work: { start: number; end: number }, events: { start: number; end: number }[]): { start: number; end: number } {
  let start = Math.min(work.start, 8 * 60)
  let end = Math.max(work.end, 20 * 60)
  for (const e of events) {
    start = Math.min(start, Math.floor(e.start / 60) * 60)
    end = Math.max(end, Math.ceil(e.end / 60) * 60)
  }
  return { start: Math.max(0, start), end: Math.min(1440, end) }
}

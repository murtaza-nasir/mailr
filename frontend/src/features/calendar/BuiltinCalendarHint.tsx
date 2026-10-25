// The empty Calendar and Tasks apps, and the setup guide's calendar step, with no calendar account:
// built-in calendars (Settings → Admin → Calendars, calendar/builtin.py) are off by default. An admin
// is offered the switch while they are off; a member is told to ask for them. A member who has none
// yet is in that state only while they are off: with them on, each user has one.

import { CalendarOff } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { useApi, useMe } from '../../api/hooks'
import { t } from '../../i18n'

export const ADMIN_CALENDARS_PATH = '/settings/admin/calendars'

/** `members`: also show the line for a member (the apps); the setup guide shows it to admins only. */
export function BuiltinCalendarHint({ members = true }: { members?: boolean }) {
  const admin = useMe().data?.user.role === 'admin'
  const q = useApi<{ builtin: boolean }>(['admin', 'calendars'], '/admin/calendars', undefined, { enabled: admin })
  if (admin) {
    if (!q.data || q.data.builtin) return null
    return (
      <div className="builtin-cal-hint" data-testid="builtin-cal-hint">
        <p>{t('calendar.builtinCalendarHint.builtInCalendarsAreOff')}</p>
        <Link className="btn outlined" to={ADMIN_CALENDARS_PATH}>{t('calendar.builtinCalendarHint.turnOnBuiltInCalendars')}</Link>
      </div>
    )
  }
  if (!members) return null
  return (
    <p className="builtin-cal-hint" data-testid="builtin-cal-hint">{t('calendar.builtinCalendarHint.askForBuiltInCalendars')}</p>
  )
}

/** The Calendar and Tasks apps with no calendar account: one state for both, with the way to connect
 *  one (Settings → Calendar & tasks opens its connect form with ?add=1). `extra`: the Calendar's
 *  "Subscribe to a calendar link…". */
export function NoCalendarConnected({ app, extra }: { app: 'calendar' | 'tasks'; extra?: ReactNode }) {
  const Heading = app === 'calendar' ? 'h1' : 'h2'
  return (
    <>
      <CalendarOff size={app === 'calendar' ? 48 : 44} aria-hidden />
      <Heading>{t('calendar.builtinCalendarHint.noCalendarConnectedYet')}</Heading>
      <p>{app === 'calendar' ? t('calendar.builtinCalendarHint.connectTheCalendarYouUse') : t('calendar.builtinCalendarHint.tasksAreKeptWithYour')}</p>
      <Link className="btn filled" to="/settings/calendar?add=1">{t('calendar.builtinCalendarHint.connectACalendar')}</Link>
      {extra}
      <BuiltinCalendarHint />
    </>
  )
}

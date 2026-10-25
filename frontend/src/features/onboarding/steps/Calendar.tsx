// Step 6 (optional): calendars. The built-in mailr calendar, when the admin turned it on, shows as
// already connected. On offer as well: the calendar server the admin prepared (her own account on it,
// which also brings in anything shared with her there), and any number of CalDAV calendars she
// already uses (the same connect as Settings → Calendar & tasks). Google and Microsoft calendars
// cannot be connected; for her mail there, the step offers to subscribe to the calendar's link.

import { CalendarDays, CheckCircle2, Link2, Plus, Server } from 'lucide-react'
import { useState } from 'react'
import { useApiList } from '../../../api/hooks'
import type { Connector } from '../../../api/types'
import { BuiltinCalendarHint } from '../../calendar/BuiltinCalendarHint'
import { CaldavDialog } from '../../settings/CalendarTab'
import { ChoiceCard, StepFrame } from '../guide'
import { useGuide } from '../guideContext'
import { calendarStepState, closedCalendarProvider, hostOf } from '../calendarStep'
import { SubscribeButton } from '../../calendar/Subscriptions'
import { t } from '../../../i18n'

export function CalendarStep() {
  const g = useGuide()
  const conns = useApiList<Connector>(['connectors'], '/connectors')
  // The built-in calendar is connected already; the choices stay until she connects one of her own.
  const { connected: cals, external, server } = calendarStepState(conns.data ?? [], g.state.offer.calendar_url)
  const [adding, setAdding] = useState(false)
  const [choice, setChoice] = useState<'server' | 'other'>('server')
  const [open, setOpen] = useState(false)
  const picked = server && choice === 'server' ? 'server' : 'other'
  const choosing = external.length === 0 || adding
  const closed = closedCalendarProvider(conns.data ?? [])
  return (
    <StepFrame
      icon={<CalendarDays />}
      title={t('onboarding.steps.calendar.yourCalendars')}
      lead={t('onboarding.steps.calendar.withYourCalendarsNextTo')}
      skip={cals.length === 0 || (choosing && external.length === 0)}
      skipLabel={cals.length > 0 ? t('onboarding.steps.calendar.continueWithoutAnother') : undefined}
      skipMark={cals.length > 0 ? 'done' : 'skipped'}
      primary={choosing ? { label: t('common.connect'), onClick: () => setOpen(true) } : { label: t('common.continue') }}
    >
      {cals.length > 0 && (
        <ul className="ob-accounts" aria-label={t('onboarding.steps.calendar.connectedCalendars')}>
          {cals.map((c) => (
            <li key={c.id} className="ob-account">
              <span className="ob-account-icon" aria-hidden>
                <CheckCircle2 />
              </span>
              <span className="ob-account-text">
                {c.builtin ? (
                  <>
                    <b>{t('onboarding.steps.calendar.mailrCalendar')}</b>
                    <span className="ob-hint">{t('onboarding.steps.calendar.connectedYourCalendarAndTask')}</span>
                  </>
                ) : (
                  <>
                    <b>{c.name}</b>
                    <span className="ob-hint">{t('onboarding.steps.calendar.connectedItsCalendarsIncludingAny')}</span>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {choosing && server ? (
        <div className="ob-choices" role="radiogroup" aria-label={t('common.calendar')}>
          <ChoiceCard
            on={picked === 'server'}
            onSelect={() => setChoice('server')}
            title={t('onboarding.steps.calendar.useOurCalendarServer')}
            desc={t('onboarding.steps.calendar.yourAdministratorRunsACalendar', { hostOf: hostOf(server) })}
            name={t('onboarding.steps.calendar.useOurCalendarServer')}
          >
            <Server />
          </ChoiceCard>
          <ChoiceCard on={picked === 'other'} onSelect={() => setChoice('other')} title={t('onboarding.steps.calendar.connectACalendarIAlready')} desc={t('onboarding.steps.calendar.anyCaldavCalendarSuchAs')} name={t('onboarding.steps.calendar.connectACalendarIAlready')}>
            <Link2 />
          </ChoiceCard>
        </div>
      ) : choosing ? (
        // One way in: no radio of one choice, only what Connect does.
        <p className="ob-hint" data-testid="ob-cal-other">{t('onboarding.steps.calendar.anyCaldavCalendarSuchAs')}</p>
      ) : (
        <button type="button" className="btn outlined ob-big" onClick={() => setAdding(true)}>
          <Plus />{' '}{t('onboarding.steps.calendar.addAnotherCalendar')}
        </button>
      )}
      {/* Her mail is at Google or Microsoft, whose calendars cannot be connected here: their
          calendar's secret address can be subscribed to instead (read-only). */}
      {choosing && closed && (
        <div className="ob-note ob-note-stack" data-testid="ob-cal-closed">
          <p>{closed === 'google' ? t('onboarding.steps.calendar.googleCalendarCantBeConnected') : t('onboarding.steps.calendar.outlookCalendarCantBeConnected')}</p>
          <SubscribeButton />
        </div>
      )}
      {/* No calendar yet and built-in calendars off: an admin can turn them on (Settings → Admin → Calendars). */}
      {cals.length === 0 && <BuiltinCalendarHint members={false} />}
      {open && (
        <CaldavDialog
          onClose={() => setOpen(false)}
          onConnected={() => setAdding(false)}
          initial={picked === 'server' && server ? { name: hostOf(server) || t('common.calendar'), url: server } : undefined}
        />
      )}
    </StepFrame>
  )
}

import { assistantNameNow } from '../rail/assistant/useAssistantName'
// Settings → Notifications. "Notification types" is one choice per kind for
// every device: web push, the open windows and the Android app (PATCH /me/settings, backend
// core/notifyprefs.py). "On this computer" holds what only this browser decides: the
// browser's permission, Web Push to it (asked only from these switches, which are user gestures)
// and its one override, no mail here. Then the reminder switches (PUT /reminders/settings), sound
// and a test notification.
// ACT notification.subscribe @web.settings

import { BellRing, CheckCircle2, AlertTriangle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Switch } from '../../components/bits'
import type { ComponentProps } from 'react'
import { useSettings } from '../../api/hooks'
import type { MailNotify } from '../../api/types'
import { pushEventsFor } from '../onboarding/steps'
import { toast, toastError } from '../../stores/ui'
import { useReminderSettings, useSaveReminderSettings } from '../reminders/api'
import { REPEAT_CHOICES, repeatMinutes, repeatSummary } from '../reminders/logic'
import {
  notificationsSupported, permission, pushEvents, pushSupported, setPushEvents, showTest,
} from '../reminders/notify'
import { Section, SetRow } from './common'
import { useSaveReminders, useSaveSettings } from './save'
import { installPlatform, isStandalone, platformInput, useInstall } from '../../lib/pwa'
import { t, tx } from '../../i18n'

const SOUND = 'mailr.reminders.sound'
const MISSED = 'mailr.reminders.missedNotice'

function readFlag(k: string, dflt: boolean): boolean {
  try {
    const v = localStorage.getItem(k)
    return v == null ? dflt : v === '1'
  } catch {
    return dflt
  }
}

function writeFlag(k: string, v: boolean) {
  try {
    localStorage.setItem(k, v ? '1' : '0')
  } catch {
    /* private mode */
  }
}

/** A switch with its words beside it (a click on the words flips it too). */
function Toggle(p: ComponentProps<typeof Switch>) {
  return (
    <label className="row nt-toggle" style={{ gap: 10, cursor: p.disabled ? 'default' : 'pointer', opacity: p.disabled ? 0.6 : 1 }}>
      <Switch {...p} />
      <span>{p.label}</span>
    </label>
  )
}

/** The account's mail levels, in the order of how much they let through. */
export const MAIL_LEVELS: { id: MailNotify; label: string }[] = [
  { id: 'off', label: t('common.off') },
  { id: 'vip', label: t('settings.notificationsTab.onlyMailFromVipSenders') },
  { id: 'important', label: t('common.importantMail') },
  { id: 'primary', label: t('settings.notificationsTab.importantMailAndThePrimary') },
  { id: 'all', label: t('settings.notificationsTab.allNewMail') },
]

export function NotificationsTab() {
  const q = useReminderSettings()
  // Every choice saves at once with Undo (save.ts). The browser's own permission and push
  // subscription are not settings of the account: they change with their own message.
  const saveR = useSaveReminders()
  const save = { mutate: (p: Parameters<typeof saveR>[0]) => void saveR(p) }
  const saveDevice = useSaveReminderSettings() // this browser's switches: no Undo (they follow the subscription)
  const settings = useSettings()
  const saveS = useSaveSettings()
  const upd = { mutate: (p: Parameters<typeof saveS>[0]) => void saveS(p) }
  const [perm, setPerm] = useState(permission())
  // What this browser's push subscription carries (null: none yet). Every kind shares one
  // subscription; the account settings decide what is sent.
  const [events, setEvents] = useState<string[] | null | undefined>(undefined)
  const pushOn = events === undefined ? null : !!events && events.length > 0
  const [busy, setBusy] = useState(false)
  const [sound, setSound] = useState(() => readFlag(SOUND, false))
  const [missed, setMissed] = useState(() => readFlag(MISSED, true))
  useEffect(() => {
    void pushEvents().then(setEvents)
  }, [])
  // iPhone and iPad: Web Push only reaches mailr added to the Home Screen (iOS 16.4 and later).
  const iosBrowser = installPlatform(platformInput()) === 'ios' && !isStandalone()

  /** Change the subscription's events; the browser asks for permission on the first one. */
  const changeEvents = async (next: string[], done?: string): Promise<boolean> => {
    setBusy(true)
    try {
      const r = await setPushEvents(next)
      setPerm(permission())
      if (!r.ok) {
        toast({ message: r.reason ?? t('settings.notificationsTab.pushCouldNotBeTurned'), error: true })
        return false
      }
      setEvents(next.length ? next : null)
      if (done) toast({ message: done })
      return true
    } catch (e) {
      toastError(e, t('settings.notificationsTab.pushNotChanged'))
      return false
    } finally {
      setBusy(false)
    }
  }
  // "Mail on this browser: Off" is the one thing a browser decides for itself.
  const mailHere = !events || events.includes('new_mail') || events.includes('important_mail')
  const s = q.data

  const ask = async (): Promise<boolean> => {
    if (!notificationsSupported()) return false
    const p = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission
    setPerm(p)
    if (p !== 'granted') toast({ message: t('settings.notificationsTab.notificationsAreBlockedForMailr'), error: true })
    return p === 'granted'
  }

  const setDesktop = async (on: boolean) => {
    if (on && !(await ask())) return
    saveDevice.mutate({ web: { desktop: on } })
  }

  const setPush = async (on: boolean) => {
    if (await changeEvents(on ? pushEventsFor(mailHere) : [], on ? t('settings.notificationsTab.notificationsReachThisBrowserEven') : undefined)) saveDevice.mutate({ web: { push: on } })
  }

  const test = async () => {
    if (!(await ask())) return
    try {
      await showTest()
    } catch {
      toast({ message: t('settings.notificationsTab.theBrowserCouldNotShow') })
    }
  }
  const pushBlocked = busy || pushOn === null || !pushSupported() || perm === 'denied'

  return (
    <>
      {/* ACT notification.choose @web.settings: the account's per-kind choices */}
      <Section
        title={t('settings.notificationsTab.whatNotifiesYou')}
        id="what"
        desc={t('settings.notificationsTab.oneChoiceForEveryDevice')}
      >
        <SetRow k={t('settings.notificationsTab.newMail')} sub={<>{tx('settings.notificationsTab.vipSendersAreSetUnder', undefined, { link: (c) => <Link to="/settings/mail/inbox#vip">{c}</Link> })}</>}>
          <select
            className="select" style={{ maxWidth: 300 }} aria-label={t('settings.notificationsTab.newMailNotifications')} data-testid="notify-mail"
            value={settings.mail_notifications} onChange={(e) => upd.mutate({ mail_notifications: e.target.value as MailNotify })}
          >
            {MAIL_LEVELS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
          </select>
        </SetRow>
        <SetRow k={t('settings.notificationsTab.approvals')} sub={t('settings.notificationsTab.anAgentOrWaitsFor', { name: assistantNameNow() })}>
          <Toggle label={t('settings.notificationsTab.approvalsAndFailedSends')} checked={settings.notify_approvals !== false} onChange={(v) => upd.mutate({ notify_approvals: v })} />
        </SetRow>
        <SetRow k={`${assistantNameNow()}:`} sub={t('settings.notificationsTab.messagesAndResultsFromAnd', { name: assistantNameNow() })}>
          <Toggle label={t('settings.notificationsTab.messagesFrom', { name: assistantNameNow() })} checked={settings.notify_assistant !== false} onChange={(v) => upd.mutate({ notify_assistant: v })} />
        </SetRow>
        <SetRow k={t('settings.notificationsTab.accounts')} sub={t('settings.notificationsTab.onceWhenAMailAccount')}>
          <Toggle label={t('settings.notificationsTab.anAccountNeedsItsPassword')} checked={settings.notify_accounts !== false} onChange={(v) => upd.mutate({ notify_accounts: v })} />
        </SetRow>
      </Section>

      <Section title={t('settings.notificationsTab.onThisComputer')} id="push-mail" desc={t('settings.notificationsTab.eachBrowserAndEachInstalled')}>
        <div className={`banner ${perm === 'denied' ? 'error' : ''}`} style={{ marginBottom: 12 }} data-testid="notif-permission">
          {perm === 'granted' ? <CheckCircle2 /> : perm === 'denied' ? <AlertTriangle /> : <BellRing />}
          <span>
            {perm === 'granted'
              ? t('settings.notificationsTab.thisBrowserMayShowNotifications')
              : perm === 'denied'
                ? t('settings.notificationsTab.notificationsAreBlockedForMailr2')
                : notificationsSupported()
                  ? t('settings.notificationsTab.thisBrowserHasNotBeen')
                  : t('settings.notificationsTab.thisBrowserCannotShowNotifications')}
          </span>
          <span className="spacer" />
          <button type="button" className="btn outlined sm" disabled={perm === 'denied' || !notificationsSupported()} onClick={() => void test()}>
            {t('settings.notificationsTab.sendATestNotification')}
          </button>
        </div>
        {iosBrowser && (
          <div className="banner" style={{ marginBottom: 12 }} data-testid="push-ios-install">
            <BellRing />
            <span style={{ flex: 1 }}>{t('settings.notificationsTab.onIphoneAndIpadNotifications')}</span>
            <button type="button" className="btn tonal sm" onClick={() => useInstall.getState().setGuide(true)}>
              {t('settings.notificationsTab.how')}
            </button>
          </div>
        )}
        <SetRow k={t('settings.notificationsTab.whileMailrIsOpen')} sub={t('settings.notificationsTab.inABackgroundTabWhat')}>
          <Toggle label={t('settings.notificationsTab.desktopNotifications')} checked={!!s?.web?.desktop && perm === 'granted'} disabled={!s || perm === 'denied'} onChange={(v) => void setDesktop(v)} />
        </SetRow>
        <SetRow k={t('settings.notificationsTab.whenMailrIsClosed')} sub={pushSupported() ? t('settings.notificationsTab.webPushToThisBrowser') : t('settings.notificationsTab.thisBrowserCannotReceivePush')}>
          <Toggle label={t('settings.notificationsTab.pushToThisBrowser')} checked={!!pushOn} disabled={pushBlocked} onChange={(v) => void setPush(v)} />
        </SetRow>
        {pushOn && (
          <SetRow k={t('settings.notificationsTab.mailOnThisBrowser')} sub={t('settings.notificationsTab.onlyThisBrowserTheOther')}>
            <select
              className="select" style={{ maxWidth: 240 }} aria-label={t('settings.notificationsTab.mailOnThisBrowser2')} data-testid="push-mail"
              value={mailHere ? 'follow' : 'off'} disabled={pushBlocked}
              onChange={(e) => void changeEvents(pushEventsFor(e.target.value === 'follow'))}
            >
              <option value="follow">{t('settings.notificationsTab.asChosenAbove')}</option>
              <option value="off">{t('common.off')}</option>
            </select>
          </SetRow>
        )}
      </Section>

      <Section title={t('settings.notificationsTab.whichReminders')} desc={t('settings.notificationsTab.theseSwitchesApplyToEvery')}>
        {/* ACT notification.subscribe (per-kind switches) */}
        <SetRow k={t('settings.notificationsTab.events')}>
          <Toggle label={t('settings.notificationsTab.eventReminders')} checked={s?.events ?? true} disabled={!s} onChange={(v) => save.mutate({ events: v })} />
        </SetRow>
        <SetRow k={t('settings.notificationsTab.tasks')}>
          <Toggle label={t('settings.notificationsTab.taskReminders')} checked={s?.tasks ?? true} disabled={!s} onChange={(v) => save.mutate({ tasks: v })} />
        </SetRow>
        <SetRow k={t('settings.notificationsTab.missed')} sub={t('settings.notificationsTab.remindersThatFiredWhileThis')}>
          <Toggle label={t('settings.notificationsTab.missedRemindersNotice')} checked={s?.missed ?? missed} onChange={(v) => { setMissed(v); writeFlag(MISSED, v); if (s && s.missed !== undefined) save.mutate({ missed: v }) }} />
        </SetRow>
        {/* ACT reminder.repeat @web.settings: the server alerts this browser again; the phone repeats on its own */}
        <SetRow k={t('settings.notificationsTab.remindAgain')} sub={t('settings.notificationsTab.eventsStop15MinutesAfter', { repeatSummary: repeatSummary(s?.repeat) })}>
          <select
            className="select" style={{ maxWidth: 240 }} aria-label={t('settings.notificationsTab.remindAgain2')} data-testid="remind-again"
            value={repeatMinutes(s?.repeat)} disabled={!s}
            onChange={(e) => {
              const m = Number(e.target.value)
              save.mutate({ repeat: m ? { enabled: true, every_min: m } : { enabled: false } })
            }}
          >
            {REPEAT_CHOICES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </SetRow>
        <SetRow k={t('settings.notificationsTab.sound')} sub={t('settings.notificationsTab.aShortChimeWithEach')}>
          <Toggle label={t('settings.notificationsTab.playASound')} checked={s?.sound ?? sound} onChange={(v) => { setSound(v); writeFlag(SOUND, v); if (s && s.sound !== undefined) save.mutate({ sound: v }) }} />
        </SetRow>
      </Section>
    </>
  )
}

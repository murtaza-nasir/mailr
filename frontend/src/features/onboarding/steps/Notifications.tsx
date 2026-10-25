// Step 7: notifications. The browser asks only after her click; a test notification shows what
// one looks like; she chooses what notifies her (important mail, VIPs, everything, nothing).
// ACT notification.subscribe @web.setup

import { AlertTriangle, BellRing, CheckCircle2, Send } from 'lucide-react'
import { useState } from 'react'
import { useSettings, useUpdateSettings } from '../../../api/hooks'
import { toast } from '../../../stores/ui'
import { isIOS, isStandalone, platformInput } from '../../../lib/pwa'
import { InstallGuide as PwaInstallGuide } from '../../pwa/InstallGuide'
import { notificationsSupported, permission, pushSupported, setPushEvents, showTest } from '../../reminders/notify'
import { ChoiceCard, StepFrame } from '../guide'
import { NOTIFY_CHOICES, pushEventsFor, type NotifyChoice } from '../steps'
import { t } from '../../../i18n'

export function NotificationsStep() {
  const s = useSettings()
  const upd = useUpdateSettings()
  const [perm, setPerm] = useState(permission())
  const [busy, setBusy] = useState(false)
  const [tested, setTested] = useState(false)
  const supported = notificationsSupported()
  // The account's mail level (backend core/notifyprefs.py): the same on every device. The guide
  // offers four of its five levels; "primary" shows as Important mail here.
  const choice: NotifyChoice = s.mail_notifications === 'primary' ? 'important' : s.mail_notifications

  const turnOn = async () => {
    setBusy(true)
    try {
      const p = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission
      setPerm(p)
      if (p !== 'granted') return
      if (s.mail_notifications === 'off') upd.mutate({ mail_notifications: 'important' })
      // Mail, reminders and approvals also reach this device while mailr is closed, where it can
      // (one Web Push subscription, shared with Settings → Notifications).
      if (pushSupported()) await setPushEvents(pushEventsFor()).catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

  const choose = (c: NotifyChoice) => {
    // One setting for every device; this browser's subscription already follows it.
    upd.mutate({ mail_notifications: c })
  }
  // iPhone and iPad: notifications reach mailr only from the Home Screen.
  const iosBrowser = isIOS(platformInput()) && !isStandalone()

  const test = async () => {
    try {
      await showTest()
      setTested(true)
    } catch {
      toast({ message: t('onboarding.steps.notifications.yourBrowserCouldNotShow') })
    }
  }

  return (
    <StepFrame icon={<BellRing />} title={t('common.notifications')} lead={t('onboarding.steps.notifications.getANotificationWhenSomething')} skip skipLabel={t('common.notNow')}>
      <div className={`ob-notify${perm === 'granted' ? ' on' : ''}`} data-testid="ob-permission">
        {perm === 'granted' ? (
          <>
            <p className="ob-ok">
              <CheckCircle2 />{' '}{t('onboarding.steps.notifications.notificationsAreOnForThis')}
            </p>
            <button type="button" className="btn tonal ob-big" onClick={() => void test()}>
              <Send />{' '}{t('onboarding.steps.notifications.sendMeATestNotification')}
            </button>
            {tested && <span className="ob-hint">{t('onboarding.steps.notifications.didYouSeeItIt')}</span>}
          </>
        ) : perm === 'denied' ? (
          <p className="banner error">
            <AlertTriangle />
            <span>{t('onboarding.steps.notifications.notificationsAreBlockedForMailr')}</span>
          </p>
        ) : iosBrowser ? (
          <div className="ob-ios-note" data-testid="ob-ios-install">
            <p className="ob-hint">{t('onboarding.steps.notifications.onIphoneAndIpadNotifications')}</p>
            <PwaInstallGuide platform="ios" />
          </div>
        ) : supported ? (
          <>
            <button type="button" className="btn filled ob-big" disabled={busy} onClick={() => void turnOn()}>
              <BellRing />{' '}{t('onboarding.steps.notifications.turnOnNotifications')}
            </button>
            <span className="ob-hint">{t('onboarding.steps.notifications.yourBrowserAsksYouOnce')}</span>
          </>
        ) : (
          <p className="ob-hint">{t('onboarding.steps.notifications.thisBrowserCannotShowNotifications')}</p>
        )}
      </div>
      <fieldset className="ob-fieldset">
        <legend>{t('onboarding.steps.notifications.tellMeAbout')}</legend>
        <div className="ob-choices" role="radiogroup" aria-label={t('onboarding.steps.notifications.tellMeAbout')}>
          {NOTIFY_CHOICES.map((c) => (
            <ChoiceCard key={c.id} on={choice === c.id} onSelect={() => choose(c.id)} title={c.title} desc={c.desc} name={c.title} />
          ))}
        </div>
      </fieldset>
    </StepFrame>
  )
}

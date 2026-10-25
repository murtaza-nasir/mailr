// Step 5: mailr on the phone. The phone is detected when this page runs on one; otherwise she
// says which she has. Android: a QR code to the Get the app page (download, then open the app
// with the server filled in; the Notes app is offered there too). iPhone: the web app on the Home
// Screen, with notifications; notes are in it.

import { Smartphone } from 'lucide-react'
import { useState } from 'react'
import { StepFrame } from '../guide'
import { useGuide } from '../guideContext'
import { InstallGuide } from '../InstallGuide'
import { QrCode } from '../QrCode'
import { detectPhone, getAppLink, type Phone } from '../steps'
import { t } from '../../../i18n'

export function PhoneStep() {
  const g = useGuide()
  const here = typeof navigator !== 'undefined' ? detectPhone(navigator.userAgent, navigator.maxTouchPoints ?? 0) : null
  const [phone, setPhone] = useState<Phone | null>(here)
  const server = g.state.phone.server || window.location.origin
  const qr = phone === 'ios' ? server : getAppLink(server)
  return (
    <StepFrame
      icon={<Smartphone />}
      title={t('onboarding.steps.phone.mailOnYourPhone')}
      lead={here ? t('onboarding.steps.phone.youAreOnYourPhone') : t('onboarding.steps.phone.getMailrOnYourPhone')}
      skip
      skipLabel={t('common.notNow')}
      wide
    >
      {!here && (
        <div className="ob-segmented ob-phones" role="radiogroup" aria-label={t('onboarding.steps.phone.yourPhone')}>
          <button type="button" role="radio" aria-checked={phone === 'ios'} className={phone === 'ios' ? 'on' : ''} onClick={() => setPhone('ios')}>
            {t('onboarding.steps.phone.iphone')}
          </button>
          <button type="button" role="radio" aria-checked={phone === 'android'} className={phone === 'android' ? 'on' : ''} onClick={() => setPhone('android')}>
            {t('common.android')}
          </button>
        </div>
      )}
      {phone === null && <p className="ob-hint ob-center">{t('onboarding.steps.phone.whichPhoneDoYouHave')}</p>}
      {phone && (
        <div className={`ob-phone${here ? ' here' : ''}`}>
          {!here && (
            <figure className="ob-qr-box">
              <QrCode value={qr} label={phone === 'ios' ? t('onboarding.steps.phone.qrCodeToOpenMailr') : t('onboarding.steps.phone.qrCodeToGetThe')} />
              <figcaption>{t('onboarding.steps.phone.scanWithYourPhonesCamera')}</figcaption>
            </figure>
          )}
          <InstallGuide platform={phone} server={server} apkUrl={g.state.phone.apk_url} apps={g.state.phone.apps} onPhone={!!here} />
        </div>
      )}
    </StepFrame>
  )
}

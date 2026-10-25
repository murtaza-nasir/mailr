// Settings rows that belong to this device, not the account: Install app (the install
// guide, web.install) and the swipe actions on mail rows (kept per device, like Android's).
import { useState } from 'react'
import { SetRow } from '../settings/common'
import { readSwipes, SWIPE_CHOICES, writeSwipes, type SwipeChoice, type SwipePrefs } from '../../lib/swipe'
import { InstallGuide, useStandalone } from './InstallGuide'
import { t } from '../../i18n'
import { isRtl } from '../../lib/rtl'

export function InstallAppSetting() {
  const standalone = useStandalone()
  return (
    <div id="install-app">
      <SetRow
        k={t('pwa.deviceSettings.installApp')}
        sub={standalone ? t('pwa.deviceSettings.mailrRunsAsAnApp') : t('pwa.deviceSettings.putMailrOnYourHome')}
      >
        <InstallGuide />
      </SetRow>
    </div>
  )
}

export function SwipeSetting() {
  const [p, setP] = useState<SwipePrefs>(() => readSwipes())
  const set = (side: keyof SwipePrefs, v: SwipeChoice) => {
    const next = { ...p, [side]: v }
    setP(next)
    writeSwipes(next)
  }
  // The settings are start to end and end to start (lib/swipe.ts): a right-to-left page names them
  // by the way the finger moves there, so the first is a swipe to the left.
  const rtl = isRtl()
  const toEnd = t(rtl ? 'pwa.deviceSettings.swipeLeft' : 'pwa.deviceSettings.swipeRight')
  const toStart = t(rtl ? 'pwa.deviceSettings.swipeRight' : 'pwa.deviceSettings.swipeLeft')
  return (
    <SetRow k={t('pwa.deviceSettings.swipeActions')} sub={t('pwa.deviceSettings.onTouchScreensWhatA')}>
      <div className="row" style={{ flexWrap: 'wrap', gap: 16 }}>
        <label className="row" style={{ gap: 8 }}>
          {toEnd}
          <select className="select" value={p.right} onChange={(e) => set('right', e.target.value as SwipeChoice)} aria-label={toEnd}>
            {SWIPE_CHOICES.map((c) => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </select>
        </label>
        <label className="row" style={{ gap: 8 }}>
          {toStart}
          <select className="select" value={p.left} onChange={(e) => set('left', e.target.value as SwipeChoice)} aria-label={toStart}>
            {SWIPE_CHOICES.map((c) => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </select>
        </label>
      </div>
    </SetRow>
  )
}

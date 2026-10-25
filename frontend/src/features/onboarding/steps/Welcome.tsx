// Step 1: hello, with the name and picture the sign-in provider gave; both can change here.

import { useQueryClient } from '@tanstack/react-query'
import { Camera, RotateCcw } from 'lucide-react'
import { useRef, useState } from 'react'
import { api } from '../../../api/client'
import { qk, useMe } from '../../../api/hooks'
import { Spinner } from '../../../components/bits'
import { ownPictureUrl, resetMissing } from '../../../lib/avatars'
import { toastError } from '../../../stores/ui'
import { StepFrame } from '../guide'
import { useGuide } from '../guideContext'
import { t } from '../../../i18n'

export function firstName(name: string | undefined | null): string {
  return (name ?? '').trim().split(/\s+/)[0] ?? ''
}

/** The name the field starts with: the account's name, or empty when the account has no name of
 * its own (the server then holds the address's local part, such as "admin"). */
export function startingName(user: { name?: string | null; email: string }): string {
  const name = (user.name ?? '').trim()
  const local = user.email.split('@')[0] ?? ''
  return name.toLowerCase() === local.toLowerCase() ? '' : name
}

export function WelcomeStep() {
  const g = useGuide()
  const me = useMe().data!
  const qc = useQueryClient()
  const [name, setName] = useState(() => startingName(me.user))
  const [busy, setBusy] = useState(false)
  const file = useRef<HTMLInputElement>(null)
  const pic = ownPictureUrl(me)
  const fromProvider = me.picture?.source === 'idp'

  const picture = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      resetMissing()
      await qc.invalidateQueries({ queryKey: qk.me })
      void qc.invalidateQueries({ queryKey: ['pictures', 'local'] })
    } catch (e) {
      toastError(e, t('onboarding.steps.welcome.thePictureWasNotChanged'))
    } finally {
      setBusy(false)
    }
  }

  const go = async () => {
    const n = name.trim()
    if (n && n !== me.user.name) {
      setBusy(true)
      try {
        await api.patch('/me', { name: n })
        await qc.invalidateQueries({ queryKey: qk.me })
      } catch (e) {
        toastError(e, t('onboarding.steps.welcome.yourNameWasNotSaved'))
        setBusy(false)
        return
      }
      setBusy(false)
    }
    g.next('done')
  }

  const first = firstName(name)
  return (
    <StepFrame
      title={first ? t('onboarding.steps.welcome.welcomeToMailr', { first }) : t('onboarding.steps.welcome.welcomeToMailr2')}
      lead={t('onboarding.steps.welcome.letsGetYouSetUp')}
      primary={{ label: t('onboarding.steps.welcome.letsGo'), onClick: () => void go(), busy, disabled: !name.trim() }}
    >
      <div className="ob-welcome">
        <div className="ob-me">
          <div className="ob-avatar" data-testid="welcome-picture">
            {pic ? <img src={pic} alt={t('common.yourPicture')} /> : <span aria-hidden>{(first || me.user.email)[0]?.toUpperCase()}</span>}
            {busy && (
              <span className="ob-avatar-busy">
                <Spinner small />
              </span>
            )}
          </div>
          <div className="ob-me-actions">
            <button type="button" className="btn tonal" onClick={() => file.current?.click()} disabled={busy}>
              <Camera /> {pic ? t('onboarding.steps.welcome.changePicture') : t('onboarding.steps.welcome.addAPicture')}
            </button>
            {me.picture?.idp_available && !fromProvider && (
              <button type="button" className="btn text" onClick={() => void picture(() => api.post('/me/picture/use-sign-in'))} disabled={busy}>
                <RotateCcw />{' '}{t('onboarding.steps.welcome.useMySignInPicture')}
              </button>
            )}
            <input
              ref={file}
              type="file"
              accept="image/*"
              hidden
              aria-label={t('onboarding.steps.welcome.chooseAPicture')}
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.target.value = ''
                if (!f) return
                const fd = new FormData()
                fd.append('file', f)
                void picture(() => api.put('/me/picture', fd))
              }}
            />
            <span className="ob-hint">{fromProvider ? t('onboarding.steps.welcome.thisPictureComesFromYour') : t('onboarding.steps.welcome.peopleYouWriteToSee')}</span>
          </div>
        </div>
        <div className="field ob-field">
          <label htmlFor="ob-name">{t('common.yourName')}</label>
          <input id="ob-name" className="input ob-input" value={name} autoComplete="name" onChange={(e) => setName(e.target.value)} />
          <span className="ob-hint">{t('onboarding.steps.welcome.thisIsTheNamePeople')}</span>
        </div>
      </div>
    </StepFrame>
  )
}

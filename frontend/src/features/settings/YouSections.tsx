// Settings → You: your name, the setup guide and installing the app. Your picture, password and
// devices come from PictureSection.tsx and AccountsTab.tsx; Region from CalendarPrefs.tsx.

import { Sparkles } from 'lucide-react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { qk, useMe, useSettings } from '../../api/hooks'
import type { Me } from '../../api/types'
import { toastError } from '../../stores/ui'
import { useRestartGuide } from '../onboarding/api'
import { InstallAppSetting } from '../pwa/DeviceSettings'
import { Section } from './common'
import { savedToast, useTextDraft } from './save'
import { t } from '../../i18n'

/** Your name, as other people see it on your mail and on shared calendars (PATCH /me). */
export function NameSection() {
  const qc = useQueryClient()
  const me = useMe().data
  const saved = me?.user.name ?? ''
  const rename = (name: string, undo?: string): void => {
    const prev = qc.getQueryData<Me>(qk.me)
    if (prev) qc.setQueryData<Me>(qk.me, { ...prev, user: { ...prev.user, name } })
    api
      .patch('/me', { name })
      .then(() => savedToast(undo === undefined ? t('common.changeUndone') : t('settings.youSections.nameSaved'), undo === undefined ? undefined : () => rename(undo)))
      .catch((e) => {
        if (prev) qc.setQueryData(qk.me, prev)
        toastError(e, t('settings.youSections.nameNotSaved'))
      })
      .finally(() => void qc.invalidateQueries({ queryKey: qk.me }))
  }
  const text = useTextDraft(saved, (v) => {
    const name = v.trim()
    if (name && name !== saved) rename(name, saved)
  })
  return (
    <Section title={t('common.yourName')} desc={t('settings.youSections.shownToThePeopleYou')}>
      <input className="input" style={{ maxWidth: 360 }} value={text.value} aria-label={t('common.yourName')} maxLength={200}
        onChange={(e) => text.set(e.target.value)} onBlur={text.flush} />
      {me && <p className="muted" style={{ margin: '6px 0 0', fontSize: 13 }}>{t('settings.youSections.youSignInAs', { email: me.user.email })}</p>}
    </Section>
  )
}

/** Open the guided setup again (features/onboarding). */
export function SetupGuideSection() {
  // ACT settings.setup_guide @web.settings
  const restart = useRestartGuide()
  const navigate = useNavigate()
  const st = useSettings().onboarding
  return (
    <Section title={t('settings.youSections.setupGuide')} desc={st?.status === 'done' ? t('settings.youSections.youFinishedTheGuideOpen') : t('settings.youSections.connectYourEmailChooseYour')}>
        <button
          type="button"
          className="btn tonal"
          disabled={restart.isPending}
          onClick={() =>
            st?.status === 'active' ? navigate('/setup') : restart.mutate(undefined, { onSuccess: () => navigate('/setup/welcome'), onError: (e) => toastError(e) })
          }
        >
          <Sparkles /> {st?.status === 'active' ? t('settings.youSections.continueTheSetupGuide') : t('settings.youSections.openTheSetupGuide')}
        </button>
    </Section>
  )
}

export function InstallSection() {
  return (
    <Section title={t('settings.youSections.installTheApp')}>
      <InstallAppSetting />
    </Section>
  )
}

/** Settings → Notes & tables: every note and list in one zip. */
export function NotesExportSection() {
  return (
    <Section title={t('settings.youSections.exportYourNotes')} desc={t('settings.youSections.everyNoteAndListAs')}>
      {/* ACT note.export @web.settings */}
      <a className="btn outlined sm" href="/api/v1/notes/export" download data-testid="notes-export">{t('settings.youSections.exportAllNotes')}</a>
    </Section>
  )
}

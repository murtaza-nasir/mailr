import { assistantNameNow } from '../../rail/assistant/useAssistantName'
// Settings → Ambrose → About you: what Ambrose knows about the user, and its options. Text saves
// when you leave a field or stop typing, a switch at once, each with Undo. The time zone is
// the account's one zone (You → Region); this page only shows it.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api } from '../../../api/client'
import { Skeleton } from '../../../components/bits'
import { toastError } from '../../../stores/ui'
import { Check, SetRow } from '../common'
import { savedToast, useTextDraft } from '../save'
import { qkA, type AssistantOptions, type AssistantProfile, type ProfileField } from './types'
import { t, tx } from '../../../i18n'

const ABOUT_MAX = 2000

type Draft = { fields: Partial<Record<ProfileField, string>>; options: Partial<AssistantOptions> }

export function useAssistantProfile() {
  return useQuery<AssistantProfile>({ queryKey: qkA.profile, queryFn: () => api.get<AssistantProfile>('/assistant/profile') })
}

/** PUT /assistant/profile with the change shown at once, and Undo. */
function useSaveProfile() {
  const qc = useQueryClient()
  const put = (d: Draft, undo: Draft | null): void => {
    const prev = qc.getQueryData<AssistantProfile>(qkA.profile)
    if (prev) qc.setQueryData<AssistantProfile>(qkA.profile, { ...prev, ...d.fields, options: { ...prev.options, ...d.options } })
    api
      .put<AssistantProfile>('/assistant/profile', { ...d.fields, ...(Object.keys(d.options).length ? { options: d.options } : {}) })
      .then((p) => {
        qc.setQueryData(qkA.profile, p)
        savedToast(undo ? t('settings.assistant.profileSection.willUseThisFromNow', { name: assistantNameNow() }) : t('common.changeUndone'), undo ? () => put(undo, null) : undefined)
      })
      .catch((e) => {
        if (prev) qc.setQueryData(qkA.profile, prev)
        toastError(e, t('common.notSaved'))
      })
  }
  return put
}

/** The values a change replaces, for Undo. */
function undoOf(p: AssistantProfile, d: Draft): Draft {
  const fields: Draft['fields'] = {}
  for (const k of Object.keys(d.fields) as ProfileField[]) fields[k] = p[k] ?? ''
  const options: Partial<AssistantOptions> = {}
  for (const k of Object.keys(d.options) as (keyof AssistantOptions)[]) (options as Record<string, unknown>)[k] = p.options[k]
  return { fields, options }
}

export function ProfileSection() {
  const q = useAssistantProfile()
  const put = useSaveProfile()
  const saved: Draft = { fields: {}, options: {} }
  // Text saves when you leave a field or stop typing; switches save at once.
  const text = useTextDraft(saved, (d) => {
    const p = q.data
    if (!p) return
    const changed: Draft = {
      fields: Object.fromEntries(Object.entries(d.fields).filter(([k, v]) => (p[k as ProfileField] ?? '') !== v)),
      options: Object.fromEntries(Object.entries(d.options).filter(([k, v]) => p.options[k as keyof AssistantOptions] !== v)),
    }
    if (Object.keys(changed.fields).length || Object.keys(changed.options).length) put(changed, undoOf(p, changed))
  })
  const draft = text.value

  if (q.isLoading || !q.data) return <Skeleton height={240} />
  const p = q.data
  const v = (k: ProfileField) => draft.fields[k] ?? p[k] ?? ''
  const set = (k: ProfileField, val: string) => text.set({ ...draft, fields: { ...draft.fields, [k]: val } })
  const setOpt = (patch: Partial<AssistantOptions>) => text.set({ ...draft, options: { ...draft.options, ...patch } })
  const o = { ...p.options, ...draft.options }
  const saveOpt = (patch: Partial<AssistantOptions>) => {
    text.flush()
    const d: Draft = { fields: {}, options: patch }
    put(d, undoOf(p, d))
  }
  const firstName = (p.account_name || '').split(/\s+/)[0]

  return (
    <>
      <SetRow k={t('common.name')} sub={t('settings.assistant.profileSection.yourAccountNameIs', { accountName: p.account_name || p.account_email })}>
        <div className="grid-2 as-fields">
          <div className="field">
            <label htmlFor="as-pref">{t('settings.assistant.profileSection.preferredName')}</label>
            <input id="as-pref" className="input" value={v('preferred_name')} placeholder={firstName || t('settings.assistant.profileSection.whatPeopleCallYou')} onChange={(e) => set('preferred_name', e.target.value)} onBlur={text.flush} />
          </div>
          <div className="field">
            <label htmlFor="as-sign">{t('settings.assistant.profileSection.signOffName')}</label>
            <input id="as-sign" className="input" value={v('signoff_name')} placeholder={v('preferred_name') || firstName || t('settings.assistant.profileSection.eGSam')} onChange={(e) => set('signoff_name', e.target.value)} onBlur={text.flush} />
          </div>
        </div>
        <span className="as-hint">{t('settings.assistant.profileSection.draftsSignWithThisName')}</span>
      </SetRow>
      <SetRow k={t('settings.assistant.profileSection.roleOrAffiliation')} sub={t('settings.assistant.profileSection.helpsPitchRepliesAndUnderstand', { name: assistantNameNow() })}>
        <input className="input" value={v('role')} placeholder={t('settings.assistant.profileSection.eGHeadOfOperations')} onChange={(e) => set('role', e.target.value)} onBlur={text.flush} aria-label={t('settings.assistant.profileSection.roleOrAffiliation')} />
      </SetRow>
      <SetRow k={t('common.timeZone')} sub={t('settings.assistant.profileSection.usedForTodayDeadlinesAnd')}>
        <p className="muted" style={{ margin: 0 }} data-testid="profile-zone">
          {tx('settings.assistant.profileSection.youChangeItUnderYou', { value: p.timezone ? p.timezone.replace(/_/g, ' ') : t('settings.assistant.profileSection.automaticThisBrowsersZone') }, { link: (c) => <Link to="/settings/you/region">{c}</Link> })}
        </p>
      </SetRow>
      <SetRow k={t('settings.assistant.profileSection.languages')} sub={t('settings.assistant.profileSection.languagesYouWriteIn')}>
        <input className="input" value={v('languages')} placeholder={t('settings.assistant.profileSection.eGEnglishSpanish')} onChange={(e) => set('languages', e.target.value)} onBlur={text.flush} aria-label={t('settings.assistant.profileSection.languages')} />
      </SetRow>
      <SetRow k={t('settings.assistant.profileSection.writingPreferences')} sub={t('settings.assistant.profileSection.appliedToEveryDraftSkills')}>
        <textarea className="textarea as-textarea" rows={3} value={v('writing')} placeholder={t('settings.assistant.profileSection.eGShortParagraphsBritish')} onChange={(e) => set('writing', e.target.value)} onBlur={text.flush} aria-label={t('settings.assistant.profileSection.writingPreferences')} />
      </SetRow>
      <SetRow k={t('settings.assistant.profileSection.aboutMe')} sub={t('settings.assistant.profileSection.anythingShouldKnowYourWork', { name: assistantNameNow() })}>
        <textarea
          className="textarea as-textarea"
          rows={7}
          maxLength={ABOUT_MAX}
          value={v('about')}
          placeholder={t('settings.assistant.profileSection.eGIRunOperations')}
          onChange={(e) => set('about', e.target.value)} onBlur={text.flush}
          aria-label={t('settings.assistant.profileSection.aboutMe')}
        />
        <span className="as-hint">
          {v('about').length} / {ABOUT_MAX}
        </span>
      </SetRow>
      <SetRow k={t('settings.assistant.profileSection.yourAssistantsName')} sub={t('settings.assistant.profileSection.itsNameAppearsInThe')}>
        <input
          className="input"
          style={{ maxWidth: 240 }}
          value={o.assistant_name ?? 'Ambrose'}
          maxLength={40}
          onChange={(e) => setOpt({ assistant_name: e.target.value })} onBlur={text.flush}
          aria-label={t('settings.assistant.profileSection.yourAssistantsName')}
        />
        <div className="as-hint" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
          {t('settings.assistant.profileSection.quietHoursForItsNotifications')}
          <input type="time" className="input" style={{ width: 120 }} value={o.quiet_from ?? ''}
            onChange={(e) => setOpt({ quiet_from: e.target.value })} onBlur={text.flush} aria-label={t('settings.assistant.profileSection.quietFrom')} />
          {t('settings.assistant.profileSection.to')}
          <input type="time" className="input" style={{ width: 120 }} value={o.quiet_to ?? ''}
            onChange={(e) => setOpt({ quiet_to: e.target.value })} onBlur={text.flush} aria-label={t('settings.assistant.profileSection.quietUntil')} />
          <span>{t('settings.assistant.profileSection.heldBackAndSentAs')}</span>
        </div>
      </SetRow>
      <SetRow k={t('settings.assistant.profileSection.learning')}>
        <Check
          checked={o.learn_from_mail}
          onChange={(x) => saveOpt({ learn_from_mail: x })}
          label={t('settings.assistant.profileSection.learnFromMyMail')}
          desc={t('settings.assistant.profileSection.onceADayReadsYour', { name: assistantNameNow() })}
        />
        <Check
          checked={o.learn_from_chats ?? true}
          onChange={(x) => saveOpt({ learn_from_chats: x })}
          label={t('memory.settings.learnFromChats')}
          desc={t('memory.settings.learnFromChatsDesc', { name: assistantNameNow() })}
        />
        <Check
          checked={o.auto_recall}
          onChange={(x) => saveOpt({ auto_recall: x })}
          label={t('settings.assistant.profileSection.recallMemoriesAutomaticallyInChat')}
          desc={t('settings.assistant.profileSection.relevantMemoriesAboutThePeople', { name: assistantNameNow() })}
        />
      </SetRow>
    </>
  )
}

// Settings → Mail → Reading, Writing and Inbox (VIP): the rows the old General tab held. Every
// change saves at once with Undo (save.ts).

import { useState } from 'react'
import { useLabelList } from '../../api/hooks'
import type { Settings } from '../../api/types'
import { SwipeSetting } from '../pwa/DeviceSettings'
import { Check, Radio, Section, SetRow } from './common'
import { useInstantSettings, useTextDraft } from './save'
import { t } from '../../i18n'



export function ReadingSection() {
  const { s, set } = useInstantSettings()
  return (
    <Section title={t('common.reading')}>
      <SetRow k={t('settings.mailSections.conversationView')} sub={t('settings.mailSections.groupMessagesOnTheSame')}>
        <Radio name="cv" value="on" current={s.conversation_view ? 'on' : 'off'} onChange={() => set('conversation_view', true)} label={t('settings.mailSections.conversationViewOn')} />
        <Radio name="cv" value="off" current={s.conversation_view ? 'on' : 'off'} onChange={() => set('conversation_view', false)} label={t('settings.mailSections.conversationViewOff')} />
      </SetRow>
      <SetRow k={t('settings.mailSections.pageSize')} sub={t('settings.mailSections.conversationsShownPerPage')}>
        <div className="row">
          {t('common.show')}
          <select className="select" style={{ width: 100 }} value={s.page_size} onChange={(e) => set('page_size', Number(e.target.value))} aria-label={t('settings.mailSections.pageSize2')}>
            {[25, 50, 100].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
          {t('settings.mailSections.conversationsPerPage')}
        </div>
      </SetRow>
      <SetRow k={t('settings.mailSections.autoAdvance')} sub={t('settings.mailSections.afterYouArchiveDeleteOr')}>
        <Radio name="aa" value="list" current={s.auto_advance ?? 'list'} onChange={(v) => set('auto_advance', v)} label={t('settings.mailSections.goBackToTheList')} />
        <Radio name="aa" value="newer" current={s.auto_advance ?? 'list'} onChange={(v) => set('auto_advance', v)} label={t('settings.mailSections.goToTheNextNewer')} />
        <Radio name="aa" value="older" current={s.auto_advance ?? 'list'} onChange={(v) => set('auto_advance', v)} label={t('settings.mailSections.goToThePreviousOlder')} />
      </SetRow>
      {/* ACT mail.show_images @web.settings */}
      <SetRow k={t('settings.mailSections.images')} sub={t('settings.mailSections.trackingPixelsAreRemovedIn')}>
        <Radio
          name="img"
          value="always"
          current={s.images}
          onChange={(v) => set('images', v)}
          label={t('settings.mailSections.alwaysDisplayImages')}
          desc={t('settings.mailSections.imagesAreFetchedWhenMail')}
        />
        <Radio name="img" value="ask" current={s.images} onChange={(v) => set('images', v)} label={t('settings.mailSections.askBeforeDisplayingImages')} desc={t('settings.mailSections.imagesLoadOnlyAfterYou')} />
        <Radio name="img" value="never" current={s.images} onChange={(v) => set('images', v)} label={t('settings.mailSections.neverDisplayImages')} />
      </SetRow>
      <SetRow k={t('settings.mailSections.senderPictures')} sub={t('settings.mailSections.yourOwnAddressPicturesAlways')}>
        <Radio
          name="pics"
          value="off"
          current={s.sender_pictures ? 'on' : 'off'}
          onChange={() => set('sender_pictures', false)}
          label={t('settings.mailSections.showInitials')}
          desc={t('settings.mailSections.noOneElseIsAsked')}
        />
        <Radio
          name="pics"
          value="on"
          current={s.sender_pictures ? 'on' : 'off'}
          onChange={() => set('sender_pictures', true)}
          label={t('settings.mailSections.lookUpSenderPictures')}
          desc={t('settings.mailSections.yourMailrServerAsksThe')}
        />
      </SetRow>
      <SwipeSetting />
    </Section>
  )
}

/** What Archive removes: always Inbox; optionally the label of the list you archive from, and
 *  labels chosen here (enforced by the server, so every app and Ambrose behave the same). */
export function ArchiveSection() {
  const { s, set } = useInstantSettings()
  const labels = useLabelList().filter((l) => !l.role).sort((a, b) => a.name.localeCompare(b.name))
  const current = s.archive_removes_current_label ?? true
  const also = new Set(s.archive_also_removes ?? [])
  return (
    <Section title={t('common.archive')} desc={t('settings.mailSections.archiveAlwaysTakesAConversation')}>
      <SetRow k={t('settings.mailSections.archive')}>
        <Radio name="arch" value="current" current={current ? 'current' : 'inbox'} onChange={() => set('archive_removes_current_label', true)}
          label={t('settings.mailSections.alsoRemoveTheLabelOf')}
          desc={t('settings.mailSections.archivingInALabelsList')} />
        <Radio name="arch" value="inbox" current={current ? 'current' : 'inbox'} onChange={() => set('archive_removes_current_label', false)}
          label={t('settings.mailSections.onlyRemoveItFromThe')} desc={t('settings.mailSections.otherLabelsAlwaysStay')} />
        {labels.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div className="field-label" style={{ marginBottom: 4 }}>{t('settings.mailSections.alwaysAlsoRemoveTheseLabels')}</div>
            <div className="archive-labels">
              {labels.map((l) => (
                <Check key={l.id} checked={also.has(l.id)} label={l.name}
                  onChange={(v) => set('archive_also_removes', v ? [...also, l.id] : [...also].filter((x) => x !== l.id))} />
              ))}
            </div>
          </div>
        )}
      </SetRow>
    </Section>
  )
}

export function WritingSection() {
  const { s, set } = useInstantSettings()
  return (
    <Section title={t('settings.mailSections.writing')}>
      <SetRow k={t('settings.mailSections.defaultReply')}>
        <Radio name="reply" value="reply" current={s.default_reply} onChange={(v) => set('default_reply', v)} label={t('common.reply')} />
        <Radio name="reply" value="reply_all" current={s.default_reply} onChange={(v) => set('default_reply', v)} label={t('common.replyAll')} />
      </SetRow>
      <SetRow k={t('settings.mailSections.undoSend')}>
        <div className="row">
          {t('settings.mailSections.youCanCancelAMessage')}
          <select className="select" style={{ width: 100 }} value={s.undo_send_seconds} onChange={(e) => set('undo_send_seconds', Number(e.target.value))} aria-label={t('settings.mailSections.undoSendSeconds')}>
            {[5, 10, 20, 30].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          {t('settings.mailSections.secondsAfterYouSendIt')}
        </div>
      </SetRow>
      <SetRow k={t('settings.mailSections.sendAndArchive')}>
        <Radio name="sa" value="yes" current={s.send_and_archive ? 'yes' : 'no'} onChange={() => set('send_and_archive', true)} label={t('settings.mailSections.showSendArchiveOnReplies')} desc={t('settings.mailSections.replyingArchivesTheConversation')} />
        <Radio name="sa" value="no" current={s.send_and_archive ? 'yes' : 'no'} onChange={() => set('send_and_archive', false)} label={t('settings.mailSections.keepConversationsInTheInbox')} />
      </SetRow>
      <SetRow k={t('settings.mailSections.signaturePlacement')} sub={t('settings.mailSections.eachAddressHasItsOwn')}>
        <Check checked={s.signature_placement === 'above'} onChange={(v) => set('signature_placement', v ? 'above' : 'below')} label={t('settings.mailSections.insertSignatureBeforeQuotedText')} />
      </SetRow>
    </Section>
  )
}

type Vacation = { enabled: boolean; start: string; end: string; subject: string; message: string; contacts_only: boolean }

export function AwaySection() {
  const { s, set } = useInstantSettings()
  const saved: Vacation = { enabled: false, start: '', end: '', subject: '', message: '', contacts_only: false, ...(s.vacation ?? {}) }
  // The subject and text save when you leave the field or stop typing; the switches at once.
  const text = useTextDraft(saved, (v) => set('vacation', v as Settings['vacation']))
  const v = text.value
  const put = (patch: Partial<Vacation>, now = true) => {
    if (now) {
      text.flush()
      set('vacation', { ...v, ...patch } as Settings['vacation'])
    } else text.set({ ...v, ...patch })
  }
  return (
    <Section title={t('settings.mailSections.awayMessage')} desc={t('settings.mailSections.sentOnceEvery4Days')}>
      <SetRow k={t('settings.mailSections.awayMessage2')}>
        <Radio name="vac" value="off" current={v.enabled ? 'on' : 'off'} onChange={() => put({ enabled: false })} label={t('settings.mailSections.awayMessageOff')} />
        <Radio name="vac" value="on" current={v.enabled ? 'on' : 'off'} onChange={() => put({ enabled: true })} label={t('settings.mailSections.awayMessageOn')} />
        <div style={{ display: 'grid', gap: 8, maxWidth: 560, marginTop: 6, opacity: v.enabled ? 1 : 0.6 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {t('settings.mailSections.firstDay')}{' '}<input type="date" className="input" style={{ width: 170 }} value={v.start} onChange={(e) => put({ start: e.target.value })} aria-label={t('settings.mailSections.firstDay')} />
            {t('settings.mailSections.lastDay')}{' '}<input type="date" className="input" style={{ width: 170 }} value={v.end} onChange={(e) => put({ end: e.target.value })} aria-label={t('settings.mailSections.lastDayOptional')} />
          </div>
          <input className="input" placeholder={t('settings.mailSections.subjectDefaultReTheirSubject')} value={v.subject} onChange={(e) => put({ subject: e.target.value }, false)} onBlur={text.flush} aria-label={t('settings.mailSections.awayMessageSubject')} />
          <textarea className="textarea" rows={5} placeholder={t('settings.mailSections.iAmAwayUntilAnd')} value={v.message} onChange={(e) => put({ message: e.target.value }, false)} onBlur={text.flush} aria-label={t('settings.mailSections.awayMessage')} />
          <Check checked={v.contacts_only} onChange={(x) => put({ contacts_only: x })} label={t('settings.mailSections.onlySendToPeopleI')} />
        </div>
      </SetRow>
    </Section>
  )
}

/** VIP senders: their mail is always important and can be the only mail that notifies. */
export function VipSection() {
  const { s, set } = useInstantSettings()
  return <VipRow vip={s.vip ?? []} save={(v) => set('vip', v)} />
}

function VipRow({ vip, save }: { vip: string[]; save: (v: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const valid = /^[^@\s]+@[^@\s]+$/.test(draft.trim())
  const add = () => {
    if (!valid) return
    save([...vip, draft.trim().toLowerCase()])
    setDraft('')
  }
  return (
    <Section title={t('settings.mailSections.vipSenders')} desc={t('settings.mailSections.theirMailIsAlwaysMarked')}>
      <div className="vip-list">
        {vip.length === 0 && <span className="muted">{t('settings.mailSections.noVipSendersYet')}</span>}
        {vip.map((a) => (
          <span key={a} className="chip vip-chip">
            {a}
            <button type="button" className="icon-btn xs" aria-label={t('settings.mailSections.removeFromVipSenders', { a })} onClick={() => save(vip.filter((x) => x !== a))}>×</button>
          </span>
        ))}
      </div>
      <div className="row" style={{ gap: 8, marginTop: 8 }}>
        <input className="input" type="email" placeholder={t('settings.mailSections.nameExampleCom')} aria-label={t('settings.mailSections.addAVipSender')} value={draft}
          onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} style={{ maxWidth: 280 }} />
        <button type="button" className="btn tonal sm" disabled={!valid} onClick={add}>{t('common.add')}</button>
      </div>
    </Section>
  )
}


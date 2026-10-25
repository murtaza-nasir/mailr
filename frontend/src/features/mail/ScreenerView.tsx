// Mail → Screener (web.screener; backend core/screener.py): one card per first-time sender whose mail
// waits out of the Inbox, with the latest message's subject and preview, the sender's name and
// address, who the decision covers (the address, or everyone at a company's domain), the
// authentication hint from the receiving server's results, and Allow / Block / Label.
//
// The cards are a list, not the conversation grid: each card is a group of buttons. Opening the
// preview reads the held conversation in the normal reader (/mail/screener/<id>).

import { Ban, Check, ChevronDown, Inbox as InboxIcon, Rss, Settings as SettingsIcon, ShieldAlert, ShieldCheck, ShieldQuestion, Tag, Trash2, UserCheck } from 'lucide-react'
import { useNavigate } from 'react-router'
import { useLabelList } from '../../api/hooks'
import { Avatar, EmptyState, LoadFailed, Spinner, failedEmpty } from '../../components/bits'
import { MenuHeader, MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { formatRelative } from '../../lib/format'
import { mailPath } from '../../lib/views'
import './screener.css'
import { heldText, senderScope, senderTitle, useDecide, useScreener, type AuthLevel, type ScreenerSender } from './screener'
import { t } from '../../i18n'

const AUTH_ICON: Record<AuthLevel, typeof ShieldCheck> = { verified: ShieldCheck, failed: ShieldAlert, unknown: ShieldQuestion }

export function ScreenerView() {
  const nav = useNavigate()
  const q = useScreener()
  const senders = q.data?.senders ?? []
  const n = senders.length
  return (
    <div className="screener-view">
      <div className="screener-head">
        <div>
          <h2>{t('common.screener')}</h2>
          <p className="muted screener-sub">
            {n ? t('mail.screenerView.newSendersWaiting', { count: n }) : t('mail.screenerView.peopleWritingToYouFor')}
          </p>
        </div>
        <button type="button" className="btn text" onClick={() => nav('/settings/mail/spam#screener')}>
          <SettingsIcon size={16} />{' '}{t('mail.screenerView.screenerSettings')}
        </button>
      </div>
      {q.isLoading ? (
        <div className="screener-empty"><Spinner /></div>
      ) : failedEmpty(q) ? (
        <LoadFailed what={t('mail.screenerView.theScreener')} q={q} />
      ) : n === 0 ? (
        q.data?.enabled ? (
          <EmptyState icon={<UserCheck />} title={t('mail.screenerView.noNewSenders')}>
            <p>{t('mail.screenerView.whenSomeoneWritesToYou')}</p>
          </EmptyState>
        ) : (
          <EmptyState icon={<UserCheck />} title={t('mail.screenerView.theScreenerIsOff')}>
            <p>{t('mail.screenerView.turnItOnToKeep')}</p>
            <button type="button" className="btn filled" onClick={() => nav('/settings/mail/spam#screener')}>{t('mail.screenerView.turnOnTheScreener')}</button>
          </EmptyState>
        )
      ) : (
        <>
          <p className="screener-intro">
            {t('mail.screenerView.allowASenderToLet')}
          </p>
          <ul className="screener-list" aria-label={t('mail.screenerView.sendersWaiting')}>
            {senders.map((s) => (
              <SenderCard key={s.id} s={s} onOpen={() => nav(mailPath('screener', s.latest.conversation_id))} />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function SenderCard({ s, onOpen }: { s: ScreenerSender; onOpen: () => void }) {
  const decide = useDecide()
  const labels = useLabelList().filter((l) => !l.role && l.name.toLowerCase() !== 'feed')
  const block = useAnchor<HTMLButtonElement>()
  const label = useAnchor<HTMLButtonElement>()
  const who = senderTitle(s)
  const AuthIcon = AUTH_ICON[s.auth.level]
  const titleId = `screener-${s.id}`
  const go = (d: Parameters<typeof decide.mutate>[0]['d']) => decide.mutate({ sender: s, d, who })
  return (
    <li className="screener-card" aria-labelledby={titleId}>
      <div className="screener-who">
        <Avatar who={{ email: s.email, name: s.name }} />
        <div className="screener-names">
          <div className="screener-name" id={titleId}>{who}</div>
          <div className="muted screener-addr" title={s.addresses.join(', ')}>{s.email}</div>
        </div>
      </div>
      <div className="screener-facts">
        <span className="screener-chip">{senderScope(s)}</span>
        <span className={`screener-chip auth-${s.auth.level}`} data-tip={s.auth.text} aria-label={s.auth.text}>
          <AuthIcon size={14} aria-hidden="true" />
          {s.auth.level === 'verified' ? t('mail.screenerView.verified') : s.auth.level === 'failed' ? t('mail.screenerView.failedAuthentication') : t('mail.screenerView.notVerified')}
        </span>
        {s.newsletter && <span className="screener-chip">{t('mail.screenerView.mailingList')}</span>}
        <span className="muted screener-meta">
          {heldText(s)}{s.account ? ` · ${s.account}` : ''}{s.first_seen ? t('mail.screenerView.firstSeen', { relative: formatRelative(s.first_seen) }) : ''}
        </span>
      </div>
      <button type="button" className="screener-preview" onClick={onOpen} aria-label={t('mail.screenerView.read', { subject: s.latest.subject || t('common.noSubject') })}>
        <span className={`screener-subject${s.unread ? ' unread' : ''}`}>{s.latest.subject || t('common.noSubject')}</span>
        <span className="muted screener-snippet">{s.latest.snippet}</span>
        <span className="muted screener-date">{formatRelative(s.latest.date)}</span>
      </button>
      <div className="screener-actions" role="group" aria-label={t('mail.screenerView.decideAbout', { who })}>
        {/* ACT screener.allow @web.screener */}
        <button type="button" className="btn filled" disabled={decide.isPending} onClick={() => go({ decision: 'allow' })}>
          <Check size={16} />{' '}{t('mail.screenerView.allow')}
        </button>
        {/* ACT screener.block @web.screener */}
        <button type="button" className="btn outlined" disabled={decide.isPending} aria-haspopup="menu" aria-expanded={block.open}
          onClick={(e) => block.toggle(e.currentTarget)}>
          <Ban size={16} />{' '}{t('mail.screenerView.block2')}{' '}<ChevronDown size={14} />
        </button>
        {/* ACT screener.label @web.screener */}
        <button type="button" className="btn outlined" disabled={decide.isPending} aria-haspopup="menu" aria-expanded={label.open}
          onClick={(e) => label.toggle(e.currentTarget)}>
          <Tag size={16} />{' '}{t('common.label')}{' '}<ChevronDown size={14} />
        </button>
      </div>
      <Popover anchor={block.anchor} open={block.open} onClose={block.close} label={t('mail.screenerView.block', { who })} minWidth={240}>
        <MenuItem icon={<Ban />} onSelect={() => { block.close(); go({ decision: 'block', block_to: 'spam' }) }} danger>
          {t('mail.screenerView.blockMailGoesToSpam')}
        </MenuItem>
        <MenuItem icon={<Trash2 />} onSelect={() => { block.close(); go({ decision: 'block', block_to: 'trash' }) }} danger>
          {t('mail.screenerView.blockAndDeleteMailGoes')}
        </MenuItem>
      </Popover>
      <Popover anchor={label.anchor} open={label.open} onClose={label.close} label={t('mail.screenerView.label', { who })} minWidth={240}>
        <MenuItem icon={<Rss />} onSelect={() => { label.close(); go({ decision: 'label', feed: true }) }}>{t('mail.screenerView.feed')}</MenuItem>
        {labels.length > 0 && <MenuSep />}
        {labels.length > 0 && <MenuHeader>{t('mail.screenerView.yourLabels')}</MenuHeader>}
        {labels.map((l) => (
          <MenuItem key={l.id} icon={<InboxIcon />} onSelect={() => { label.close(); go({ decision: 'label', label_id: l.id }) }}>
            {l.name}
          </MenuItem>
        ))}
      </Popover>
    </li>
  )
}

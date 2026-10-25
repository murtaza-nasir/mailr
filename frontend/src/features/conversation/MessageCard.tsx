// ACT mail.delete, ACT mail.spam, ACT mail.unread: the same actions on one message, from its ⋮ menu.
// ACT mail.reply @web.reader, ACT mail.forward @web.reader, ACT mail.star @web.reader (the message's star), ACT mail.to_event @web.reader, ACT mail.to_task @web.reader (the
// message menu; FromMessageDialog does the work)
import {
  CalendarPlus, ListTodo, ChevronDown, CornerUpLeft, CornerUpRight, EllipsisVertical, Filter, Forward, MailX, Printer, Reply, ReplyAll, ShieldAlert,
  Star, Trash2, OctagonAlert, FileCode2, Split, Ban, Download, File as FileIcon, Eye, Pencil, Languages, Crown,
  TriangleAlert, StickyNote, ListChecks, Copy,
} from 'lucide-react'
import { Fragment, useState, type HTMLAttributes } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api, API } from '../../api/client'
import { qk, useActions, useSettings, useUpdateSettings } from '../../api/hooks'
import type { Address, Attachment, Label, Message } from '../../api/types'
import { Avatar, ItemBoundary, LabelChip } from '../../components/bits'
import { ConfirmDialog } from '../../components/Dialog'
import { MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { addressLine, displayName, formatFullDate, formatListDate, formatSize, personName } from '../../lib/format'
import { mixedScript, nameAddressMismatch, riskyAttachment, safeText } from '../../lib/safeText'
import { filterQueryForSender } from '../../lib/search'
import { toast, toastError } from '../../stores/ui'
import { emptyPayload, useCompose } from '../../stores/compose'
import { escapeHtml } from '../../lib/html'
import { EventChangeCard, FromMessageDialog, InviteCard } from './CalendarBits'
import { hasInvite } from '../../lib/calendarForm'
import { MessageBody } from './MessageBody'
import { AddressCopyButton, CopyableAddress, copyAddress, useAddressMenu } from './AddressCopy'
import { TranslateBar, TranslatedText, useTranslate } from './Translate'
import { ReplySuggestions } from './ReplySuggestions'
import { AttachmentViewer } from './AttachmentViewer'
import { AttachedEmails } from './AttachedEmails'
import { attIcon, splitExt } from './attachmentBits'
import { RecordingMailCard, TranscribeButton } from '../speakr/MailCard'
import { mailPath } from '../../lib/views'
import { noteFromEmail } from '../notes/actions'
import { runUndo } from '../notes/api'
import { notePath } from '../../lib/routes'
import { SendStatusLine, type SendState } from '../mail/outbox'
import { t, tx, appLocale, isolate } from '../../i18n'

export type ReplyMode = 'reply' | 'reply_all' | 'forward'

interface Props {
  m: Message
  expanded: boolean
  onToggle: () => void
  onReply: (mode: ReplyMode, m: Message, text?: string) => void
  onEditDraft: (m: Message) => void
  labelsById: Map<number, Label>
  inSpam: boolean
  isLast: boolean
  /** A quote to highlight (opened from a citation). */
  highlight?: string | null
  /** On its way, not sent, waiting for approval, or just sent. */
  sendState?: SendState | null
}

/** What the thread calls a message that has not left: the word in place of "Draft". */
function draftWord(s: SendState | null | undefined): string {
  if (!s) return t('common.draft')
  return s.kind === 'sending' ? t('conversation.messageCard.sending') : s.kind === 'not_sent' ? t('common.notSent') : s.kind === 'approval' ? t('conversation.messageCard.waitingForApproval') : t('common.draft')
}

function recipientSummary(m: Message): string {
  const all = [...m.to, ...m.cc]
  if (!all.length) return m.bcc.length ? t('conversation.messageCard.bccMe') : ''
  // Each name in its own direction: an Arabic name beside an English one keeps the commas in place.
  const names = all.slice(0, 3).map((a) => isolate(displayName(a)))
  return t('conversation.messageCard.to', { names: names.join(', '), value: all.length > 3 ? ` +${all.length - 3}` : '' })
}

function unsubscribeTarget(header: string | null): { kind: 'http' | 'mailto'; url: string } | null {
  if (!header) return null
  const parts = header.match(/<([^>]+)>/g)?.map((p) => p.slice(1, -1)) ?? []
  const http = parts.find((p) => /^https:/i.test(p))
  if (http) return { kind: 'http', url: http }
  const mailto = parts.find((p) => /^mailto:/i.test(p))
  return mailto ? { kind: 'mailto', url: mailto } : null
}

export function MessageCard({ m, expanded, onToggle, onReply, onEditDraft, labelsById, inSpam, isLast, highlight, sendState }: Props) {
  const translate = useTranslate(m.id)
  const [showTranslation, setShowTranslation] = useState(false)
  const vipList = useSettings().vip ?? []
  const saveSettings = useUpdateSettings()
  const isVip = vipList.includes(m.from.email.toLowerCase())
  const toggleVip = () => {
    const next = isVip ? vipList.filter((a) => a !== m.from.email.toLowerCase()) : [...vipList, m.from.email.toLowerCase()]
    saveSettings.mutate({ vip: next }, { onSuccess: () => toast({ message: isVip ? t('conversation.messageCard.isNoLongerAVip', { displayName: displayName(m.from) }) : t('conversation.messageCard.isAVipSenderTheir', { displayName: displayName(m.from) }) }) })
  }
  const { run } = useActions()
  const nav = useNavigate()
  const qc = useQueryClient()
  const openCompose = useCompose((s) => s.open)
  const more = useAnchor()
  const addrMenu = useAddressMenu()
  const details = useAnchor()
  const [confirmBlock, setConfirmBlock] = useState(false)
  const [confirmUnsub, setConfirmUnsub] = useState(false)
  const [fromMsg, setFromMsg] = useState<'event' | 'task' | null>(null)
  const unsub = unsubscribeTarget(m.list_unsubscribe)
  const dmarcFail = ['fail', 'reject', 'quarantine'].includes((m.auth_results?.dmarc ?? '').toLowerCase())
  // The text selected in this message, if any (note.from_email keeps it; a checklist takes its lines).
  const saveToNote = async (kind: 'text' | 'checklist') => {
    const sel = window.getSelection()?.toString().trim() || null
    try {
      const { note, undo } = await noteFromEmail(qc, m.conversation_id, sel, kind)
      toast({
        message: kind === 'checklist' ? t('conversation.messageCard.checklistMadeFromTheEmail') : t('conversation.messageCard.savedToANote'), actionLabel: t('common.open'), onAction: () => nav(notePath(note.id)),
        ...(undo ? { secondaryLabel: t('common.undo'), onSecondary: () => void runUndo(qc, undo) } : {}), duration: 7000,
      })
    } catch {
      /* write() showed the error */
    }
  }
  const actOnMessage = (action: 'unread' | 'trash' | 'spam' | 'star' | 'unstar') =>
    run({ ids: [m.id], action }, { scope: 'messages', conversationIds: [m.conversation_id] })

  if (!expanded) {
    return (
      <div data-message-id={m.id} className="msg collapsed" role="button" tabIndex={0} onClick={onToggle} aria-expanded={false}
        onKeyDown={(e) => {
          // Enter and Space, as on a button.
          if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return
          e.preventDefault()
          onToggle()
        }}>
        <Avatar who={m.from} />
        <div className="msg-main">
          <div className="msg-head">
            <div className="who" style={{ display: 'flex', gap: 12, minWidth: 0 }}>
              <span className="name" style={{ fontWeight: m.seen ? 500 : 700, flex: 'none' }}>
                {m.draft ? <span className={`msg-draft-word${sendState ? ` s-${sendState.kind}` : ''}`}>{draftWord(sendState)}</span> : m.sent_by_me ? 'me' : <bdi>{displayName(m.from)}</bdi>}
              </span>
              <span className="snip" dir="auto">{m.snippet}</span>
            </div>
            <div className="meta">
              {m.has_attachments && <FileIcon size={14} />}
              <span className="date">{formatListDate(m.date)}</span>
              {m.flagged && <Star size={16} fill="var(--star)" color="var(--star)" />}
            </div>
          </div>
        </div>
      </div>
    )
  }

  const userLabels = m.label_ids.map((id) => labelsById.get(id)).filter((l): l is Label => !!l && !l.role)

  return (
    <article data-message-id={m.id} className={`msg${highlight ? " cited" : ""}`} aria-label={t('conversation.messageCard.messageFrom', { displayName: displayName(m.from) })}>
      <Avatar who={m.from} />
      <div className="msg-main">
        <div className="msg-head">
          <div className="who" onClick={onToggle}>
            {/* The header collapses the message on click; the keyboard gets a real button for it,
                shown when focused. */}
            <button type="button" className="msg-collapse" aria-expanded={true} onClick={(e) => { e.stopPropagation(); onToggle() }}>
              {t('conversation.messageCard.collapseMessage')}
            </button>
            {/* The sender's line: the name keeps its room longest; the address gives way first (msg-head
                container queries in conversation.css). */}
            <div className="who-line">
              {m.draft && <span className={`msg-draft-word${sendState ? ` s-${sendState.kind}` : ''}`} style={{ fontWeight: 500, marginInlineEnd: 4 }}>{draftWord(sendState)}</span>}
              <bdi className="name" {...addrMenu.press(m.from)}>{personName(safeText(m.from.name)) || displayName(m.from)}</bdi>
              {/* The address and its copy button (AddressCopy.tsx): the button keeps its room while
                  hidden and the address text gives way first, so showing it moves nothing. */}
              <span className="email" {...addrMenu.press(m.from)}>
                <span className="email-text">&lt;<bdi>{m.from.email}</bdi>&gt;</span>
                <AddressCopyButton addr={m.from} />
              </span>
              <SenderWarning name={m.from.name} email={m.from.email} problem={m.auth_results?.from_problem} />
              {unsub && !m.sent_by_me && (
                <button
                  type="button"
                  className="unsub"
                  onClick={(e) => {
                    e.stopPropagation()
                    setConfirmUnsub(true)
                  }}
                >
                  {t('conversation.messageCard.unsubscribe')}
                </button>
              )}
            </div>
            <div className="to-line">
              <span className="ellipsis">{recipientSummary(m)}</span>
              <button type="button" className="icon-btn xs" aria-label={t('conversation.messageCard.showDetails')} data-tip={t('conversation.messageCard.showDetails')} onClick={(e) => { e.stopPropagation(); details.toggle(e.currentTarget) }}>
                <ChevronDown />
              </button>
            </div>
          </div>
          <div className="meta">
            {m.has_attachments && <FileIcon size={14} aria-label={t('conversation.messageCard.hasAttachments')} />}
            <span className="date" title={m.date ? new Date(m.date).toLocaleString(appLocale()) : ''}>
              <span className="long">{formatFullDate(m.date)}</span>
              <span className="short">{formatListDate(m.date)}</span>
            </span>
            <button
              type="button"
              className={`icon-btn${m.flagged ? ' star-on' : ''}`}
              aria-label={t('common.star')}
              aria-pressed={m.flagged}
              data-tip={m.flagged ? t('common.starred') : t('conversation.messageCard.notStarred')}
              onClick={() => void run({ ids: [m.id], action: m.flagged ? 'unstar' : 'star' }, { scope: 'messages', silent: true, conversationIds: [m.conversation_id] })}
            >
              <Star fill={m.flagged ? 'var(--star)' : 'none'} color={m.flagged ? 'var(--star)' : 'currentColor'} />
            </button>
            {m.draft ? (
              <button type="button" className="icon-btn" aria-label={t('conversation.messageCard.editDraft')} data-tip={t('common.edit2')} onClick={() => onEditDraft(m)}>
                <Pencil />
              </button>
            ) : (
              <button type="button" className="icon-btn" aria-label={t('common.reply')} data-tip={t('common.reply')} onClick={() => onReply('reply', m)}>
                <Reply />
              </button>
            )}
            <button type="button" className="icon-btn" aria-label={t('common.more')} data-tip={t('common.more')} onClick={(e) => more.toggle(e.currentTarget)}>
              <EllipsisVertical />
            </button>
          </div>
        </div>
        {addrMenu.menu}
        {sendState && <SendStatusLine state={sendState} onEditDraft={() => onEditDraft(m)} />}

        <Popover anchor={details.anchor} open={details.open} onClose={details.close} role="dialog" label={t('conversation.messageCard.messageDetails')} minWidth={320} className="details-popover">
          <dl className="details-pop">
            <dt>{t('conversation.messageCard.from')}</dt>
            <dd><CopyableAddress addr={m.from} press={addrMenu.press(m.from)}>{addressLine(m.from)}</CopyableAddress></dd>
            {m.reply_to.length > 0 && (
              <>
                <dt>{t('conversation.messageCard.replyTo')}</dt>
                <dd><AddressList list={m.reply_to} press={addrMenu.press} /></dd>
              </>
            )}
            {m.to.length > 0 && (
              <>
                <dt>{t('conversation.messageCard.to2')}</dt>
                <dd><AddressList list={m.to} press={addrMenu.press} /></dd>
              </>
            )}
            {m.cc.length > 0 && (
              <>
                <dt>{t('conversation.messageCard.cc')}</dt>
                <dd><AddressList list={m.cc} press={addrMenu.press} /></dd>
              </>
            )}
            {m.bcc.length > 0 && (
              <>
                <dt>{t('conversation.messageCard.bcc')}</dt>
                <dd><AddressList list={m.bcc} press={addrMenu.press} /></dd>
              </>
            )}
            <dt>{t('conversation.messageCard.date')}</dt>
            <dd>{m.date ? new Date(m.date).toLocaleString(appLocale()) : ''}</dd>
            <dt>{t('conversation.messageCard.subject')}</dt>
            <dd dir="auto">{m.subject}</dd>
            {Object.entries(m.auth_results ?? {}).filter(([k]) => k !== 'from_problem').map(([k, v]) => (
              <span key={k} style={{ display: 'contents' }}>
                <dt>{k}:</dt>
                <dd>
                  <span className={`pill ${v === 'pass' ? 'ok' : v === 'fail' ? 'err' : 'warn'}`}>{v}</span>
                </dd>
              </span>
            ))}
            {userLabels.length > 0 && (
              <>
                <dt>{t('conversation.messageCard.labels')}</dt>
                <dd style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {userLabels.map((l) => (
                    <LabelChip key={l.id} label={l} />
                  ))}
                </dd>
              </>
            )}
          </dl>
        </Popover>

        <Popover anchor={more.anchor} open={more.open} onClose={more.close} placement="bottom-end" minWidth={240}>
          <MenuItem icon={<CornerUpLeft />} onSelect={() => { more.close(); onReply('reply', m) }}>
            {t('common.reply')}
          </MenuItem>
          <MenuItem icon={<ReplyAll />} onSelect={() => { more.close(); onReply('reply_all', m) }}>
            {t('common.replyAll')}
          </MenuItem>
          <MenuItem icon={<CornerUpRight />} onSelect={() => { more.close(); onReply('forward', m) }}>
            {t('common.forward')}
          </MenuItem>
          <MenuSep />
          <MenuItem icon={<CalendarPlus />} onSelect={() => { more.close(); setFromMsg('event') }}>
            {t('conversation.messageCard.createEvent')}
          </MenuItem>
          <MenuItem icon={<ListTodo />} onSelect={() => { more.close(); setFromMsg('task') }}>
            {t('conversation.messageCard.addToTasks')}
          </MenuItem>
          {/* ACT note.from_email @web.reader: a note with the subject, a link to the conversation and any
              selected text; or a checklist from the selected lines. */}
          <MenuItem icon={<StickyNote />} onSelect={() => { more.close(); void saveToNote('text') }}>
            {t('conversation.messageCard.saveToANote')}
          </MenuItem>
          <MenuItem icon={<ListChecks />} onSelect={() => { more.close(); void saveToNote('checklist') }}>
            {t('conversation.messageCard.makeAChecklistFromIt')}
          </MenuItem>
          <MenuSep />
          <MenuItem icon={<Filter />} onSelect={() => { more.close(); nav(`/settings/mail/filters?new=${encodeURIComponent(filterQueryForSender(m.from.email))}`) }}>
            {t('conversation.messageCard.filterMessagesLikeThis')}
          </MenuItem>
          <MenuItem icon={<Copy />} onSelect={() => { more.close(); void copyAddress(m.from) }}>
            {t('conversation.addressCopy.copySendersAddress')}
          </MenuItem>
          {!m.sent_by_me && (
            <MenuItem icon={<Crown />} onSelect={() => { more.close(); toggleVip() }}>
              {isVip ? t('conversation.messageCard.removeFromVipSenders') : t('conversation.messageCard.makeAVipSender', { displayName: displayName(m.from) })}
            </MenuItem>
          )}
          {/* ACT mail.translate @web.reader */}
          <MenuItem icon={<Languages />} onSelect={() => { more.close(); setShowTranslation(true); if (!translate.data) translate.mutate() }}>
            {t('conversation.messageCard.translateToEnglish')}
          </MenuItem>
          <MenuItem icon={<Printer />} onSelect={() => { more.close(); window.print() }}>
            {t('conversation.messageCard.print')}
          </MenuItem>
          <MenuItem icon={<Trash2 />} onSelect={() => { more.close(); void actOnMessage('trash') }}>
            {t('conversation.messageCard.deleteThisMessage')}
          </MenuItem>
          {!inSpam && (
            <MenuItem icon={<OctagonAlert />} onSelect={() => { more.close(); void actOnMessage('spam') }}>
              {t('common.reportSpam')}
            </MenuItem>
          )}
          <MenuItem icon={<Ban />} onSelect={() => { more.close(); setConfirmBlock(true) }} disabled={m.sent_by_me}>
            {t('conversation.messageCard.block3', { displayName: displayName(m.from) })}
          </MenuItem>
          <MenuSep />
          <MenuItem icon={<MailX />} onSelect={() => { more.close(); void actOnMessage('unread') }}>
            {t('common.markAsUnread')}
          </MenuItem>
          <MenuItem
            icon={<Split />}
            onSelect={() => {
              more.close()
              api
                .post<{ conversation_id: number }>(`/conversations/${m.conversation_id}/split`, { message_ids: [m.id] })
                .then((r) => {
                  void qc.invalidateQueries({ queryKey: qk.conversations })
                  void qc.invalidateQueries({ queryKey: qk.conversation(m.conversation_id) })
                  toast({ message: t('conversation.messageCard.messageMovedToANew'), actionLabel: r?.conversation_id ? t('common.open') : undefined, onAction: r?.conversation_id ? () => nav(mailPath('all', r.conversation_id)) : undefined })
                })
                .catch((e) => toastError(e, t('conversation.messageCard.couldNotSplitConversation')))
            }}
          >
            {t('conversation.messageCard.splitIntoNewConversation')}
          </MenuItem>
          <MenuItem icon={<FileCode2 />} onSelect={() => { more.close(); window.open(`${API}/messages/${m.id}/raw`, '_blank', 'noopener') }}>
            {t('conversation.messageCard.showOriginal')}
          </MenuItem>
        </Popover>

        {dmarcFail && (
          <div className="banner warn auth-warning">
            <ShieldAlert />
            <span>
              {t('conversation.messageCard.beCarefulWithThisMessage')}
            </span>
          </div>
        )}

        {hasInvite(m) && !m.sent_by_me && <ItemBoundary what="invite"><InviteCard m={m} /></ItemBoundary>}
        {m.event_change && !m.sent_by_me && <ItemBoundary what="invite"><EventChangeCard m={m} c={m.event_change} /></ItemBoundary>}
        {!m.sent_by_me && !m.draft && <RecordingMailCard m={m} />}

        <TranslateBar t={translate} showing={showTranslation} offer={!!m.offer_translate} onShow={setShowTranslation} />
        {showTranslation && translate.data?.status === 'ready'
          ? <TranslatedText r={translate.data} />
          : <MessageBody messageId={m.id} sender={m.from.email} spam={inSpam} highlight={highlight} />}

        <AttachedEmails m={m} spam={inSpam} openFirst={!(m.snippet ?? '').trim()} />
        {fileAttachments(m).length > 0 && <Attachments m={m} />}

        {isLast && !m.draft && !m.sent_by_me && <ReplySuggestions m={m} onPick={(text) => onReply('reply', m, text)} />}
        {isLast && !m.draft && (
          <div className="reply-bar no-print" style={{ paddingInlineStart: 0 }}>
            <button type="button" className="btn" onClick={() => onReply('reply', m)}>
              <Reply />{' '}{t('common.reply')}
            </button>
            {[...m.to, ...m.cc].length > 1 && (
              <button type="button" className="btn" onClick={() => onReply('reply_all', m)}>
                <ReplyAll />{' '}{t('common.replyAll')}
              </button>
            )}
            <button type="button" className="btn" onClick={() => onReply('forward', m)}>
              <Forward />{' '}{t('common.forward')}
            </button>
          </div>
        )}
      </div>

      <FromMessageDialog m={m} kind={fromMsg} onClose={() => setFromMsg(null)} />

      <ConfirmDialog
        open={confirmBlock}
        title={t('conversation.messageCard.block', { email: m.from.email })}
        body={t('conversation.messageCard.futureMessagesFromThisSender')}
        confirmLabel={t('conversation.messageCard.block2')}
        danger
        onClose={() => setConfirmBlock(false)}
        onConfirm={() => {
          setConfirmBlock(false)
          api
            .post(`/people/${encodeURIComponent(m.from.email)}/block`)
            .then(() => toast({ message: t('conversation.messageCard.blocked', { email: m.from.email }), actionLabel: t('common.undo'), onAction: () => void api.del(`/people/${encodeURIComponent(m.from.email)}/block`) }))
            .catch((e) => toastError(e, t('conversation.messageCard.couldNotBlockSender')))
        }}
      />
      <ConfirmDialog
        open={confirmUnsub}
        title={t('conversation.messageCard.unsubscribeFrom', { displayName: displayName(m.from) })}
        body={unsub?.kind === 'http' ? t('conversation.messageCard.theSendersUnsubscribePageOpens') : t('conversation.messageCard.anUnsubscribeEmailWillBe', { split: unsub?.url.replace(/^mailto:/i, '').split('?')[0] ?? '' })}
        confirmLabel={t('conversation.messageCard.unsubscribe')}
        onClose={() => setConfirmUnsub(false)}
        onConfirm={() => {
          setConfirmUnsub(false)
          if (!unsub) return
          if (unsub.kind === 'http') window.open(unsub.url, '_blank', 'noopener,noreferrer')
          else {
            const u = new URL(unsub.url)
            const to: Address = { email: decodeURIComponent(u.pathname) }
            const text = u.searchParams.get('body') ?? 'unsubscribe'
            openCompose({
              payload: { ...emptyPayload(), to: [to], subject: u.searchParams.get('subject') ?? 'unsubscribe', html: `<p>${escapeHtml(text)}</p>` },
            })
            toast({ message: t('conversation.messageCard.reviewAndSendTheUnsubscribe') })
          }
        }}
      />
    </article>
  )
}

/** The details popover's To, Cc, Bcc and Reply-To: each address with its copy button. */
function AddressList({ list, press }: { list: Address[]; press: (a: Address) => HTMLAttributes<HTMLElement> }) {
  return <>{list.map((a, i) => <Fragment key={i}>{i > 0 && ', '}<CopyableAddress addr={a} press={press(a)}>{addressLine(a)}</CopyableAddress></Fragment>)}</>
}

/** A sender name that shows another address, or mixes look-alike alphabets ("PаyPal"). */
function SenderWarning({ name, email, problem }: { name: string | null | undefined; email: string; problem?: string }) {
  const other = nameAddressMismatch(name, email)
  // A From line that is not one plain address (backend core/fromline.py): mailr matched it to nobody,
  // so it never counts as the user's or a trusted address.
  const text = problem === 'malformed' || problem === 'several'
    ? t('conversation.messageCard.fromLineMalformed')
    : other
      ? t('conversation.messageCard.theNameShowsButThis', { other, email })
      : mixedScript(name) ? t('conversation.messageCard.theNameMixesLettersFrom')
        : mixedScript(email) ? t('conversation.messageCard.theAddressMixesLetters') : null
  if (!text) return null
  return (
    <span className="sender-warning" role="note" data-testid="sender-warning">
      <TriangleAlert size={14} aria-hidden="true" /> {text}
    </span>
  )
}

/** The files a message lists: not inline images, not the attached emails shown as cards. */
function fileAttachments(m: Message): Attachment[] {
  return m.attachments.filter((a) => !a.inline && !a.email)
}

function Attachments({ m }: { m: Message }) {
  const list = fileAttachments(m)
  const total = list.reduce((s, a) => s + a.size, 0)
  const [open, setOpen] = useState<number | null>(null)
  const urlFor = (a: Attachment) => `${API}/messages/${m.id}/attachments/${a.part}`
  return (
    <div className="attachments">
      <div className="head">
        {tx('conversation.messageCard.attachments', { count: list.length, size: formatSize(total) }, { span: (c) => <span className="muted">{c}</span> })}
      </div>
      <div className="att-cards">
        {list.map((a, i) => {
          const url = urlFor(a)
          const isImg = a.type.startsWith('image/') && a.size < 8 * 1024 * 1024
          return (
            <div key={a.part} className="att-card" title={`${a.filename} (${formatSize(a.size)})`}
              onClick={(e) => { if (!(e.target as HTMLElement).closest('a,button')) setOpen(i) }}>
              <div className="preview">{isImg ? <img src={url} alt={a.filename} loading="lazy" /> : attIcon(a)}</div>
              <div className="foot">
                {attIcon(a)}
                {/* The extension stays visible however long the name is. */}
                <bdi className="name" data-testid="att-name"><span className="base">{splitExt(a.filename)[0]}</span><span className="ext">{splitExt(a.filename)[1]}</span></bdi>
              </div>
              {riskyAttachment(a.filename) && (
                <div className="att-warning" role="note">{t('conversation.messageCard.programOrScriptFileOpen')}</div>
              )}
              <div className="overlay">
                <button className="icon-btn" onClick={() => setOpen(i)} aria-label={t('conversation.messageCard.preview', { filename: a.filename })} data-tip={t('common.preview')}>
                  <Eye />
                </button>
                <a className="icon-btn" href={`${url}?download=1`} download={a.filename} aria-label={t('conversation.messageCard.download', { filename: a.filename })} data-tip={t('conversation.messageCard.download2')}>
                  <Download />
                </a>
                <TranscribeButton m={m} a={a} />
              </div>
            </div>
          )
        })}
      </div>
      {open !== null && list[open] && (
        <AttachmentViewer items={list} index={open} urlFor={urlFor} onIndex={setOpen} onClose={() => setOpen(null)} />
      )}
    </div>
  )
}

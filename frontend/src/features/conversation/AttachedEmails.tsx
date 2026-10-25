// ACT mail.open_attached_email @web.reader
// Emails attached to a message (message/rfc822: "Forward as attachment", digests, a bounce's
// original), one card each. Closed, a card shows who sent
// the attached email, its subject, date and first words; open, its own body, sanitised like any
// message body (MessageBody with attachedPart: the same remote-image rules) and its files. A
// message whose own body is empty opens its first card. An attached email the server could not
// read has no summary and stays in the file list as a plain .eml file.
// Its From line is only what the attached email says, which nobody verified: whether its images
// load by default follows the OUTER message's sender and spam state, and it offers no "Always
// display images from". Its files carry the same kind icon and
// risky-file note as the reader's own file list.

import { ChevronDown, ChevronRight, Download, Mail } from 'lucide-react'
import { useId, useState } from 'react'
import { API } from '../../api/client'
import { useAttachedEmail } from '../../api/hooks'
import type { AttachedEmailSummary, Attachment, Message } from '../../api/types'
import { BidiList } from '../../components/bits'
import { addressLine, formatFullDate, formatSize } from '../../lib/format'
import { riskyAttachment } from '../../lib/safeText'
import { t } from '../../i18n'
import { MessageBody } from './MessageBody'
import { attIcon, splitExt } from './attachmentBits'

const MAX_DEPTH = 3 // nested forwards shown as cards; deeper ones download as .eml files

/** The attachments of a message that are attached emails with a summary (shown as cards). */
export function attachedEmails(m: Pick<Message, 'attachments'>): Attachment[] {
  return m.attachments.filter((a) => !!a.email)
}

/** "Attached email: <subject> — <sender>". */
export function attachedEmailLabel(e: AttachedEmailSummary): string {
  return t('conversation.attachedEmail.label', {
    subject: e.subject || t('conversation.attachedEmail.noSubject'),
    from: e.from_name || e.from_email || t('conversation.attachedEmail.unknownSender'),
  })
}

export function AttachedEmails({ m, spam, openFirst }: { m: Message; spam: boolean; openFirst: boolean }) {
  const list = attachedEmails(m)
  if (!list.length) return null
  return (
    <div className="attached-emails" data-testid="attached-emails">
      {list.map((a, i) => (
        <AttachedEmailCard key={a.part} messageId={m.id} part={a.part} filename={a.filename} size={a.size}
          summary={a.email!} spam={spam} outerSender={m.from.email} defaultOpen={openFirst && i === 0} depth={0} />
      ))}
    </div>
  )
}

function AttachedEmailCard({ messageId, part, filename, size, summary, spam, outerSender, defaultOpen, depth }: {
  messageId: number
  part: number
  filename: string
  size: number
  summary: AttachedEmailSummary
  spam: boolean
  /** The sender of the message this email is attached to: its trust decides the images. */
  outerSender: string
  defaultOpen: boolean
  depth: number
}) {
  const [open, setOpen] = useState(defaultOpen)
  const bodyId = useId()
  const label = attachedEmailLabel(summary)
  return (
    <section className={`attached-email${open ? ' open' : ''}`} data-testid="attached-email" aria-label={label}>
      <div className="ae-head">
        <button type="button" className="ae-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
          <Mail size={16} aria-hidden="true" className="ae-icon" />
          <span className="ae-title"><bdi>{label}</bdi></span>
          {summary.date && <span className="ae-date muted">{formatFullDate(summary.date)}</span>}
        </button>
        <a className="icon-btn" href={`${API}/messages/${messageId}/attachments/${part}?download=1`} download={filename}
          aria-label={t('conversation.attachedEmail.download', { filename })}
          data-tip={t('conversation.attachedEmail.downloadTip', { size: formatSize(size) })}>
          <Download size={16} />
        </a>
      </div>
      {!open && summary.excerpt && <p className="ae-excerpt muted"><bdi>{summary.excerpt}</bdi></p>}
      {open && (
        <div id={bodyId} className="ae-body">
          <AttachedEmailHeader messageId={messageId} part={part} />
          <MessageBody messageId={messageId} sender={outerSender} spam={spam} attachedPart={part} />
          <AttachedEmailFiles messageId={messageId} part={part} spam={spam} outerSender={outerSender} depth={depth} />
        </div>
      )}
    </section>
  )
}

/** From and To of the open attached email (the summary has only the sender). */
function AttachedEmailHeader({ messageId, part }: { messageId: number; part: number }) {
  const q = useAttachedEmail(messageId, part, false)
  const e = q.data
  // The space is held while it loads, so the body below does not move.
  return (
    <div className="ae-meta" aria-busy={!e}>
      {e && (
        <>
          <div><span className="muted">{t('conversation.attachedEmail.from')}</span> <bdi>{addressLine(e.from)}</bdi></div>
          {e.to.length > 0 && (
            <div><span className="muted">{t('conversation.attachedEmail.to')}</span> <BidiList items={e.to.map(addressLine)} /></div>
          )}
        </>
      )}
    </div>
  )
}

/** The attached email's own files, each downloadable from the outer message; an email attached
 *  to it is a card of its own (up to MAX_DEPTH levels). */
function AttachedEmailFiles({ messageId, part, spam, outerSender, depth }: { messageId: number; part: number; spam: boolean; outerSender: string; depth: number }) {
  const q = useAttachedEmail(messageId, part, false)
  const files = (q.data?.attachments ?? []).filter((f) => !f.inline)
  if (!files.length) return null
  return (
    <div className="ae-files">
      {files.map((f, i) => {
        if (f.email && f.part !== null && depth + 1 < MAX_DEPTH) {
          return (
            <AttachedEmailCard key={`e${i}`} messageId={messageId} part={f.part} filename={f.filename} size={f.size}
              summary={f.email} spam={spam} outerSender={outerSender} defaultOpen={false} depth={depth + 1} />
          )
        }
        const [base, ext] = splitExt(f.filename)
        const risky = riskyAttachment(f.filename)
        const name = (
          <>
            {attIcon(f, 14)} <bdi className="name" data-testid="att-name"><span className="base">{base}</span><span className="ext">{ext}</span></bdi>{' '}
            <span className="muted">{formatSize(f.size)}</span>
          </>
        )
        const chip = f.part !== null ? (
          <a className={`att-chip${risky ? ' risky' : ''}`} href={`${API}/messages/${messageId}/attachments/${f.part}?download=1`} download={f.filename}
            aria-label={t('conversation.attachedEmail.download', { filename: f.filename })}>
            {name}
          </a>
        ) : (
          <span className="att-chip" title={t('conversation.attachedEmail.notOnItsOwn')}>{name}</span>
        )
        return (
          <span key={i} className="ae-file">
            {chip}
            {risky && <span className="att-warning" role="note">{t('conversation.messageCard.programOrScriptFileOpen')}</span>}
          </span>
        )
      })}
    </div>
  )
}

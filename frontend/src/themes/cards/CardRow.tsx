// ACT mail.archive @web.list, ACT mail.move @web.list (Move to Inbox), ACT mail.star @web.list, ACT mail.delete @web.list, ACT mail.untrash @web.list (Restore)
import { AlarmClock, Archive, ArchiveRestore, Check, ChevronDown, ChevronUp, Paperclip, Star, Trash2 } from 'lucide-react'
import { memo, useRef, type MouseEvent } from 'react'
import { Avatar } from '../../components/bits'
import { formatDateTime, formatListDate } from '../../lib/format'
import { useListPrefs } from '../listPrefs'
import type { ListGroup, ListRowProps } from '../types'
import { ActionChip, factFor } from '../../features/mail/ActionChip'
import { RowAttachments, RowLabelChips, RowMarker, RowMuted, RowSignals, rowAttachments, rowLabels, useRowA11y } from '../../features/mail/RowParts'
import { rowSender, rowSnippet, rowAccessibleName } from '../../features/mail/rowText'
import { t } from '../../i18n'
import { baseView } from '../../lib/views'
import { useArchiveSlot } from '../../lib/rowArchive'
import { RowAccounts } from '../../features/layout/accounts'

/** A conversation as a rounded card: big avatar (tap it to select), sender, subject, two lines of preview. */
function CardRowImpl({ c, labelsById, selected, focused, tabStop, rowIndex, view, markers, showChips, hideLabelId, draggable, onOpen, onToggleSelect, onAction, onSnooze }: ListRowProps) {
  const unread = c.unread_count > 0
  const a11y = useRowA11y({ focused, selected, tabStop, rowIndex, onOpen: () => onOpen(c), name: rowAccessibleName(c, view) })
  const snoozeRef = useRef<HTMLButtonElement>(null)
  const other = [...c.participants].reverse().find((p) => !p.me) ?? c.participants[0]
  const sender = rowSender(c).replace(/ \d+$/, '')
  const snippet = rowSnippet(c.subject, c.snippet)
  const chips = rowLabels(c, labelsById, hideLabelId)
  const atts = rowAttachments(c, showChips, 2)
  const stop = (e: MouseEvent) => e.stopPropagation()
  const inTrash = baseView(view) === 'trash'
  // Archive or Move to Inbox by the row's own state (lib/rowArchive.ts); Trash and Spam keep theirs.
  const slot = useArchiveSlot([c], view, labelsById)
  const inInbox = slot === 'archive' || (slot === 'keep' && c.label_ids.some((id) => labelsById.get(id)?.role === 'inbox'))

  return (
    <div className="c-slot">
    <div
      className={`c-card${unread ? ' unread' : ''}${selected ? ' selected' : ''}${focused ? ' focused' : ''}`}
      {...a11y}
      data-id={c.id}
      draggable={draggable || undefined}
      onClick={() => onOpen(c)}
    >
      <span role="gridcell" className="c-cell">
      <button type="button" className="c-avatar" role="checkbox" aria-label={t('common.select')} aria-checked={selected} onClick={(e) => { stop(e); onToggleSelect(c.id, e.shiftKey) }}>
        {selected ? (
          <span className="c-check">
            <Check />
          </span>
        ) : (
          <Avatar who={other} />
        )}
      </button>
      </span>
      <div className="c-body" role="gridcell">
        <div className="c-head">
          <span className="c-sender" dir="auto" title={c.participants.map((p) => p.email).join(', ')}>
            {sender}
            {c.message_count > 1 && <span className="c-n">{c.message_count}</span>}
            {c.draft_count > 0 && <span className={`c-draft${sender ? "" : " solo"}`}>{t('common.draft')}</span>}
          </span>
          <span className="c-date" title={formatDateTime(c.last_date)}>
            {baseView(view) === 'snoozed' && c.snoozed_until ? formatListDate(c.snoozed_until) : formatListDate(c.last_date)}
          </span>
        </div>
        <div className="c-subject">
          {markers && (
            <span className="c-marker" onClick={stop}>
              <RowMarker c={c} markers={markers} onAction={onAction} />
            </span>
          )}
          <span className="c-subject-text" dir="auto">{c.subject || t('common.noSubject')}</span>
          <RowSignals c={c} />
          <RowAccounts c={c} />
          <RowMuted c={c} />
          {c.starred && <Star className="c-star" aria-label={t('common.starred')} />}
        </div>
        {snippet && <div className="c-snippet" dir="auto">{snippet}</div>}
        <ActionChip action={c.primary_action} withFact={factFor(c)} />
        {(chips.length > 0 || atts.length > 0 || (c.has_attachments && !atts.length)) && (
          <div className="c-chips">
            <RowLabelChips labels={chips} />
            <RowAttachments atts={atts} className="c-att" />
            {c.has_attachments && !atts.length && <Paperclip className="c-clip" aria-label={t('common.hasAttachment')} />}
          </div>
        )}
      </div>
      <div className="c-actions" role="gridcell" onClick={stop}>
        {inTrash ? (
          <button type="button" className="icon-btn sm" aria-label={t('common.restore')} data-tip={t('common.restore')} onClick={() => onAction(c, 'untrash')}>
            <ArchiveRestore />
          </button>
        ) : inInbox ? (
          <button type="button" className="icon-btn sm" aria-label={t('common.archive')} data-tip={t('common.archive')} onClick={() => onAction(c, 'archive')}>
            <Archive />
          </button>
        ) : (
          <button type="button" className="icon-btn sm" aria-label={t('common.moveToInbox')} data-tip={t('common.moveToInbox')} onClick={() => onAction(c, 'inbox')}>
            <ArchiveRestore />
          </button>
        )}
        <button ref={snoozeRef} type="button" className="icon-btn sm" aria-label={t('common.snooze')} data-tip={t('common.snooze')} onClick={() => snoozeRef.current && onSnooze(c, snoozeRef.current)}>
          <AlarmClock />
        </button>
        <button type="button" className={`icon-btn sm${c.starred ? ' on' : ''}`} aria-label={c.starred ? t('common.unstar') : t('common.star')} data-tip={c.starred ? t('common.unstar') : t('common.star')} onClick={() => onAction(c, c.starred ? 'unstar' : 'star')}>
          <Star />
        </button>
        {!inTrash && (
          <button type="button" className="icon-btn sm" aria-label={t('common.delete2')} data-tip={t('common.delete2')} onClick={() => onAction(c, 'trash')}>
            <Trash2 />
          </button>
        )}
      </div>
    </div>
    </div>
  )
}

export const CardRow = memo(CardRowImpl)

/** People get a plain heading; notifications and newsletters fold into a bundle card. */
export function BundleHead({ group }: { group: ListGroup; view: string }) {
  const toggle = useListPrefs((s) => s.toggleCollapsed)
  const kind = group.id.split(':').pop() ?? ''
  if (!group.collapsible) {
    return (
      <div className="c-slot">
        <div className="c-group">
          <span>{group.title}</span>
          <span className="c-group-n">{group.count}</span>
        </div>
      </div>
    )
  }
  const senders = Array.from(new Set((group.sample ?? []).map((c) => (c.participants.find((p) => !p.me) ?? c.participants[0])?.name || '').filter(Boolean)))
  if (group.collapsed) {
    return (
      <div className="c-slot">
      <button type="button" className={`c-bundle c-bundle-${kind}`} aria-expanded={false} onClick={() => toggle(group.id, true)}>
        <span className="c-stack" aria-hidden>
          {(group.sample ?? []).slice(0, 3).map((c) => (
            <Avatar key={c.id} who={c.participants.find((p) => !p.me) ?? c.participants[0]} size="sm" />
          ))}
        </span>
        <span className="c-bundle-text">
          <span className="c-bundle-title">
            {group.title}
            {group.unread > 0 && <span className="c-badge">{t('themes.cards.cardRow.new', { unread: group.unread })}</span>}
          </span>
          <span className="c-bundle-sub">{senders.join(', ')}</span>
        </span>
        <span className="c-bundle-n">{group.count}</span>
        <ChevronDown className="c-bundle-chev" />
      </button>
      </div>
    )
  }
  return (
    <div className="c-slot">
      <div className="c-group">
        <span>{group.title}</span>
        <span className="c-group-n">{group.count}</span>
        <button type="button" className="c-fold" aria-expanded onClick={() => toggle(group.id, true)}>
          <ChevronUp />{' '}{t('themes.cards.cardRow.fold')}
        </button>
      </div>
    </div>
  )
}

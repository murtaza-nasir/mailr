// ACT mail.archive @web.list, ACT mail.move @web.list (Move to Inbox), ACT mail.star @web.list, ACT mail.delete @web.list, ACT mail.untrash @web.list (Restore)
import { AlarmClock, Archive, ArchiveRestore, Paperclip, Star, Trash2 } from 'lucide-react'
import { memo, useRef, type MouseEvent } from 'react'
import { rowSender, rowAccessibleName } from '../../features/mail/rowText'
import { formatDateTime, formatListDate } from '../../lib/format'
import type { ListRowProps } from '../types'
import { ActionChip } from '../../features/mail/ActionChip'
import { RowLabelChips, RowMarker, RowMuted, RowSignals, rowLabels, RowCheck, useRowA11y } from '../../features/mail/RowParts'
import { t } from '../../i18n'
import { baseView } from '../../lib/views'
import { useArchiveSlot } from '../../lib/rowArchive'
import { RowAccounts } from '../../features/layout/accounts'

/** One quiet line per conversation: who, what, when. Actions and their keys on hover. */
function FocusRowImpl({ c, labelsById, selected, focused, tabStop, rowIndex, view, narrow, markers, hideLabelId, draggable, onOpen, onToggleSelect, onAction, onSnooze }: ListRowProps) {
  const unread = c.unread_count > 0
  const a11y = useRowA11y({ focused, selected, tabStop, rowIndex, onOpen: () => onOpen(c), name: rowAccessibleName(c, view) })
  const snoozeRef = useRef<HTMLButtonElement>(null)
  const sender = rowSender(c).replace(/ \d+$/, '')
  const stop = (e: MouseEvent) => e.stopPropagation()
  // Focus keeps one quiet line: one label chip at most, with "+n" for the rest.
  const chips = rowLabels(c, labelsById, hideLabelId)
  const inTrash = baseView(view) === 'trash'
  // Archive or Move to Inbox by the row's own state (lib/rowArchive.ts); Trash and Spam keep theirs.
  const slot = useArchiveSlot([c], view, labelsById)
  const archived = slot === 'inbox' || (slot === 'keep' && !c.label_ids.some((id) => labelsById.get(id)?.role === 'inbox') && baseView(view) !== 'inbox')
  const date = baseView(view) === 'snoozed' && c.snoozed_until ? formatListDate(c.snoozed_until) : formatListDate(c.last_date)

  return (
    <div
      className={`f-row${unread ? ' unread' : ''}${selected ? ' selected' : ''}${focused ? ' focused' : ''}${narrow ? ' narrow' : ''}`}
      {...a11y}
      data-id={c.id}
      draggable={draggable || undefined}
      onClick={() => onOpen(c)}
    >
      <span className="f-gutter" role="gridcell" onClick={stop}>
        <span className="f-dot" aria-hidden />
        <RowCheck selected={selected} className="f-check" tip={t('themes.focus.focusRow.selectX')} onToggle={(shift) => onToggleSelect(c.id, shift)} />
      </span>
      {markers && (
        <span className="f-marker" role="gridcell" onClick={stop}>
          <RowMarker c={c} markers={markers} onAction={onAction} />
        </span>
      )}
      <span className="f-sender" dir="auto" role="gridcell" title={c.participants.map((p) => p.email).join(', ')}>
        {sender}
        {c.message_count > 1 && <span className="f-n">{c.message_count}</span>}
        {c.draft_count > 0 && <span className="f-draft">{' '}{t('common.draft')}</span>}
      </span>
      <span className="f-text" role="gridcell">
        <RowLabelChips labels={chips} max={1} />
        <span className="f-subject" dir="auto">{c.subject || t('common.noSubject')}</span>
        {c.snippet && <span className="f-snippet" dir="auto">{c.snippet}</span>}
        <RowSignals c={c} />
        <ActionChip action={c.primary_action} withFact={false} />
      </span>
      <span className="f-meta" role="gridcell">
        <RowAccounts c={c} />
        <RowMuted c={c} />
        {c.has_attachments && <Paperclip aria-label={t('common.hasAttachment')} />}
        {c.starred && <Star className="f-star" aria-label={t('common.starred')} />}
        <span className="f-date" title={formatDateTime(c.last_date)}>
          {date}
        </span>
      </span>
      <span className="f-actions" role="gridcell" onClick={stop}>
        {inTrash ? (
          <button type="button" className="icon-btn sm" aria-label={t('common.restore')} data-tip={t('common.restore')} onClick={() => onAction(c, 'untrash')}>
            <ArchiveRestore />
          </button>
        ) : archived ? (
          <button type="button" className="icon-btn sm" aria-label={t('common.moveToInbox')} data-tip={t('common.moveToInbox')} onClick={() => onAction(c, 'inbox')}>
            <ArchiveRestore />
          </button>
        ) : (
          <button type="button" className="icon-btn sm" aria-label={t('common.archive')} data-tip={t('themes.focus.focusRow.archiveE')} onClick={() => onAction(c, 'archive')}>
            <Archive />
          </button>
        )}
        <button type="button" className={`icon-btn sm${c.starred ? ' on' : ''}`} aria-label={c.starred ? t('common.unstar') : t('common.star')} data-tip={`${c.starred ? t('common.unstar') : t('common.star')} · S`} onClick={() => onAction(c, c.starred ? 'unstar' : 'star')}>
          <Star />
        </button>
        <button ref={snoozeRef} type="button" className="icon-btn sm" aria-label={t('common.snooze')} data-tip={t('themes.focus.focusRow.snoozeB')} onClick={() => snoozeRef.current && onSnooze(c, snoozeRef.current)}>
          <AlarmClock />
        </button>
        {!inTrash && (
          <button type="button" className="icon-btn sm" aria-label={t('common.delete2')} data-tip={t('themes.focus.focusRow.delete')} onClick={() => onAction(c, 'trash')}>
            <Trash2 />
          </button>
        )}
      </span>
    </div>
  )
}

export const FocusRow = memo(FocusRowImpl)

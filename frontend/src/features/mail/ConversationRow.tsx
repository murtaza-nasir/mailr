// ACT mail.important @web.list, ACT mail.move @web.list (Move to Inbox), ACT mail.untrash @web.list (Restore in Trash)
// ACT mail.archive @web.list, ACT mail.delete @web.list, ACT mail.read @web.list, ACT mail.unread @web.list, ACT mail.star @web.list, ACT mail.snooze @web.list (row buttons),
// ACT mail.primary_action @web.list (the chip), ACT recording.search @web.list (the ▶ chip of a conversation linked to a recording)
import { ActionChip, factFor } from './ActionChip'
import { Paperclip, Star } from 'lucide-react'
import { memo, useEffect, useState, type MouseEvent } from 'react'
import type { ConversationSummary } from '../../api/types'
import type { ListRowProps } from '../../themes/types'
import { Avatar } from '../../components/bits'
import { formatListDate, formatDateTime } from '../../lib/format'
import { rowSender, rowSnippet, rowAccessibleName } from './rowText'
import { RowAttachments, RowLabelChips, RowMarker, RowMuted, RowQuickActions, RowSignals, rowAttachments, rowLabels, RowCheck, useRowA11y } from './RowParts'
import { t as tr } from '../../i18n'
import { baseView } from '../../lib/views'
import { RowAccounts } from '../layout/accounts'

export interface RowHandlers {
  onOpen: (c: ConversationSummary) => void
  onToggleSelect: (id: number, shift: boolean) => void
  onAction: (c: ConversationSummary, action: 'archive' | 'inbox' | 'trash' | 'read' | 'unread' | 'star' | 'unstar' | 'important' | 'not_important' | 'untrash') => void
  onSnooze: (c: ConversationSummary, anchor: HTMLElement) => void
  onContextMenu?: (c: ConversationSummary, e: MouseEvent) => void
}

type RowProps = ListRowProps

function RowImpl({ c, labelsById, selected, focused, tabStop, rowIndex, view, markers, showChips, narrow, hideLabelId, draggable, onOpen, onToggleSelect, onAction, onSnooze, onContextMenu }: RowProps) {
  const unread = c.unread_count > 0
  const a11y = useRowA11y({ focused, selected, tabStop, rowIndex, onOpen: () => onOpen(c), name: rowAccessibleName(c, view) })
  const chips = rowLabels(c, labelsById, hideLabelId)
  const atts = rowAttachments(c, showChips, 3)
  // The main action goes on the chip line under the subject (as in Gmail), clear of the hover buttons.
  const chipLine = showChips && !!c.primary_action
  const sender = rowSender(c)
  const snippet = rowSnippet(c.subject, c.snippet)
  const date = baseView(view) === 'snoozed' && c.snoozed_until ? formatListDate(c.snoozed_until) : formatListDate(c.last_date)

  const stop = (e: MouseEvent) => e.stopPropagation()

  const senderCell = (
    <div className="sender" dir="auto" role={narrow ? undefined : 'gridcell'} title={c.participants.map((p) => p.email).join(', ')}>
      {sender.replace(/ \d+$/, '')}
      {c.message_count > 1 && <span className="count">{c.message_count}</span>}
      {c.draft_count > 0 && (
        <span className="draft">
          {tr('mail.conversationRow.draft', { value: sender ? ', ' : '', value2: c.draft_count > 1 ? ` (${c.draft_count})` : '' })}
        </span>
      )}
    </div>
  )

  const tail = (
    <div className="tail" role={narrow ? undefined : 'gridcell'}>
      <RowAccounts c={c} />
      <RowMuted c={c} />
      {c.has_attachments && !atts.length && <Paperclip className="clip" aria-label={tr('common.hasAttachment')} />}
      {baseView(view) === 'snoozed' && c.snoozed_until ? (
        <span className="date snoozed" title={formatDateTime(c.snoozed_until)}>
          {date}
        </span>
      ) : (
        <span className="date" title={formatDateTime(c.last_date)}>
          {date}
        </span>
      )}
    </div>
  )

  return (
    <div
      className={`conv-row${unread ? ' unread' : ''}${selected ? ' selected' : ''}${focused ? ' focused' : ''}${(atts.length || chipLine) && !narrow ? ' with-chips' : ''}${c.primary_action ? ' has-action' : ''}`}
      {...a11y}
      data-id={c.id}
      draggable={draggable || undefined}
      onClick={() => onOpen(c)}
      onContextMenu={onContextMenu ? (e) => onContextMenu(c, e) : undefined}
    >
      <div className="lead" role="gridcell" onClick={stop}>
        <RowCheck selected={selected} className="icon-btn" onToggle={(shift) => onToggleSelect(c.id, shift)} />
        <button
          type="button"
          className={`icon-btn${c.starred ? ' star-on' : ''}`}
          aria-label={tr('common.star')}
          aria-pressed={c.starred}
          data-tip={c.starred ? tr('common.starred') : tr('mail.conversationRow.notStarred')}
          onClick={() => onAction(c, c.starred ? 'unstar' : 'star')}
        >
          <Star />
        </button>
        <RowMarker c={c} markers={markers} onAction={onAction} />
      </div>

      {narrow ? (
        <>
        <Avatar who={[...c.participants].reverse().find((p) => !p.me) ?? c.participants[0]} size="sm" />
        <div className="content" role="gridcell">
          <div className="top">
            {senderCell}
            {tail}
          </div>
          <div className="line">
            <span className="subject" dir="auto">{c.subject || tr('common.noSubject')}</span>
            <RowSignals c={c} />
          </div>
          {/* No snippet, chips or action: no empty third line. */}
          {(chips.length > 0 || snippet || c.primary_action) && (
            <div className="line">
              <RowLabelChips labels={chips} />
              {snippet && <span className="snippet" dir="auto">{snippet}</span>}
              <ActionChip action={c.primary_action} withFact={factFor(c)} />
            </div>
          )}
        </div>
        </>
      ) : (
        <>
          {senderCell}
          <div className="content" role="gridcell">
            <div className="line">
              <RowLabelChips labels={chips} />
              <span className="subject" dir="auto">{c.subject || tr('common.noSubject')}</span>
              <RowSignals c={c} />
              {snippet && <span className="snippet-sep" aria-hidden="true">&nbsp;-&nbsp;</span>}
              {snippet && <span className="snippet" dir="auto">{snippet}</span>}
              {!chipLine && <ActionChip action={c.primary_action} withFact={false} />}
            </div>
            {(atts.length > 0 || chipLine) && (
              <div className="att-chips">
                {chipLine && <ActionChip action={c.primary_action} withFact={factFor(c)} />}
                <RowAttachments atts={atts} />
              </div>
            )}
          </div>
          {tail}
        </>
      )}

      <div className="hover-actions" role="gridcell" onClick={stop}>
        <RowQuickActions c={c} view={view} labelsById={labelsById} onAction={onAction} onSnooze={onSnooze} />
      </div>
    </div>
  )
}

export const ConversationRow = memo(RowImpl)

/** Rows drawn while a list loads. With `delay`, nothing is drawn for that long: a list that
 *  answers quickly (a tab flick, a cached page) appears without a skeleton flashing first. */
export function RowSkeleton({ count = 8, delay = 0 }: { count?: number; delay?: number }) {
  const [shown, setShown] = useState(delay <= 0)
  useEffect(() => {
    if (delay <= 0) return
    const t = setTimeout(() => setShown(true), delay)
    return () => clearTimeout(t)
  }, [delay])
  if (!shown) return <div aria-busy="true" aria-label={tr('mail.conversationRow.loadingConversations')} />
  return (
    <div aria-busy="true" aria-label={tr('mail.conversationRow.loadingConversations')}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="row-skeleton">
          <span className="skeleton" style={{ width: 18, height: 18 }} />
          <span className="skeleton" style={{ width: 18, height: 18 }} />
          <span className="skeleton" style={{ width: 140, height: 12 }} />
          <span className="skeleton" style={{ flex: 1, height: 12, maxWidth: `${50 + ((i * 37) % 40)}%` }} />
          <span className="skeleton" style={{ width: 44, height: 12 }} />
        </div>
      ))}
    </div>
  )
}

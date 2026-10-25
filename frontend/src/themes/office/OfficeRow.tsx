// ACT mail.read @web.list, ACT mail.unread @web.list, ACT mail.star @web.list (Flag), ACT mail.delete @web.list, ACT mail.untrash @web.list (Restore)
import { Flag, MailOpen, Mail, Paperclip, Trash2, ArchiveRestore, Pin } from 'lucide-react'
import { memo, type MouseEvent } from 'react'
import { appNavigate } from '../../lib/appNav'
import type { ConversationSummary } from '../../api/types'
import { Avatar } from '../../components/bits'
import { rowSender, rowAccessibleName } from '../../features/mail/rowText'
import { formatDateTime, formatListDate } from '../../lib/format'
import { mailPath, baseView } from '../../lib/views'
import { useUI } from '../../stores/ui'
import type { ListGroup, ListRowProps } from '../types'
import { useListPrefs } from '../listPrefs'
import { useOfficeTabs } from './tabs'
import { ActionChip } from '../../features/mail/ActionChip'
import { RowLabelChips, RowMarker, RowMuted, RowSignals, rowLabels, RowCheck, useRowA11y } from '../../features/mail/RowParts'
import { t } from '../../i18n'
import { RowAccounts } from '../../features/layout/accounts'

/** Open a conversation in its own tab (Ctrl/⌘-click, middle-click, double-click). The search is
 *  read from the address when it happens: router hooks in every row re-rendered all of them on
 *  each navigation. */
export function openInTab(c: ConversationSummary, view: string) {
  const q = new URLSearchParams(window.location.search).get('q') ?? ''
  useOfficeTabs.getState().pin({ id: c.id, title: c.subject || t('common.noSubject'), view, q })
  useOfficeTabs.getState().setActiveCompose(null)
  useUI.getState().setFocused(c.id)
  appNavigate(mailPath(view, c.id, q || undefined))
}

/** Two-line message item: sender, subject with time, one line of preview. */
function OfficeRowImpl({ c, labelsById, selected, focused, tabStop, rowIndex, view, markers, onOpen, onToggleSelect, onAction, hideLabelId, draggable }: ListRowProps) {
  const unread = c.unread_count > 0
  const a11y = useRowA11y({ focused, selected, tabStop, rowIndex, onOpen: () => onOpen(c), name: rowAccessibleName(c, view) })
  const other = [...c.participants].reverse().find((p) => !p.me) ?? c.participants[0]
  const sender = rowSender(c).replace(/ \d+$/, '')
  const draftOnly = c.draft_count > 0 && c.message_count === 0
  const inTrash = baseView(view) === 'trash'
  const chips = rowLabels(c, labelsById, hideLabelId)
  const stop = (e: MouseEvent) => e.stopPropagation()
  const newTab = () => (draftOnly ? onOpen(c) : openInTab(c, view))

  return (
    <div
      className={`o-row${unread ? ' unread' : ''}${selected ? ' selected' : ''}${focused ? ' focused' : ''}`}
      {...a11y}
      data-id={c.id}
      draggable={draggable || undefined}
      onClick={(e) => (e.ctrlKey || e.metaKey ? newTab() : onOpen(c))}
      onMouseDown={(e) => {
        if (e.button === 1) e.preventDefault() // no autoscroll on middle-click
      }}
      onAuxClick={(e) => {
        if (e.button !== 1) return
        e.preventDefault()
        newTab()
      }}
      onDoubleClick={newTab}
    >
      <div className="o-lead" role="gridcell" onClick={stop} onDoubleClick={stop}>
        <span className="o-avatar">
          <Avatar who={other} size="sm" />
        </span>
        <RowCheck selected={selected} className="o-check" onToggle={(shift) => onToggleSelect(c.id, shift)} />
      </div>
      <div className="o-body" role="gridcell">
        <div className="o-line">
          <span className="o-sender" dir="auto" title={c.participants.map((p) => p.email).join(', ')}>
            {sender}
            {c.draft_count > 0 && <span className="o-draft">{t('themes.office.officeRow.draft', { value: sender ? ' ' : '' })}</span>}
          </span>
          <span className="o-icons">
            <RowAccounts c={c} />
            <RowMuted c={c} />
            {c.has_attachments && <Paperclip aria-label={t('common.hasAttachment')} />}
            {c.starred && <Flag className="o-flag" aria-label={t('themes.office.officeRow.flagged')} />}
          </span>
          <span className="o-hover" onClick={stop} onDoubleClick={stop}>
            {inTrash ? (
              <button type="button" className="icon-btn xs" aria-label={t('common.restore')} data-tip={t('common.restore')} onClick={() => onAction(c, 'untrash')}>
                <ArchiveRestore />
              </button>
            ) : (
              <button type="button" className="icon-btn xs" aria-label={t('common.delete2')} data-tip={t('common.delete2')} onClick={() => onAction(c, 'trash')}>
                <Trash2 />
              </button>
            )}
            <button type="button" className="icon-btn xs" aria-label={unread ? t('common.markAsRead') : t('common.markAsUnread')} data-tip={unread ? t('common.markAsRead') : t('common.markAsUnread')} onClick={() => onAction(c, unread ? 'read' : 'unread')}>
              {unread ? <MailOpen /> : <Mail />}
            </button>
            <button type="button" className={`icon-btn xs${c.starred ? ' o-flag-on' : ''}`} aria-label={c.starred ? t('themes.office.officeRow.unflag') : t('common.flag')} data-tip={c.starred ? t('themes.office.officeRow.unflag') : t('common.flag')} onClick={() => onAction(c, c.starred ? 'unstar' : 'star')}>
              <Flag />
            </button>
            {!draftOnly && (
              <button type="button" className="icon-btn xs" aria-label={t('themes.office.officeRow.openInNewTab')} data-tip={t('themes.office.officeRow.openInNewTab')} onClick={() => openInTab(c, view)}>
                <Pin />
              </button>
            )}
          </span>
        </div>
        <div className="o-line">
          <span className="o-marker" onClick={stop} onDoubleClick={stop}>
            <RowMarker c={c} markers={markers} onAction={onAction} />
          </span>
          <span className="o-subject" dir="auto">{c.subject || t('common.noSubject')}</span>
          {c.message_count > 1 && <span className="o-count">{c.message_count}</span>}
          <RowSignals c={c} />
          <span className="o-date" title={formatDateTime(c.last_date)}>
            {baseView(view) === 'snoozed' && c.snoozed_until ? formatListDate(c.snoozed_until) : formatListDate(c.last_date)}
          </span>
        </div>
        <div className="o-line o-preview">
          <RowLabelChips labels={chips} max={2} />
          <span className="o-snippet" dir="auto">{c.snippet || t('themes.office.officeRow.noPreviewIsAvailable')}</span>
          <ActionChip action={c.primary_action} withFact={false} />
        </div>
      </div>
    </div>
  )
}

export const OfficeRow = memo(OfficeRowImpl)

/** A collapsible date heading ("Today", "Yesterday", "Last week"). */
export function OfficeGroupHead({ group }: { group: ListGroup; view: string }) {
  const toggle = useListPrefs((s) => s.toggleCollapsed)
  return (
    <button type="button" className="o-group" aria-expanded={!group.collapsed} onClick={() => toggle(group.id, false)}>
      <span className={`o-caret${group.collapsed ? ' closed' : ''}`} aria-hidden />
      <span>{group.title}</span>
      {group.collapsed && <span className="o-group-count">{group.count}</span>}
    </button>
  )
}

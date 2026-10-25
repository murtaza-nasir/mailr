// ACT mail.important @web.list (the importance marker)
// ACT mail.archive @web.list, ACT mail.move @web.list (Move to Inbox), ACT mail.untrash @web.list (Restore), ACT mail.delete @web.list,
// ACT mail.read @web.list, ACT mail.unread @web.list, ACT mail.snooze @web.list (RowQuickActions)
// The row kit: the parts of a list row that carry a setting or a state, shared by
// every theme's row (Classic ConversationRow, Workspace OfficeRow, Focus FocusRow, Desk DeskRow,
// Cards CardRow). A theme decides where a part sits and how it looks; whether it shows is decided
// here, once, so a setting offered in every theme works in every theme.
//
// - RowMarker: the importance marker, shown when settings.importance_markers is on.
// - RowMuted: the muted icon on a muted conversation.
// - rowLabels / RowLabelChips: the user labels a row shows (not system labels, not labels hidden
//   from the list, not the label whose list this is).
// - RowAttachments: attachment chips, shown unless the density is compact (showChips).
// - RowSignals: the triage chips (Reply needed, Waiting, Due), the nudge, the recording chip, and
//   "In an attached email" when a search matched only inside an email attached to the conversation.
// - useRowA11y: the row's role, selection, position and keyboard focus. Rows are rows of a
//   grid (MailList's RowGrid): they hold buttons, which an option's presentational children would
//   hide. The grid has one tab stop (the cursor row, else the first row), and the j/k cursor moves
//   DOM focus with it while the focus is in the list, so a screen reader follows it.
// - RowQuickActions: the row's hover and focus buttons (Archive or Move to Inbox or Restore, Delete,
//   Mark as read or unread, Snooze), in Classic and Desk. The theme draws the box they sit in.

import { AlarmClock, Archive, ArchiveRestore, FileText, Image as ImageIcon, File as FileIcon, Mail, MailOpen, Trash2, TriangleAlert, VolumeX } from 'lucide-react'
import { useEffect, useRef, type KeyboardEvent } from 'react'
import type { ConversationSummary, Label } from '../../api/types'
import { ImportanceMarker, LabelChip } from '../../components/bits'
import { attachmentKind } from '../../lib/safeText'
import { NudgeChip } from './NudgeChip'
import { SignalChips } from './SignalChips'
import { RecordingChip } from '../speakr/MailCard'
import type { RowHandlers } from './ConversationRow'
import { t } from '../../i18n'
import { baseView } from '../../lib/views'
import { useArchiveSlot } from '../../lib/rowArchive'

/** The user labels a row shows, in the conversation's order. */
export function rowLabels(c: Pick<ConversationSummary, 'label_ids'>, labelsById: Map<number, Label>, hideLabelId?: number): Label[] {
  return c.label_ids
    .map((id) => labelsById.get(id))
    .filter((l): l is Label => !!l && !l.role && l.show_in_message_list !== false && l.id !== hideLabelId)
}

/** The importance marker; nothing when the setting is off. */
export function RowMarker({ c, markers, onAction }: { c: ConversationSummary; markers: boolean; onAction: RowHandlers['onAction'] }) {
  if (!markers) return null
  return <ImportanceMarker on={c.important} onToggle={() => onAction(c, c.important ? 'not_important' : 'important')} />
}

/** The muted icon; nothing for a conversation that is not muted. */
export function RowMuted({ c, size = 14 }: { c: Pick<ConversationSummary, 'muted'>; size?: number }) {
  if (!c.muted) return null
  return <VolumeX size={size} className="row-muted" aria-label={t('mail.rowParts.muted')} data-tip={t('mail.rowParts.muted')} />
}

/** Label chips, at most `max` of them, with a "+n" for the rest. */
export function RowLabelChips({ labels, max }: { labels: Label[]; max?: number }) {
  const shown = max !== undefined ? labels.slice(0, max) : labels
  const more = labels.length - shown.length
  return (
    <>
      {shown.map((l) => (
        <LabelChip key={l.id} label={l} />
      ))}
      {more > 0 && (
        <span className="label-chip more" title={labels.slice(shown.length).map((l) => l.name).join(', ')}>
          +{more}
        </span>
      )}
    </>
  )
}

export function attIcon(filename: string, type: string) {
  const kind = attachmentKind(filename, type)
  if (kind === 'risky') return <TriangleAlert aria-label={t('mail.rowParts.programOrScript')} className="att-risky" />
  if (kind === 'image') return <ImageIcon />
  if (kind === 'pdf') return <FileText />
  if (type === 'message/rfc822') return <Mail /> // an attached email
  return <FileIcon />
}

/** The attachments a row shows as chips: none in the compact density (showChips off). The server
 *  sends the same file (name and size) once with a count (a thread that carries invite.ics twice). */
export function rowAttachments(c: Pick<ConversationSummary, 'attachments_preview'>, showChips: boolean, max: number): { filename: string; type: string; n: number }[] {
  if (!showChips) return []
  return (c.attachments_preview ?? []).slice(0, max).map((a) => ({ filename: a.filename, type: a.type, n: a.n ?? 1 }))
}

/** Attachment chips with the file's icon; `className` is the theme's chip class. */
export function RowAttachments({ atts, className = 'att-chip' }: { atts: { filename: string; type: string; n: number }[]; className?: string }) {
  return (
    <>
      {atts.map((a, i) => (
        <span key={i} className={className} title={a.n > 1 ? t('mail.rowParts.copies', { filename: a.filename, count: a.n }) : a.filename}>
          {attIcon(a.filename, a.type)}
          <span className="att-name">{a.filename}</span>
          {a.n > 1 && <span className="att-n">×{a.n}</span>}
        </span>
      ))}
    </>
  )
}

/** Triage chips, the nudge, the recording chip and the attached-email search hint. */
export function RowSignals({ c }: { c: ConversationSummary }) {
  return (
    <>
      <NudgeChip c={c} />
      <SignalChips c={c} />
      <RecordingChip id={c.id} />
      {c.match_in_attached && (
        <span className="signal-chip in-attached" data-testid="match-in-attached" title={t('mail.rowParts.matchInAttachedTip')}>
          {t('mail.rowParts.matchInAttached')}
        </span>
      )}
    </>
  )
}

/** Role, selection, position, tab stop and focus for a list row. Spread the result on the row. */
export function useRowA11y({ focused, selected, tabStop, rowIndex, onOpen, name }: {
  focused: boolean
  selected: boolean
  tabStop?: boolean
  rowIndex?: number
  onOpen: () => void
  /** The row's accessible name (rowAccessibleName): unread, sender, subject, count, date. */
  name?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!focused) return
    const el = ref.current
    if (!el) return
    // Follow the cursor only while the focus is in the list (or nowhere): never take it from
    // the reader, a compose window, the search field or a dialog.
    const active = document.activeElement as HTMLElement | null
    const free = !active || active === document.body || !!active.closest?.('[data-mail-list]')
    if (free && active !== el && !el.contains(active)) el.focus({ preventScroll: true })
  }, [focused])
  return {
    ref,
    role: 'row' as const,
    'aria-selected': selected,
    'aria-rowindex': rowIndex,
    'aria-label': name,
    tabIndex: (tabStop ?? focused) ? 0 : -1,
    onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== 'Enter' || e.target !== e.currentTarget) return
      e.preventDefault() // the global Enter (open) would open it a second time
      onOpen()
    },
  }
}

/** The row's quick actions (ACT mail.archive, mail.move, mail.untrash, mail.delete, mail.read,
 *  mail.unread, mail.snooze @web.list): Archive, or Move to Inbox on a row outside the Inbox
 *  (lib/rowArchive.ts), or Restore in Trash; Delete; Mark as read or unread; Snooze. The buttons
 *  run the list's handlers, so the undo toasts and the keys are those of every other surface.
 *  `btn` is the class of each button (the theme's size). */
export function RowQuickActions({ c, view, labelsById, onAction, onSnooze, btn = 'icon-btn' }: {
  c: ConversationSummary
  view: string
  labelsById: Map<number, Label>
  onAction: RowHandlers['onAction']
  onSnooze: RowHandlers['onSnooze']
  btn?: string
}) {
  const snoozeRef = useRef<HTMLButtonElement>(null)
  const unread = c.unread_count > 0
  const inTrash = baseView(view) === 'trash'
  const archived = !c.label_ids.some((id) => labelsById.get(id)?.role === 'inbox')
  // Archive or Move to Inbox by the row's own state (lib/rowArchive.ts); Trash and Spam keep theirs.
  const slot = useArchiveSlot([c], view, labelsById)
  const offerInbox = slot === 'inbox' || (slot === 'keep' && archived && baseView(view) !== 'inbox')
  return (
    <>
      {inTrash ? (
        <button type="button" className={btn} aria-label={t('common.restore')} data-tip={t('common.restore')} onClick={() => onAction(c, 'untrash')}>
          <ArchiveRestore />
        </button>
      ) : offerInbox ? (
        <button type="button" className={btn} aria-label={t('common.moveToInbox')} data-tip={t('common.moveToInbox')} onClick={() => onAction(c, 'inbox')}>
          <ArchiveRestore />
        </button>
      ) : (
        <button type="button" className={btn} aria-label={t('common.archive')} data-tip={t('common.archive')} onClick={() => onAction(c, 'archive')}>
          <Archive />
        </button>
      )}
      {!inTrash && (
        <button type="button" className={btn} aria-label={t('common.delete2')} data-tip={t('common.delete2')} onClick={() => onAction(c, 'trash')}>
          <Trash2 />
        </button>
      )}
      <button
        type="button"
        className={btn}
        aria-label={unread ? t('common.markAsRead') : t('common.markAsUnread')}
        data-tip={unread ? t('common.markAsRead') : t('common.markAsUnread')}
        onClick={() => onAction(c, unread ? 'read' : 'unread')}
      >
        {unread ? <MailOpen /> : <Mail />}
      </button>
      <button ref={snoozeRef} type="button" className={btn} aria-label={t('common.snooze')} data-tip={t('common.snooze')} onClick={() => snoozeRef.current && onSnooze(c, snoozeRef.current)}>
        <AlarmClock />
      </button>
    </>
  )
}

/**
 * A row's select box: one control, role checkbox. The rows had a Select button
 * holding a checkbox input, two controls in one (axe nested-interactive, about 30 a page). The box
 * is drawn with the same .checkbox style.
 */
export function RowCheck({ selected, className, onToggle, tip }: { selected: boolean; className?: string; onToggle: (shift: boolean) => void; tip?: string }) {
  return (
    <button type="button" role="checkbox" aria-checked={selected} className={className} aria-label={t('common.select')} data-tip={tip}
      onClick={(e) => onToggle(e.shiftKey)}>
      <span className="checkbox" data-checked={selected || undefined} aria-hidden="true" />
    </button>
  )
}

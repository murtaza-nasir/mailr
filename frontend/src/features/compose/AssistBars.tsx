import { Sparkles, Undo2, X } from 'lucide-react'
import type { Address } from '../../api/types'
import { assistantNameNow } from '../rail/assistant/useAssistantName'
import type { AssistNote, RecipientProposal } from './composeModel'
import { UsedMemories } from '../rail/assistant/UsedMemories'
import { t } from '../../i18n'

/** What Ambrose or Help me write changed in the draft, with Undo. */
export function AssistNoteBar({ note, onUndo, onDismiss }: { note: AssistNote; onUndo: () => void; onDismiss: () => void }) {
  return (
    <div className="assist-note" role="status">
      <Sparkles size={16} aria-hidden />
      <div className="assist-note-text">
        <span>{note.title}</span>
        {note.changes.filter((c) => c.old || c.new).slice(0, 4).map((c, i) => (
          <span key={i} className="assist-change">
            {c.old ? <del>{c.old.length > 60 ? `${c.old.slice(0, 57)}…` : c.old}</del> : null}
            {c.old && c.new ? ' → ' : null}
            {c.new ? <ins>{c.new.length > 60 ? `${c.new.slice(0, 57)}…` : c.new}</ins> : null}
          </span>
        ))}
        {note.changes.length > 4 && <span className="muted">{t('compose.assistBars.more', { value: note.changes.length - 4 })}</span>}
        {!!note.memoryIds?.length && <UsedMemories ids={note.memoryIds} />}
      </div>
      <button type="button" className="btn text sm" onClick={onUndo}>
        <Undo2 size={14} />{' '}{t('common.undo')}
      </button>
      <button type="button" className="icon-btn xs" aria-label={t('common.dismiss')} onClick={onDismiss}>
        <X size={14} />
      </button>
    </div>
  )
}

interface ProposalProps {
  proposal: RecipientProposal
  current: Record<'to' | 'cc' | 'bcc', Address[]>
  onAccept: () => void
  onReject: () => void
}

/** Recipients the assistant proposes: added addresses underlined, removed ones struck through. */
export function RecipientProposalBar({ proposal, current, onAccept, onReject }: ProposalProps) {
  return (
    <div className="assist-note assist-proposal" role="alert">
      <Sparkles size={16} aria-hidden />
      <div className="assist-note-text">
        <span>{t('compose.assistBars.suggestsDifferentRecipientsNothingChange', { name: assistantNameNow() })}</span>
        {(['to', 'cc', 'bcc'] as const).filter((k) => proposal[k]).map((k) => {
          const now = current[k]
          const next = proposal[k]!
          const has = (list: Address[], e: string) => list.some((a) => a.email.toLowerCase() === e.toLowerCase())
          return (
            <span key={k} className="assist-change">
              <strong>{k === 'to' ? t('common.to') : k === 'cc' ? t('common.cc') : t('common.bcc')}:</strong>{' '}
              {next.map((a) => (has(now, a.email) ? <span key={a.email}>{a.email} </span> : <ins key={a.email}>{a.email} </ins>))}
              {now.filter((a) => !has(next, a.email)).map((a) => <del key={a.email}>{a.email} </del>)}
              {next.length === 0 && now.length === 0 ? <span className="muted">{t('compose.assistBars.none')}</span> : null}
            </span>
          )
        })}
      </div>
      <button type="button" className="btn filled sm" onClick={onAccept}>
        {t('common.accept')}
      </button>
      <button type="button" className="btn text sm" onClick={onReject}>
        {t('compose.assistBars.reject')}
      </button>
    </div>
  )
}

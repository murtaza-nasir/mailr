// The bars a live editor shows: "Assistant is editing this draft…"
// while an agent writes it, and the conflict bar when both changed the same part.

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { GitCompareArrows, Sparkles } from 'lucide-react'
import { useState } from 'react'
import type { ConflictState, Content } from '../../lib/live/machine'
import { byName, editingText, NOUNS, type LiveKind } from '../../lib/live/store'
import './live.css'
import { t } from '../../i18n'

export function EditingBanner({ kind, editing }: { kind: LiveKind; editing: { by: string } | null }) {
  if (!editing) return null
  return (
    <div className="live-editing" role="status" data-testid="live-editing">
      <span className="live-dot" aria-hidden />
      {editingText(kind, editing.by)}
    </div>
  )
}

/** Plain text of an HTML fragment, for the comparison. */
function plain(v: unknown): string {
  if (v == null) return ''
  if (typeof v !== 'string') return Array.isArray(v) ? v.map((x) => (x && typeof x === 'object' && 'email' in x ? String((x as { email: string }).email) : String(x))).join(', ') : JSON.stringify(v)
  if (!/[<>]/.test(v)) return v
  const d = new DOMParser().parseFromString(v.replace(/<\/(p|div|li|h\d)>|<br\s*\/?>/gi, '$&\n'), 'text/html')
  return (d.body.textContent ?? '').replace(/\n{3,}/g, '\n\n').trim()
}

const LABELS: Record<string, string> = {
  html: t('common.text'), text: t('common.text'), notes: t('common.notes'), description: t('common.description'), summary: t('common.title'), subject: t('common.subject'),
  to: 'To', cc: t('common.cc'), bcc: t('common.bcc'), location: t('common.location'), start: t('common.start'), end: t('live.liveBars.end'), name: t('common.name'), query: 'Matches',
}

export function ConflictBar<T extends Content>({ kind, conflict, onMine, onTheirs }: {
  kind: LiveKind
  conflict: ConflictState<T>
  onMine: () => void
  onTheirs: () => void
}) {
  const [compare, setCompare] = useState(false)
  const who = byName(conflict.theirs.by)
  const whoCap = who.charAt(0).toUpperCase() + who.slice(1)
  const theirsLabel = conflict.theirs.by === 'assistant' || !conflict.theirs.by ? t('live.liveBars.useS', { name: assistantNameNow() }) : t('live.liveBars.useTheirs')
  return (
    <div className="live-conflict" role="alert" data-testid="live-conflict">
      <div className="live-conflict-head">
        <Sparkles size={16} aria-hidden />
        <span className="grow">{t('live.liveBars.changedThisWhileYouWere', { whoCap, nouns: NOUNS[kind] })}</span>
        <button type="button" className="btn tonal sm" onClick={onMine}>{t('common.useMine')}</button>
        <button type="button" className="btn tonal sm" onClick={onTheirs}>{theirsLabel}</button>
        <button type="button" className={`btn text sm${compare ? ' active' : ''}`} aria-pressed={compare} onClick={() => setCompare(!compare)}>
          <GitCompareArrows size={14} aria-hidden />{' '}{t('live.liveBars.compare')}
        </button>
      </div>
      {compare && (
        <div className="live-compare" data-testid="live-compare">
          {conflict.fields.map((f) => {
            const chunks = conflict.texts[f]
            const rows = chunks?.length ? chunks.map((c) => ({ mine: plain(c.mine), theirs: plain(c.theirs) }))
              : [{ mine: plain(conflict.mine[f]), theirs: plain(conflict.theirs.content[f]) }]
            return (
              <div key={f} className="live-compare-field">
                <div className="live-compare-label">{LABELS[f] ?? f}</div>
                {rows.map((r, i) => (
                  <div key={i} className="live-compare-row">
                    <div className="live-compare-side mine"><span className="muted">{t('live.liveBars.yours')}</span><del>{r.mine || '(empty)'}</del></div>
                    <div className="live-compare-side theirs"><span className="muted">{whoCap}</span><ins>{r.theirs || '(empty)'}</ins></div>
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

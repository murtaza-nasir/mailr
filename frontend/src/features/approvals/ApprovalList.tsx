// The waiting approvals as a list of cards, with groups: consecutive requests of one kind from one
// requester sit under one header ("claude-code wants to add 2 tasks") with Allow all and Deny all,
// which decide exactly the listed requests, each once (POST /approvals/decide-many). A group of up
// to three is open; a longer one starts closed, with one line per request, and opens with its
// toggle. Deny all asks first in place (nothing undoes a denial). The bell (ApprovalsSection), the
// Today view and Settings → Agents & API use it.
// ACT approval.approve_many @web.reader, ACT approval.deny @web.reader

import { useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { MenuItem, Popover, useAnchor } from '../../components/Popover'
import { formatRelative } from '../../lib/format'
import { toastError } from '../../stores/ui'
import { ApprovalCard, GrantItem } from '../rail/assistant/ApprovalCard'
import type { ApprovalCardData } from '../rail/assistant/useAssistant'
import { actorLabel, requesterKey, sessionLabelFor, toRef, type PendingApproval } from './approvals'
import { groupPhrase, headingOf, kindOf, segments } from './cardHead'
import { decideAll, postDecision, type DecidedCard } from './decide'
import { t } from '../../i18n'

interface Props {
  items: PendingApproval[]
  onStatus: (id: number, status: string, card?: ApprovalCardData) => void
  onDecided: () => void
  /** The card a link opened, outlined. */
  focusedId?: number | null
  /** Extra words beside a focused card (its outcome when already decided). */
  focusedNote?: (p: PendingApproval) => ReactNode
  /** Each card's top line says when the request expires (Settings). */
  withExpiry?: boolean
}

export function ApprovalList({ items, onStatus, onDecided, focusedId = null, focusedNote, withExpiry }: Props) {
  const segs = segments(items, requesterKey)
  const card = (p: PendingApproval) => {
    const focused = focusedId === p.id
    return (
      <div key={p.id} className={`approvals-item${focused ? ' focused' : ''}`} data-item-id={p.id} data-focused={focused || undefined}>
        {focused && focusedNote?.(p)}
        <ApprovalCard a={toRef(p)} actorName={actorLabel(p)} sessionLabel={sessionLabelFor(p)} requester={actorLabel(p)}
          askedAt={p.created_at} expiresAt={withExpiry ? p.expires_at : null} onStatus={onStatus} onDecided={onDecided} />
      </div>
    )
  }
  return (
    <>
      {segs.map((s) => (s.group ? <Group key={s.key} items={s.items} card={card} onStatus={onStatus} onDecided={onDecided} /> : card(s.items[0])))}
    </>
  )
}

function Group({ items, card, onStatus, onDecided }: {
  items: PendingApproval[]
  card: (p: PendingApproval) => ReactNode
  onStatus: Props['onStatus']
  onDecided: () => void
}) {
  const [open, setOpen] = useState(items.length <= 3)
  const [denying, setDenying] = useState(false)
  const [busy, setBusy] = useState(false)
  const grantsMenu = useAnchor()
  const qc = useQueryClient()
  const head = useId()
  const body = useId()
  const n = items.length
  const first = items[0]
  const who = actorLabel(first)
  const phrase = groupPhrase(kindOf(first), who, n)
  // Oldest first: the order they were asked in.
  const ids = [...items].reverse().map((p) => p.id)
  // A grant covers the other waiting requests of its kind (permissions.would_cover): on the group
  // it is the first card's own "Allow for 12 hours".
  const granter = items.find((p) => p.grants !== false)
  const alsoN = granter?.also_covers?.session ?? 0
  const sessionLabel = granter ? sessionLabelFor(granter) : ''
  const done = (cards: DecidedCard[] | null) => {
    for (const c of cards ?? []) onStatus(c.id, c.status, c)
    onDecided()
  }
  const all = async (decision: 'approve' | 'deny') => {
    setBusy(true)
    try {
      done(await decideAll(ids, decision))
    } finally {
      setBusy(false)
      setDenying(false)
    }
  }
  const grant = async () => {
    if (!granter) return
    setBusy(true)
    try {
      const r = await postDecision(granter.id, 'approve', 'session', sessionLabel, qc)
      onStatus(granter.id, r.status, r)
      onDecided()
    } catch (e) {
      toastError(e, t('approvals.approvalsSection.couldNotAllowThem'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="ap-group" aria-labelledby={head} data-testid="approval-group">
      <div className="ap-group-head">
        <button type="button" className="icon-btn xs ap-group-toggle" aria-expanded={open} aria-controls={body}
          aria-label={open ? t('approvals.cards.hideRequests') : t('approvals.cards.showRequests')} onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
        </button>
        <h4 id={head} className="ap-group-title" dir="auto">{phrase}</h4>
      </div>
      {denying ? (
        <div className="ap-group-actions ap-group-confirm" role="group" aria-label={t('approvals.cards.denyAllQuestion', { count: n })}>
          <span>{t('approvals.cards.denyAllQuestion', { count: n })}</span>
          <button type="button" className="btn sm danger" disabled={busy} onClick={() => void all('deny')}>
            {t('approvals.cards.denyAll')}
          </button>
          <button type="button" className="btn text sm" disabled={busy} onClick={() => setDenying(false)}>
            {t('common.cancel')}
          </button>
        </div>
      ) : (
        <div className="ap-group-actions">
          <button type="button" className="btn filled sm" disabled={busy} data-tip={t('approvals.cards.allowAllTip')} onClick={() => void all('approve')}>
            {t('approvals.cards.allowAll')}
          </button>
          <button type="button" className="btn text sm ap-deny" disabled={busy} onClick={() => setDenying(true)}>
            {t('approvals.cards.denyAll')}
          </button>
          <span className="ap-spacer" />
          {granter && (
            <>
              <button type="button" className="btn tonal sm ap-grants" disabled={busy} aria-haspopup="menu" aria-expanded={grantsMenu.open}
                aria-label={alsoN ? t('approvals.cards.moreWaysToAllowAlso', { count: alsoN }) : t('approvals.cards.moreWaysToAllow')}
                onClick={(e) => grantsMenu.toggle(e.currentTarget)}>
                {alsoN > 0 && <span>{t('approvals.cards.plusN', { n: alsoN })}</span>}
                <ChevronDown size={14} aria-hidden />
              </button>
              <Popover anchor={grantsMenu.anchor} open={grantsMenu.open} onClose={grantsMenu.close} placement="bottom-end" label={t('approvals.cards.moreWaysToAllow')}>
                <MenuItem onSelect={() => { grantsMenu.close(); void grant() }}><GrantItem label={sessionLabel} n={alsoN} /></MenuItem>
              </Popover>
            </>
          )}
        </div>
      )}
      <div id={body} className="ap-group-body">
        {open ? (
          items.map((p) => card(p))
        ) : (
          <ol className="ap-group-lines">
            {items.map((p) => (
              <li key={p.id} dir="auto">
                <span className="ap-group-line">{headingOf({ action: p.action, display: p.display, head: p.head, title: p.preview?.title })}</span>
                {p.created_at && <span className="ap-group-when"> · {formatRelative(p.created_at)}</span>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}

// The assistant's brief for a conversation: the answer of a standing order's errand, delivered
// as "brief". Same look as the summary card; citations open in place.
// ACT brief.read @web.reader, ACT brief.dock @web.reader (the button on the card)

import { useQuery } from '@tanstack/react-query'
import { Bot, ChevronDown, ChevronUp, History, PanelRight, PanelTop, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { api } from '../../api/client'
import { useSettings, useUpdateSettings } from '../../api/hooks'
import { Markdown } from '../rail/assistant/LazyMarkdown'
import type { Citation } from '../rail/assistant/citations'
import { useAssistantName } from '../rail/assistant/useAssistantName'
import { orderName, type AssistantRun, type RunApproval } from '../settings/assistant/types'
import { formatRelative } from '../../lib/format'
import { t } from '../../i18n'
import { startsOpen, type AiCardsInput } from './aiCards'

/** The brief's first line as plain text, for the folded card's head. */
export function teaser(md: string): string {
  const line = md.split('\n').map((l) => l.trim()).find(Boolean) ?? ''
  return line.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`#>]+/g, '').replace(/^[-•]\s*/, '').trim()
}

export const briefKey = (id: number) => ['brief', id] as const

const OPEN = new Set(['pending', 'approved', 'executing'])

export function useBrief(id: number) {
  return useQuery<{ brief: AssistantRun | null }>({
    queryKey: briefKey(id),
    queryFn: () => api.get<{ brief: AssistantRun | null }>(`/conversations/${id}/brief`),
    staleTime: 60_000,
    // An approval the run asked for may be decided anywhere (drawer, chat, phone): look again until none is open.
    refetchInterval: (q) => (q.state.data?.brief?.approvals ?? []).some((a) => OPEN.has(a.status)) ? 10_000 : false,
  })
}

/** Where briefs sit (Settings → Ambrose → Briefs & replies, or the button on the card); saved with the settings. */
export function useBriefSide(): [boolean, (side: boolean) => void] {
  const side = useSettings().brief_position === 'side'
  const update = useUpdateSettings()
  return [side, (v) => update.mutate({ brief_position: v ? 'side' : 'top' })]
}

/** The brief's open or folded state the user chose last (this browser). */
function storedOpen(): boolean | null {
  try {
    const v = localStorage.getItem('mailr.briefOpen')
    return v === '1' ? true : v === '0' ? false : null
  } catch {
    return null
  }
}

export function BriefCard({ id, input, onOpenCitation }: { id: number; input: AiCardsInput; onOpenCitation: (c: Citation) => void }) {
  const name = useAssistantName()
  const [side, setSide] = useBriefSide()
  // Folded in a long thread unless the user chose otherwise (aiCards.ts); beside the messages it is open.
  const [open, setOpenState] = useState(() => side || startsOpen(storedOpen(), input, 'brief'))
  const setOpen = (v: boolean) => {
    setOpenState(v)
    try {
      localStorage.setItem('mailr.briefOpen', v ? '1' : '0')
    } catch {
      /* private window: not remembered */
    }
  }
  const q = useBrief(id)
  const b = q.data?.brief
  if (!b || !b.output) return null
  return (
    <div className={`summary-card brief-card no-print${open ? ' open' : ''}`}>
      <div className="head" role="button" tabIndex={0} aria-expanded={open} onClick={() => setOpen(!open)}
        onKeyDown={(e) => e.key === 'Enter' && setOpen(!open)}>
        <Bot />
        <span style={{ flex: open ? 1 : 'none' }}>
          {name}
          {b.order_name ? <span className="muted"> · {orderName({ name: b.order_name, builtin: b.order_builtin })}</span> : null}
        </span>
        {!open && <span className="brief-teaser muted" dir="auto">{teaser(b.output)}</span>}
        {b.finished_at && <span className="muted" style={{ fontSize: 12, marginInlineEnd: 4 }}>{formatRelative(b.finished_at)}</span>}
        <button type="button" className="icon-btn sm brief-dock" aria-label={side ? t('conversation.briefCard.showAboveTheMessages') : t('conversation.briefCard.showBesideTheMessages')}
          data-tip={side ? t('conversation.briefCard.showAboveTheMessages') : t('conversation.briefCard.showBesideTheMessages')}
          onClick={(e) => { e.stopPropagation(); setSide(!side) }} onKeyDown={(e) => e.stopPropagation()}>
          {side ? <PanelTop size={16} /> : <PanelRight size={16} />}
        </button>
        {open ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
      </div>
      {open && (
        <div className="body">
          <Markdown text={b.output} citations={b.citations} onOpenCitation={onOpenCitation} />
          {b.approvals && b.approvals.length > 0 && (
            <ul className="brief-approvals">
              {b.approvals.map((a, i) => <ApprovalLine key={i} a={a} />)}
            </ul>
          )}
          <Link className="brief-how muted" to={`/settings/ambrose/orders?run=${b.id}`}>
            <History size={14} />{' '}{t('conversation.briefCard.howGotThis', { name })}
          </Link>
        </div>
      )}
    </div>
  )
}

/** One approval of the run, as it stands now: the brief's text was written when the run ended. */
function ApprovalLine({ a }: { a: RunApproval }) {
  const path = (() => {
    try {
      return new URL(a.url).pathname
    } catch {
      return a.url
    }
  })()
  const state = a.status === 'executed'
    ? (a.result ? t('conversation.briefCard.approvalDoneWith', { result: a.result }) : t('conversation.briefCard.approvalDone'))
    : a.status === 'pending' ? t('conversation.briefCard.approvalWaiting')
      : a.status === 'approved' || a.status === 'executing' ? t('conversation.briefCard.approvalApproved')
        : a.status === 'denied' ? t('conversation.briefCard.approvalDenied')
          : a.status === 'expired' ? t('conversation.briefCard.approvalExpired')
            : t('conversation.briefCard.approvalFailed')
  return (
    <li className={`brief-approval ${a.status}`}>
      <ShieldCheck size={14} />{' '}
      <span dir="auto">{a.title}</span>
      <span className="muted"> · </span>
      {a.status === 'pending' ? <Link to={path}>{state}</Link> : <span className="muted">{state}</span>}
    </li>
  )
}

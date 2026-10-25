// The main action an email offers, as a chip on its row (mail.primary_action).
// The link opens in a new tab like any link in mail; the server never follows it.
// ACT mail.primary_action @web.list

import { CalendarCheck, Copy, CreditCard, FileText, LogIn, Package, PenLine, Receipt, Video } from 'lucide-react'
import { copyText } from '../../lib/clipboard'
import type { ReactNode } from 'react'
import type { PrimaryAction } from '../../api/types'
import { t, tx } from '../../i18n'

const ICON: Record<string, ReactNode> = {
  track: <Package size={14} />, pay: <CreditCard size={14} />, checkin: <LogIn size={14} />, join: <Video size={14} />,
  rsvp: <CalendarCheck size={14} />, sign: <PenLine size={14} />, statement: <FileText size={14} />, order: <Receipt size={14} />,
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** The action's key fact, unless it is a date and the row already shows a deadline signal ("Due
 * Oct 4" beside "due on 10/04/2026" says the same thing twice). Amounts and arrival windows stay. */
export function factFor(c: { primary_action?: PrimaryAction | null; signals?: { name: string }[] }): boolean {
  const fact = c.primary_action?.fact ?? ''
  const hasDeadline = (c.signals ?? []).some((s) => s.name === 'deadline')
  return !(hasDeadline && /\d{1,2}\/\d{1,2}|\d{4}-\d{2}-\d{2}|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d/i.test(fact))
}

export function ActionChip({ action, withFact = true }: { action?: PrimaryAction | null; withFact?: boolean }) {
  // A one-time code: the chip copies it (verification / sign-in emails).
  if (action?.kind === 'code' && action.code) {
    const code = action.code
    return (
      <span className="action-chip-wrap">
        <button type="button" className="action-chip" title={t('mail.actionChip.copyTheCode')}
          onClick={(e) => {
            e.stopPropagation()
            void copyText(code, t('mail.actionChip.codeCopied', { code }), t('mail.actionChip.couldNotCopyTheCode'))
          }}
          onMouseDown={(e) => e.stopPropagation()}>
          <Copy size={14} />
          {tx('mail.actionChip.copy', { code }, { span: (c) => <span className="action-code">{c}</span> })}
        </button>
      </span>
    )
  }
  if (!action?.url || !/^https?:\/\//i.test(action.url)) return null
  return (
    <span className="action-chip-wrap">
      <a className="action-chip" href={action.url} target="_blank" rel="noopener noreferrer nofollow"
        title={t('mail.actionChip.opens', { actionLabel: action.label, host: host(action.url) })} aria-label={action.label}
        onClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()}>
        {ICON[action.kind] ?? null}
        <span className="action-label">{action.label}</span>
      </a>
      {withFact && action.fact && <span className="action-fact">{action.fact}</span>}
    </span>
  )
}

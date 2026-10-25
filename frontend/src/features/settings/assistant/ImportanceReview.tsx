// Settings → Ambrose → Review importance: the mail the model judged important, and the
// borderline mail it judged not important, for the user to confirm or flip. One key per row
// (i important, n not important, j/k move, Enter opens the email) and a button for each.
// A choice is recorded like a click on the importance marker (a "user" decision, the flag
// set) and the row drops out; the model is retrained after ten choices or two minutes
// (backend intel/routes.py, intel/tasks.py). ACT mail.review_importance @web.settings

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpRight, Flag, FlagOff } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../../api/client'
import { qk } from '../../../api/hooks'
import { Spinner } from '../../../components/bits'
import { mailPath } from '../../../lib/views'
import { toastError } from '../../../stores/ui'
import { Section } from '../common'
import { afterChoice, pct, RANGES, rangeStart, reviewKey, type ReviewOut, type ReviewRange, type ReviewStatus } from './importanceReview'
import { t as tr, tx, appLocale } from '../../../i18n'
import { clockOpts } from '../../../lib/dates'

const qkReview = (range: ReviewRange) => ['importance-review', range] as const

function when(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString(appLocale(), { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() })
}

function StatusLine({ s }: { s: ReviewStatus }) {
  const m = s.model
  return (
    <div className="ir-status" aria-label={tr('settings.assistant.importanceReview.modelStatus')}>
      <b>{tr('settings.assistant.importanceReview.learnedFromChoices', { count: s.learned_from })}</b>
      {m ? (
        <span>
          {tr('settings.assistant.importanceReview.lastTrained', { when: when(m.trained_at) })}
          {m.examples != null && <>{' '}{tr('settings.assistant.importanceReview.onExamples', { count: m.examples })}</>}
          {m.sources && (
            <>{' '}{tr('settings.assistant.importanceReview.yourChoicesRepliedOrStarred', { user: m.sources.user, repliedOrStarred: m.sources.replied_or_starred, archivedUnread: m.sources.archived_unread, providerExcluded: m.sources.provider_excluded })}</>
          )}
          {!m.current && <>{' '}{tr('settings.assistant.importanceReview.trainedBeforeGmailsVerdictsWere')}</>}
          {m.precision != null && <>{' '}{tr('settings.assistant.importanceReview.precisionRecall', { pct: pct(m.precision), pct2: pct(m.recall) })}</>}
        </span>
      ) : (
        <span>{tr('settings.assistant.importanceReview.noLearnedModelYetMail', { minEach: s.min_each })}</span>
      )}
      {s.since_training > 0 && (
        <span className="muted">
          {tr('settings.assistant.importanceReview.newChoicesSinceTheLast', { count: s.since_training, retrainAfter: s.retrain_after })}
        </span>
      )}
    </div>
  )
}

export function ImportanceReview() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [range, setRange] = useState<ReviewRange>('today')
  const since = useMemo(() => rangeStart(range).toISOString(), [range])
  // The status a choice answered with, until the list's next refresh is newer.
  const [chosen, setChosen] = useState<{ status: ReviewStatus; at: number } | null>(null)
  const q = useQuery({
    queryKey: qkReview(range),
    queryFn: () => api.get<ReviewOut>('/intel/importance/review', { since }),
    // While a retrain is due, the status refreshes so "Last trained" moves on its own.
    refetchInterval: (query) =>
      Math.max(query.state.data?.status.since_training ?? 0, chosen?.status.since_training ?? 0) > 0 ? 30_000 : false,
  })
  const [judged, setJudged] = useState<Set<number>>(new Set())
  const [cursor, setCursor] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)

  const all = useMemo(() => q.data?.items ?? [], [q.data])
  const items = useMemo(() => all.filter((it) => !judged.has(it.message_id)), [all, judged])
  const cur = Math.min(cursor, Math.max(0, items.length - 1))
  const shown = chosen && chosen.at > q.dataUpdatedAt ? chosen.status : q.data?.status ?? chosen?.status ?? null

  const choose = useMutation({
    mutationFn: ({ id, important }: { id: number; important: boolean }) =>
      api.post<{ status: ReviewStatus }>(`/messages/${id}/importance`, { important }),
    onSuccess: (r) => {
      setChosen({ status: r.status, at: Date.now() })
      void qc.invalidateQueries({ queryKey: qk.conversations })
    },
    onError: (e, v) => {
      setJudged((s) => { const n = new Set(s); n.delete(v.id); return n })
      toastError(e, tr('settings.assistant.importanceReview.yourChoiceWasNotSaved'))
    },
  })

  const decide = (idx: number, important: boolean) => {
    const it = items[idx]
    if (!it) return
    const next = new Set(judged).add(it.message_id)
    const after = afterChoice(items, next, idx)
    setJudged(next)
    setCursor(after.cursor)
    choose.mutate({ id: it.message_id, important })
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.defaultPrevented || t?.closest?.('input,textarea,select,[contenteditable="true"],[role="dialog"]')) return
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return
      const k = reviewKey(e)
      if (!k || !items.length) return
      // Buttons keep their own Enter; elsewhere Enter opens the email.
      if (k === 'open' && t?.closest?.('button,a')) return
      e.preventDefault()
      e.stopPropagation()
      if (k === 'next') setCursor(Math.min(cur + 1, items.length - 1))
      else if (k === 'prev') setCursor(Math.max(0, cur - 1))
      else if (k === 'important') decide(cur, true)
      else if (k === 'not_important') decide(cur, false)
      else nav(mailPath('all', items[cur].conversation_id))
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  })

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[aria-current="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [cur])

  return (
    <Section
      title={tr('common.reviewImportance')}
      desc={<>{tx('settings.assistant.importanceReview.mailTheModelJudgedImportant', undefined, { kbd: (c) => <kbd>{c}</kbd> })}</>}
    >
      <div className="ir-bar">
        <div className="ir-ranges" role="group" aria-label={tr('settings.assistant.importanceReview.range')}>
          {RANGES.map((r) => (
            <button key={r.id} type="button" className="chip" aria-pressed={range === r.id}
              onClick={() => { setRange(r.id); setCursor(0) }}>
              {r.label}
            </button>
          ))}
        </div>
        {q.isFetching && <Spinner small />}
      </div>
      {shown && <StatusLine s={shown} />}
      {q.isError && <p className="muted">{tr('settings.assistant.importanceReview.theReviewListCouldNot')}</p>}
      {q.isSuccess && items.length === 0 && (
        <p className="ir-empty">{all.length ? tr('settings.assistant.importanceReview.allReviewed') : tr('settings.assistant.importanceReview.nothingToReviewInThis')}</p>
      )}
      <div className="ir-list" role="list" aria-label={tr('settings.assistant.importanceReview.mailToReview')} ref={listRef}>
        {items.map((it, i) => (
          <div key={it.message_id} role="listitem" className="ir-row" aria-current={i === cur ? 'true' : undefined}
            data-message-id={it.message_id} onClick={() => setCursor(i)}>
            <div className="ir-main">
              <div className="ir-head">
                <span className="ir-from">{it.from.name || it.from.email}</span>
                <span className={`pill ${it.judged_important ? 'warn' : ''}`}>
                  {it.judged_important ? tr('settings.assistant.importanceReview.judgedImportant') : tr('settings.assistant.importanceReview.judgedNotImportant')}
                </span>
                {it.borderline && <span className="pill info">{tr('settings.assistant.importanceReview.borderline')}</span>}
                <span className="ir-score" title={tr('settings.assistant.importanceReview.theModelsScore50Or')}>{pct(it.score)}</span>
              </div>
              <div className="ir-subject" dir="auto">{it.subject || tr('common.noSubject')}</div>
              {it.snippet && <div className="ir-snippet muted" dir="auto">{it.snippet}</div>}
              {it.reasons.length > 0 && (
                <ul className="ir-reasons" aria-label={tr('settings.assistant.importanceReview.reasons')}>
                  {it.reasons.map((r) => <li key={r}>{r}</li>)}
                </ul>
              )}
            </div>
            <div className="ir-actions">
              <button type="button" className="btn tonal sm" onClick={(e) => { e.stopPropagation(); decide(i, true) }}
                aria-label={tr('settings.assistant.importanceReview.important', { subject: it.subject || tr('common.noSubject') })}>
                <Flag size={15} />{' '}{tr('common.important')}
              </button>
              <button type="button" className="btn tonal sm" onClick={(e) => { e.stopPropagation(); decide(i, false) }}
                aria-label={tr('settings.assistant.importanceReview.notImportant', { subject: it.subject || tr('common.noSubject') })}>
                <FlagOff size={15} />{' '}{tr('settings.assistant.importanceReview.notImportant2')}
              </button>
              <button type="button" className="icon-btn sm" onClick={(e) => { e.stopPropagation(); nav(mailPath('all', it.conversation_id)) }}
                aria-label={tr('settings.assistant.importanceReview.open', { subject: it.subject || tr('common.noSubject') })} title={tr('common.openTheEmail')}>
                <ArrowUpRight size={16} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </Section>
  )
}

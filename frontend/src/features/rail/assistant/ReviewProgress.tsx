// A bulk review in the chat (backend agent/review.py): how far it got, with Stop and Continue, and its
// review card once it is ready (handed to the turn, so the card renders like any other approval).
// ACT agent.review_mail @web.reader

import { useEffect, useRef, useState } from 'react'
import { Pause, Play, ScanSearch } from 'lucide-react'
import { api } from '../../../api/client'
import { toastError } from '../../../stores/ui'
import type { ApprovalCardData } from './useAssistant'
import { t as tr, appLocale } from '../../../i18n'

export interface ReviewState {
  review_id: number
  status: 'running' | 'stopping' | 'stopped' | 'done' | 'failed' | 'applied'
  scope: string
  total: number
  done: number
  set_aside: number
  decided: Record<string, number>
  approval_id: number | null
  error: string | null
}

const LIVE = new Set(['running', 'stopping'])

export function ReviewProgress({ id, onCard }: { id: number; onCard: (reviewId: number, card: ApprovalCardData) => void }) {
  const [r, setR] = useState<ReviewState | null>(null)
  const [busy, setBusy] = useState(false)
  // Goes up after Stop or Continue: polling starts again while the review runs.
  const [kick, setKick] = useState(0)
  const handed = useRef(false)

  useEffect(() => {
    let stop = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      try {
        const next = await api.get<ReviewState>(`/agent/reviews/${id}`)
        if (stop) return
        setR(next)
        if (next.approval_id && !handed.current) {
          handed.current = true
          onCard(id, await api.get<ApprovalCardData>(`/approvals/${next.approval_id}`))
        }
        if (LIVE.has(next.status)) timer = setTimeout(() => void tick(), 2000)
      } catch {
        if (!stop) timer = setTimeout(() => void tick(), 5000)
      }
    }
    void tick()
    return () => {
      stop = true
      if (timer) clearTimeout(timer)
    }
  }, [id, onCard, kick])

  if (!r) return null
  const n = (x: number) => x.toLocaleString(appLocale())
  const act = async (what: 'stop' | 'resume') => {
    setBusy(true)
    try {
      setR(await api.post<ReviewState>(`/agent/reviews/${id}/${what}`))
      setKick((k) => k + 1)
    } catch (e) {
      toastError(e, tr('rail.assistant.review.couldNotChange'))
    } finally {
      setBusy(false)
    }
  }
  const pct = r.total ? Math.round((100 * r.done) / r.total) : 100
  const head = {
    // The exact scope and its size from the start, so a wrong scope can be stopped at once.
    running: tr('rail.assistant.review.reviewing', { scope: r.scope, count: r.total }),
    stopping: tr('rail.assistant.review.stopping'),
    stopped: tr('rail.assistant.review.stopped', { done: n(r.done), total: n(r.total) }),
    failed: tr('rail.assistant.review.failed', { error: r.error ?? '' }),
    done: r.approval_id ? tr('rail.assistant.review.ready', { count: r.total }) : tr('rail.assistant.review.nothing', { count: r.total }),
    applied: tr('rail.assistant.review.applied'),
  }[r.status]
  return (
    <div className={`review-progress ${r.status}`} role="status" data-review-id={id}>
      <div className="rp-head">
        {LIVE.has(r.status) ? <span className="spinner xs" aria-hidden /> : <ScanSearch size={16} aria-hidden />}
        <span className="grow">{head}</span>
        {r.status === 'running' && (
          <button type="button" className="btn text sm" disabled={busy} onClick={() => void act('stop')}>
            <Pause size={14} aria-hidden />{' '}{tr('rail.assistant.review.stop')}
          </button>
        )}
        {(r.status === 'stopped' || r.status === 'failed') && (
          <button type="button" className="btn tonal sm" disabled={busy} onClick={() => void act('resume')}>
            <Play size={14} aria-hidden />{' '}{tr('rail.assistant.review.continue')}
          </button>
        )}
      </div>
      {LIVE.has(r.status) || r.status === 'stopped' ? (
        <>
          <div className="rp-bar" aria-hidden>
            <span style={{ inlineSize: `${pct}%` }} />
          </div>
          <div className="rp-meta muted">
            {tr('rail.assistant.review.doneOf', { done: n(r.done), total: n(r.total) })}
            {' · '}
            {tr('rail.assistant.review.setAside', { count: r.set_aside })}
          </div>
        </>
      ) : null}
    </div>
  )
}

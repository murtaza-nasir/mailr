// Seeing Ambrose's suggestions without opening Settings (docs/design/memory-quality.md, step 3):
// "Remember: …?" under the reply a suggestion came from, the "n to review" count in the panel's
// header, and the weekly review card at the top of the chat (from Monday morning, until dismissed):
// one line per topic, opened one at a time.
// ACT memory.chat_ask @web.panel, ACT memory.review_count @web.panel, ACT memory.dismiss_review_card @web.panel
// The card's actions (Keep, Merge, Create task, Dismiss) are SuggestionGroups', the Memory page's own.
// ACT memory.merge @web.panel, ACT memory.accept_suggestion @web.panel, ACT memory.dismiss_suggestion @web.panel
// ACT memory.make_task @web.panel

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, ChevronRight, X } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { api } from '../../../api/client'
import { t } from '../../../i18n'
import { toastError } from '../../../stores/ui'
import { invalidateKnowledge } from '../../settings/assistant/memoryBits'
import { groupByTopic, SuggestionGroups } from '../../settings/assistant/SuggestionGroups'
import { qkA, type MemoryCounts, type MemoryList } from '../../settings/assistant/types'

/** One "Remember …?" item of a chat turn, as the session's transcript gives it. */
export interface RememberItem {
  id: number
  text: string
  state: 'waiting' | 'kept' | 'dropped' | 'merged'
}

export function useMemoryCounts(enabled = true) {
  return useQuery<MemoryCounts>({
    queryKey: qkA.memoryCounts,
    queryFn: () => api.get<MemoryCounts>('/assistant/memories/counts'),
    enabled,
    staleTime: 60_000,
  })
}

/** "3 to review" in the panel's header: opens the Memory page's To review. */
export function ReviewCount() {
  const n = useMemoryCounts().data?.suggested ?? 0
  if (!n) return null
  return (
    <Link to="/settings/ambrose/memory#memory-review" className="btn text sm review-count" data-testid="review-count"
      data-tip={t('memory.review.openMemory')}>
      <Brain size={14} aria-hidden />{' '}{t('memory.review.count', { count: n })}
    </Link>
  )
}

/** Under a reply: Ambrose asks to remember what the message before it stated. */
export function RememberAsk({ items, onChange }: { items: RememberItem[]; onChange: (id: number, state: RememberItem['state']) => void }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<number | null>(null)
  if (!items.length) return null
  const answer = async (it: RememberItem, yes: boolean) => {
    setBusy(it.id)
    try {
      await api.post(`/assistant/memories/${it.id}/${yes ? 'accept' : 'reject'}`)
      onChange(it.id, yes ? 'kept' : 'dropped')
      invalidateKnowledge(qc)
    } catch (e) {
      toastError(e)
    } finally {
      setBusy(null)
    }
  }
  return (
    <ul className="remember-ask" data-testid="remember-ask">
      {items.map((it) => (
        <li key={it.id} data-state={it.state}>
          <Brain size={14} aria-hidden />
          {it.state === 'waiting' ? (
            <>
              <span dir="auto">{t('memory.ask.question', { text: it.text })}</span>
              <button type="button" className="btn tonal xs" disabled={busy === it.id} onClick={() => void answer(it, true)}>
                {t('memory.ask.yes')}
              </button>
              <button type="button" className="btn text xs" disabled={busy === it.id} onClick={() => void answer(it, false)}>
                {t('memory.ask.no')}
              </button>
            </>
          ) : (
            <span className="muted" dir="auto">
              {t(it.state === 'kept' ? 'memory.ask.kept' : it.state === 'merged' ? 'memory.ask.merged' : 'memory.ask.dropped', { text: it.text })}
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

/** The weekly review card: one line per topic with its count; one topic opens at a time with the
 *  Memory page's actions (SuggestionGroups). The card has a height limit and links to the Memory page. */
export function ReviewCard() {
  const qc = useQueryClient()
  const counts = useMemoryCounts()
  const due = counts.data?.review_card === true
  const [open, setOpen] = useState<string | null>(null)
  const sugg = useQuery<MemoryList>({
    queryKey: qkA.memories('suggested'),
    queryFn: () => api.get<MemoryList>('/assistant/memories', { status: 'suggested' }),
    enabled: due,
  })
  if (!due) return null
  const items = sugg.data?.items ?? []
  if (!items.length) return null
  const dismiss = async () => {
    try {
      await api.post('/assistant/memories/review-card/dismiss')
    } catch (e) {
      toastError(e)
    }
    void qc.invalidateQueries({ queryKey: qkA.memoryCounts })
  }
  return (
    <section className="review-card" data-testid="review-card" aria-label={t('memory.review.title', { count: items.length })}>
      <header>
        <Brain size={16} aria-hidden />
        <h3>{t('memory.review.title', { count: items.length })}</h3>
        <button type="button" className="icon-btn xs" aria-label={t('memory.review.notNow')} data-tip={t('memory.review.notNow')}
          data-testid="review-card-dismiss" onClick={() => void dismiss()}>
          <X size={16} />
        </button>
      </header>
      <ul className="review-topics">
        {groupByTopic(items).sort((a, b) => b.items.length - a.items.length).map((g) => {
          const isOpen = open === g.key
          return (
            <li key={g.key} data-testid="review-topic">
              <button type="button" className="review-topic" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : g.key)}>
                <ChevronRight size={14} aria-hidden className="review-chevron" />
                <span className="review-topic-name" dir="auto">{g.name}</span>
                <span className="pill">{g.items.length}</span>
              </button>
              {isOpen && <div className="review-topic-body"><SuggestionGroups items={g.items} compact hideTopic /></div>}
            </li>
          )
        })}
      </ul>
      <Link to="/settings/ambrose/memory#memory-review" className="btn text sm review-open-all">
        {t('memory.review.openAll')}
      </Link>
    </section>
  )
}

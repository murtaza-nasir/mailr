// Suggestions grouped by the person or topic they are about, with the same actions on the Memory
// page (To review) and the Ambrose panel's weekly card (docs/design/memory-quality.md, C9 and C10):
// Keep, Merge into another note, Create task (a commitment), Dismiss; Keep all / Dismiss all per
// topic. A tidy proposal says which saved notes it merges, or which one it retires and why.
// ACT memory.merge @web.settings, ACT memory.accept_suggestion @web.settings, ACT memory.dismiss_suggestion @web.settings

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ExternalLink, ListChecks, Merge } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { api } from '../../../api/client'
import { t } from '../../../i18n'
import { mailPath } from '../../../lib/views'
import { toast, toastError } from '../../../stores/ui'
import { conversationOf, invalidateKnowledge, kindExample, kindLabel, MemorySource, useMemoryMutations } from './memoryBits'
import { qkA, type Memory, type MemoryList } from './types'

/** The person or topic a note is about: the server's topic (other wordings of one topic share
 *  it), or its entity or the one kept in its refs. */
export function topicOf(m: Memory): string {
  if (m.topic) return m.topic.self ? t('memory.dialog.you') : m.topic.name || t('memory.review.otherTopic')
  if (m.entity) return m.entity.is_self ? t('memory.dialog.you') : m.entity.name
  const about = m.refs.find((r) => r.about)?.about
  if (about?.self) return t('memory.dialog.you')
  return about?.name || t('memory.review.otherTopic')
}

export function topicKey(m: Memory): string {
  return m.topic?.key ?? topicOf(m)
}

export interface TopicGroup {
  key: string
  name: string
  items: Memory[]
}

/** Group suggestions by topic, keeping the order they came in. */
export function groupByTopic(items: Memory[]): TopicGroup[] {
  const groups = new Map<string, TopicGroup>()
  for (const m of items) {
    const k = topicKey(m)
    const g = groups.get(k) ?? { key: k, name: topicOf(m), items: [] }
    g.items.push(m)
    groups.set(k, g)
  }
  return [...groups.values()]
}

export function useSuggestionActions() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<Set<number>>(new Set())
  const mark = (ids: number[], on: boolean) =>
    setBusy((b) => (on ? new Set([...b, ...ids]) : new Set([...b].filter((x) => !ids.includes(x)))))
  const run = async (ids: number[], verb: 'accept' | 'reject') => {
    mark(ids, true)
    try {
      for (const id of ids) await api.post(`/assistant/memories/${id}/${verb}`)
      if (verb === 'accept') toast({ message: t('memory.page.kept', { count: ids.length }) })
    } catch (e) {
      toastError(e)
    } finally {
      mark(ids, false)
      invalidateKnowledge(qc)
    }
  }
  const merge = async (m: Memory, into: number) => {
    mark([m.id], true)
    try {
      await api.post(`/assistant/memories/${m.id}/merge`, { into })
      toast({ message: t('memory.review.merged') })
    } catch (e) {
      toastError(e)
    } finally {
      mark([m.id], false)
      invalidateKnowledge(qc)
    }
  }
  return { busy, run, merge }
}

/** What a suggestion changes besides adding itself: an edit, a tidy merge, or a retirement. */
function Changes({ m }: { m: Memory }) {
  if (m.retires) {
    return (
      <div className="as-mem-replaces" data-testid="memory-retires">
        {m.retires.reason ? t('memory.review.retiresWhy', { reason: m.retires.reason }) : t('memory.review.retires')}
      </div>
    )
  }
  if (m.merges?.length && m.replaces) {
    return (
      <div className="as-mem-replaces" data-testid="memory-merges">
        {t('memory.review.mergesNotes', { count: m.merges.length + 1 })}
        <ul className="as-merged-list">
          {[m.replaces, ...m.merges].map((x) => <li key={x.id} dir="auto">{x.text}</li>)}
        </ul>
      </div>
    )
  }
  if (m.replaces) {
    return <div className="as-mem-replaces" data-testid="memory-replaces">{t('memory.page.updatesSaved', { text: m.replaces.text })}</div>
  }
  return null
}

/** `hideTopic`: the topic is named around the list already (the review card's open topic). */
export function SuggestionGroups({ items, compact = false, hideTopic = false }: { items: Memory[]; compact?: boolean; hideTopic?: boolean }) {
  const saved = useQuery<MemoryList>({
    queryKey: qkA.memories('active', '', '', null),
    queryFn: () => api.get<MemoryList>('/assistant/memories', { status: 'active', limit: 500 }),
  })
  const { busy, run, merge } = useSuggestionActions()
  const { task } = useMemoryMutations()
  const [merging, setMerging] = useState<number | null>(null)
  return (
    <>
      {groupByTopic(items).map(({ key, name, items: ms }) => {
        const targets = [...(saved.data?.items ?? []).filter((x) => topicKey(x) === key), ...ms.filter((x) => !x.retires)]
        return (
          <div key={key} className="review-group" data-testid="review-group">
            <div className="review-group-head">
              {!hideTopic && <span className="pill info">{name}</span>}
              {ms.length > 1 && (
                <span className="review-group-all">
                  <button type="button" className="btn text xs" onClick={() => void run(ms.map((m) => m.id), 'accept')}>{t('memory.page.keepAll')}</button>
                  <button type="button" className="btn text xs" onClick={() => void run(ms.map((m) => m.id), 'reject')}>{t('settings.assistant.memorySection.dismissAll')}</button>
                </span>
              )}
            </div>
            <ul className={compact ? 'review-list' : 'as-mems as-suggested'}>
              {ms.map((m) => {
                const others = m.retires ? [] : targets.filter((x) => x.id !== m.id)
                const conv = conversationOf(m)
                return (
                  <li key={m.id} className={compact ? 'review-item' : 'as-mem'}>
                    <div className="as-mem-main">
                      <div className="as-mem-text" dir="auto">{m.text}</div>
                      <Changes m={m} />
                      <div className="as-mem-meta">
                        <span className="pill" data-tip={kindExample(m.kind)}>{kindLabel(m.kind)}</span>
                        {!compact && <MemorySource m={m} />}
                        {!compact && m.confidence !== null && <span>{t('settings.assistant.memorySection.sure', { round: Math.round((m.confidence ?? 0) * 100) })}</span>}
                        {!compact && conv && (
                          <Link to={mailPath('all', conv)} className="as-link">
                            <ExternalLink size={12} />{' '}{t('settings.assistant.memorySection.source')}
                          </Link>
                        )}
                      </div>
                    </div>
                    {merging === m.id ? (
                      <select className="review-merge" autoFocus aria-label={t('memory.review.mergeInto')} defaultValue=""
                        onChange={(e) => {
                          setMerging(null)
                          if (e.target.value) void merge(m, Number(e.target.value))
                        }} onBlur={() => setMerging(null)}>
                        <option value="" disabled>{t('memory.review.mergeInto')}</option>
                        {others.map((x) => <option key={x.id} value={x.id}>{x.text}</option>)}
                      </select>
                    ) : (
                      <div className="as-item-actions review-actions">
                        <button type="button" className="btn text sm" disabled={busy.has(m.id)} onClick={() => void run([m.id], 'reject')}>
                          {m.retires ? t('memory.review.keepNote') : t('common.dismiss')}
                        </button>
                        {m.kind === 'commitment' && !m.retires && (
                          <button type="button" className="btn text sm" disabled={busy.has(m.id) || task.isPending}
                            onClick={() => task.mutate(m, { onSuccess: (made) => { if (made) void run([m.id], 'reject') } })}>
                            <ListChecks size={16} />{' '}{t('memory.page.makeTask')}
                          </button>
                        )}
                        {others.length > 0 && (
                          <button type="button" className="btn text sm" disabled={busy.has(m.id)} onClick={() => setMerging(m.id)}>
                            <Merge size={16} />{' '}{t('memory.review.merge')}
                          </button>
                        )}
                        <button type="button" className="btn tonal sm" disabled={busy.has(m.id)} onClick={() => void run([m.id], 'accept')}>
                          <Check size={16} />{' '}{m.retires ? t('memory.review.retire') : t('memory.page.keep')}
                        </button>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        )
      })}
    </>
  )
}

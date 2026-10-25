// Settings → Ambrose → Memory: one page for everything Ambrose remembers.
// How learning went ("Last learned 6:07 AM · 0 new", Learn now), then To review (suggestions:
// Keep / Dismiss, all at once), About you, People (grouped by person, each with its card) and
// Topics (grouped by topic). One search and one kind filter cover every section; one Add memory.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check as CheckIcon, ExternalLink, IdCard, Plus, Search, Sparkles, X } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { api } from '../../../api/client'
import { Skeleton } from '../../../components/bits'
import { toast, toastError } from '../../../stores/ui'
import { useAssistantName } from '../../rail/assistant/useAssistantName'
import { PersonCardDialog } from '../../people/PersonCardDialog'
import { Section } from '../common'
import { EntityDialog } from './EntityCard'
import { MemoryDialog, MemoryRow, MemorySource, conversationOf, invalidateKnowledge, kindExample, kindLabel } from './memoryBits'
import { MEMORY_KINDS, qkA, type LearningRun, type LearningStatus, type Memory, type MemoryList } from './types'
import { mailPath } from '../../../lib/views'
import { clockOpts } from '../../../lib/dates'
import { t as tr, appLocale } from '../../../i18n'

function useDebounced<T>(v: T, ms = 200): T {
  const [d, setD] = useState(v)
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms)
    return () => clearTimeout(t)
  }, [v, ms])
  return d
}

/** "6:07 AM" today, else "Oct 4, 6:07 AM". */
function when(iso: string): string {
  const d = new Date(iso)
  const today = d.toDateString() === new Date().toDateString()
  return d.toLocaleString(appLocale(), today
    ? { hour: 'numeric', minute: '2-digit', ...clockOpts() }
    : { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() })
}

export function useLearning() {
  return useQuery<LearningStatus>({ queryKey: qkA.learning, queryFn: () => api.get<LearningStatus>('/assistant/memories/learning') })
}

/** "Last learned 6:07 AM · 0 new", or what went wrong. ACT memory.learning_status @web.settings */
export function LearningLine() {
  const q = useLearning()
  const s = q.data
  // The line's height is kept while it loads, so nothing below it moves.
  if (!s) return <p className="muted as-learning" aria-hidden>&nbsp;</p>
  const last = s.last
  let text: string
  if (!s.enabled) text = tr('memory.page.learningOff')
  else if (!last) text = tr('memory.page.neverLearned')
  else if (last.error) text = tr('memory.page.lastFailed', { when: when(last.at), reason: learnError(last.error) })
  else text = tr('memory.page.lastLearned', { when: when(last.at), count: last.suggested })
  return <p className={`muted as-learning${last?.error ? ' failed' : ''}`} data-testid="learning-status">{text}</p>
}

function learnError(code: NonNullable<LearningRun['error']>): string {
  const words: Record<NonNullable<LearningRun['error']>, string> = {
    model_failed: tr('memory.page.errors.model_failed'),
    cut_off: tr('memory.page.errors.cut_off'),
    unreadable: tr('memory.page.errors.unreadable'),
    gave_up: tr('memory.page.errors.gave_up'),
  }
  return words[code] ?? words.model_failed
}

interface Group { key: string; title: string; entityId: number; kind: string; items: Memory[] }

export function MemorySection() {
  const name = useAssistantName()
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const dq = useDebounced(q.trim().toLowerCase())
  const [editing, setEditing] = useState<Partial<Memory> | null>(null)
  const [card, setCard] = useState<number | null>(null)
  const [topic, setTopic] = useState<number | null>(null)

  const all = useAllMemories()
  const suggested = useQuery<MemoryList>({
    queryKey: qkA.memories('suggested'),
    queryFn: () => api.get<MemoryList>('/assistant/memories', { status: 'suggested' }),
  })
  // ACT memory.learn_now @web.settings
  const learn = useMutation({
    mutationFn: () => api.post('/assistant/memories/learn'),
    onSuccess: () => {
      toast({ message: tr('memory.page.learnQueued') })
      setTimeout(() => void qc.invalidateQueries({ queryKey: ['assistant'] }), 4000)
    },
    onError: (e) => toastError(e),
  })

  const match = (m: Memory) =>
    (!kind || m.kind === kind) &&
    (!dq || m.text.toLowerCase().includes(dq) || (m.entity?.name ?? '').toLowerCase().includes(dq))
  const sugg = (suggested.data?.items ?? []).filter(match)
  const items = (all.data?.items ?? []).filter(match)

  const { about, people, topics } = useMemo(() => {
    const about: Memory[] = []
    const byEntity = new Map<number, Group>()
    for (const m of items) {
      if (!m.entity || m.entity.is_self) {
        about.push(m)
        continue
      }
      const g = byEntity.get(m.entity.id) ?? { key: `e${m.entity.id}`, title: m.entity.name, entityId: m.entity.id, kind: m.entity.kind, items: [] }
      g.items.push(m)
      byEntity.set(m.entity.id, g)
    }
    const groups = [...byEntity.values()].sort((a, b) => a.title.localeCompare(b.title, appLocale()))
    return { about, people: groups.filter((g) => g.kind === 'person'), topics: groups.filter((g) => g.kind !== 'person') }
  }, [items])

  const loading = all.isLoading
  const filtered = !!(dq || kind)
  const nothing = !loading && !filtered && (all.data?.items.length ?? 0) === 0 && (suggested.data?.items.length ?? 0) === 0

  return (
    <>
      <Section
        title={tr('settings.assistant.memorySection.memory')}
        desc={tr('memory.page.desc', { name })}
        actions={
          <div className="row">
            <button type="button" className="btn outlined" disabled={learn.isPending} onClick={() => learn.mutate()} data-testid="learn-now">
              <Sparkles />{' '}{tr('memory.page.learnNow')}
            </button>
            {/* ACT memory.add @web.settings */}
            <button type="button" className="btn filled" onClick={() => setEditing({})}>
              <Plus />{' '}{tr('common.addMemory')}
            </button>
          </div>
        }
      >
        <LearningLine />
        <div className="as-filters">
          <div className="as-search">
            <Search size={18} aria-hidden />
            <input className="input" value={q} placeholder={tr('settings.assistant.memorySection.searchMemories')} onChange={(e) => setQ(e.target.value)} aria-label={tr('settings.assistant.memorySection.searchMemories')} />
          </div>
          <div className="as-chips" role="group" aria-label={tr('settings.assistant.memorySection.filterByKind')}>
            <button type="button" className="chip" aria-pressed={kind === ''} onClick={() => setKind('')}>
              {tr('common.all')}
            </button>
            {MEMORY_KINDS.map((k) => (
              <button key={k.id} type="button" className="chip" aria-pressed={kind === k.id} data-tip={k.example} onClick={() => setKind(kind === k.id ? '' : k.id)}>
                {k.label}
              </button>
            ))}
          </div>
        </div>
        {nothing && (
          <div className="as-empty-block">
            <p className="as-empty-title">{tr('memory.page.empty')}</p>
            <p className="muted">{tr('memory.page.emptyHint', { name })}</p>
          </div>
        )}
        {!loading && filtered && !items.length && !sugg.length && <p className="muted as-empty">{tr('settings.assistant.memorySection.noMemoriesMatch')}</p>}
      </Section>
      {sugg.length > 0 && <ToReview items={sugg} />}
      {loading ? (
        <Skeleton height={160} />
      ) : (
        <>
          {(about.length > 0 || (!filtered && !nothing)) && (
            <Section id="memory-about" title={tr('memory.page.aboutYou')}>
              {about.length ? (
                <ul className="as-mems" data-testid="memories-about">
                  {about.map((m) => <MemoryRow key={m.id} m={m} hideEntity onEdit={setEditing} />)}
                </ul>
              ) : <p className="muted as-empty">{tr('memory.page.aboutYouEmpty')}</p>}
            </Section>
          )}
          {people.length > 0 && (
            <Section id="memory-people" title={tr('memory.page.people')}>
              {people.map((g) => (
                <MemoryGroup key={g.key} g={g} onEdit={setEditing}
                  open={<button type="button" className="btn text sm" onClick={() => setCard(g.entityId)}><IdCard size={16} />{' '}{tr('memory.page.openCard')}</button>} />
              ))}
            </Section>
          )}
          {topics.length > 0 && (
            <Section id="memory-topics" title={tr('memory.page.topics')}>
              {topics.map((g) => (
                <MemoryGroup key={g.key} g={g} onEdit={setEditing}
                  open={<button type="button" className="btn text sm" onClick={() => setTopic(g.entityId)}><IdCard size={16} />{' '}{tr('memory.page.openCard')}</button>} />
              ))}
            </Section>
          )}
        </>
      )}
      {editing && <MemoryDialog memory={editing} onClose={() => setEditing(null)} />}
      {card !== null && <PersonCardDialog entityId={card} onClose={() => setCard(null)} />}
      {topic !== null && <EntityDialog id={topic} initialKind="topic" onClose={() => setTopic(null)} />}
    </>
  )
}

function useAllMemories() {
  return useQuery<MemoryList>({
    queryKey: qkA.memories('active', '', '', null),
    queryFn: () => api.get<MemoryList>('/assistant/memories', { status: 'active', limit: 500 }),
  })
}

function MemoryGroup({ g, onEdit, open }: { g: Group; onEdit: (m: Memory) => void; open: ReactNode }) {
  return (
    <div className="as-mem-group" data-testid="memory-group">
      <div className="row as-mem-group-head">
        <h3 className="sub-h" style={{ flex: 1 }}><bdi>{g.title}</bdi></h3>
        {open}
      </div>
      <ul className="as-mems">
        {g.items.map((m) => <MemoryRow key={m.id} m={m} hideEntity onEdit={onEdit} />)}
      </ul>
    </div>
  )
}

/** Suggestions from mail and chats: Keep or Dismiss, one by one or all at once. */
function ToReview({ items }: { items: Memory[] }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<Set<number>>(new Set())
  // ACT memory.accept_suggestion @web.settings, ACT memory.dismiss_suggestion @web.settings
  const run = async (ids: number[], verb: 'accept' | 'reject') => {
    setBusy((s) => new Set([...s, ...ids]))
    try {
      for (const id of ids) await api.post(`/assistant/memories/${id}/${verb}`)
      if (verb === 'accept') toast({ message: tr('memory.page.kept', { count: ids.length }) })
    } catch (e) {
      toastError(e)
    } finally {
      setBusy((s) => new Set([...s].filter((x) => !ids.includes(x))))
      invalidateKnowledge(qc)
    }
  }
  return (
    <Section
      id="memory-review"
      title={tr('memory.page.toReview')}
      desc={tr('memory.page.toReviewDesc')}
      actions={
        items.length > 1 ? (
          <div className="row">
            <button type="button" className="btn text" onClick={() => void run(items.map((m) => m.id), 'reject')}>
              {tr('settings.assistant.memorySection.dismissAll')}
            </button>
            <button type="button" className="btn tonal" onClick={() => void run(items.map((m) => m.id), 'accept')}>
              <CheckIcon />{' '}{tr('memory.page.keepAll')}
            </button>
          </div>
        ) : undefined
      }
    >
      <ul className="as-mems as-suggested" data-testid="memories-review">
        {items.map((m) => {
          const about = m.refs.find((r) => r.about)?.about
          const conv = conversationOf(m)
          const who = m.entity ? (m.entity.is_self ? tr('memory.dialog.you') : m.entity.name) : about?.self ? tr('memory.dialog.you') : about?.name
          return (
            <li key={m.id} className="as-mem">
              <div className="as-mem-main">
                <div className="as-mem-text">{m.text}</div>
                <div className="as-mem-meta">
                  <span className="pill" data-tip={kindExample(m.kind)}>{kindLabel(m.kind)}</span>
                  {who && <span className="pill info">{who}</span>}
                  <MemorySource m={m} />
                  {m.confidence !== null && <span>{tr('settings.assistant.memorySection.sure', { round: Math.round((m.confidence ?? 0) * 100) })}</span>}
                  {conv && (
                    <Link to={mailPath('all', conv)} className="as-link">
                      <ExternalLink size={12} />{' '}{tr('settings.assistant.memorySection.source')}
                    </Link>
                  )}
                </div>
              </div>
              <div className="as-item-actions">
                <button type="button" className="btn text sm" disabled={busy.has(m.id)} onClick={() => void run([m.id], 'reject')}>
                  <X size={16} />{' '}{tr('common.dismiss')}
                </button>
                <button type="button" className="btn tonal sm" disabled={busy.has(m.id)} onClick={() => void run([m.id], 'accept')}>
                  <CheckIcon size={16} />{' '}{tr('memory.page.keep')}
                </button>
              </div>
            </li>
          )
        })}
      </ul>
    </Section>
  )
}

/** Settings → Ambrose → About you: the same notes as the Memory page's About you. */
export function AboutYouMemories() {
  const [editing, setEditing] = useState<Partial<Memory> | null>(null)
  const all = useAllMemories()
  const about = (all.data?.items ?? []).filter((m) => !m.entity || m.entity.is_self)
  return (
    <Section id="about-memories" title={tr('memory.page.aboutYou')} desc={tr('memory.page.aboutYouDesc')}
      actions={<button type="button" className="btn tonal" onClick={() => setEditing({})}><Plus size={16} />{' '}{tr('common.addMemory')}</button>}>
      {all.isLoading ? <Skeleton height={80} /> : about.length ? (
        <ul className="as-mems" data-testid="about-you-memories">
          {about.map((m) => <MemoryRow key={m.id} m={m} hideEntity onEdit={setEditing} />)}
        </ul>
      ) : <p className="muted as-empty">{tr('memory.page.aboutYouEmpty')}</p>}
      <Link to="/settings/ambrose/memory" className="as-link">{tr('memory.page.allMemories')}</Link>
      {editing && <MemoryDialog memory={editing} onClose={() => setEditing(null)} />}
    </Section>
  )
}

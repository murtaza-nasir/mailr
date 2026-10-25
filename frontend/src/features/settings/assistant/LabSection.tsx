// Settings → Ambrose → Decisions lab: run every triage decision on some emails with the rules
// and each sorting model (the decision models…) side by side; nothing is saved (backend intel/lab.py).
// "Re-run on the inbox" saves: category and signals decided again, the model allowed.

import { useMutation, useQuery } from '@tanstack/react-query'
import { Cloud, FlaskConical, HardDrive, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../../api/client'
import { Spinner } from '../../../components/bits'
import { toast, toastError } from '../../../stores/ui'
import { Section } from '../common'
import { t, tx } from '../../../i18n'

interface Ep { name: string; adapter: string; locality: string; model?: string }
interface Run<T> { value?: T; confidence?: number | null; reason?: string | null; ms?: number; error?: string | null; note?: string | null }
interface Signals { needs_reply?: boolean; asks_question?: boolean; request?: string; deadline?: string | null; deadline_model?: string; deadline_dropped?: string; ms?: number; error?: string | null }
interface ModelRes { category?: Run<string>; signals?: Signals; action?: Run<string>; question?: { answer?: boolean; confidence?: number; reason?: string; source?: string; ms?: number; error?: string | null } }
interface Item {
  message: { id: number; conversation_id: number; subject: string; from: string; date: string | null; local_only: boolean; bulk: boolean; to_me: boolean }
  stored: { category?: { value: string; source: string }; importance?: { value: boolean; source: string }; signals: { name: string; source: string; reason: string }[] }
  rules: { category: Run<string>; learned: Run<string> | null; signals: { needs_reply: boolean; waiting_on: boolean; deadline: string | null; reason: string }; actions: { kind: string; label: string }[] }
  models: Record<string, ModelRes>
  /** A built-in sample email: what it tests and what a good answer is. */
  sample?: { id: string; tests: string; expected: { category?: string | string[]; needs_reply?: boolean; deadline?: string | null; action?: string } }
}
interface LabOut { endpoints: Ep[]; question: string | null; items: Item[] }

const pct = (x?: number | null) => (x == null ? '' : ` ${Math.round(x * 100)}%`)
const ms = (x?: number) => (x == null ? '' : t('settings.assistant.labSection.ms', { x }))
const NAMES: Record<string, string> = { needs_reply: t('settings.assistant.labSection.needsReply'), waiting_on: 'waiting', deadline: 'deadline' }

/** ✓ / ✗ against a sample's expected answer (nothing for real mail). */
function Mark({ ok }: { ok: boolean | null | undefined }) {
  if (ok == null) return null
  return <span className={`lab-mark ${ok ? 'ok' : 'bad'}`} aria-label={ok ? t('settings.assistant.labSection.asExpected') : t('settings.assistant.labSection.notAsExpected')}>{ok ? '✓' : '✗'} </span>
}

function okCat(it: Item, v?: string | null): boolean | null {
  const want = it.sample?.expected.category
  if (!want || !v) return null
  return Array.isArray(want) ? want.includes(v) : v === want
}

/** Reply right, and the deadline right: none when none is expected, else the expected date in it. */
function okSig(it: Item, reply: boolean, deadline?: string | null): boolean | null {
  const e = it.sample?.expected
  if (!e) return null
  const dOk = e.deadline ? !!deadline && deadline.toLowerCase().includes(e.deadline.toLowerCase().replace(/, \d{4}$/, '')) : !deadline
  return reply === !!e.needs_reply && dOk
}

function Cell({ children, err }: { children?: React.ReactNode; err?: string | null }) {
  return <td>{err ? <span className="lab-err">{err}</span> : children ?? <span className="muted">—</span>}</td>
}

export function LabSection() {
  const eps = useQuery({ queryKey: ['lab', 'endpoints'], queryFn: () => api.get<Ep[]>('/intel/lab/endpoints') })
  const [q, setQ] = useState('in:inbox')
  const [limit, setLimit] = useState(6)
  const [question, setQuestion] = useState('')
  const [picked, setPicked] = useState<string[] | null>(null)
  const chosen = picked ?? (eps.data ?? []).map((e) => e.name)
  const run = useMutation({
    mutationFn: (samples: boolean) => api.post<LabOut>('/intel/lab', samples ? { samples: true, question, endpoints: chosen } : { q, limit, question, endpoints: chosen }),
    onError: (e) => toastError(e, t('settings.assistant.labSection.theLabRunFailed')),
  })
  const rerun = useMutation({
    mutationFn: () => api.post<{ queued: number }>('/intel/retriage', { view: 'inbox', limit: 200 }),
    onSuccess: (r) => toast({ message: t('settings.assistant.labSection.reRunningCategoryAndSignals', { count: r.queued }) }),
    onError: (e) => toastError(e, t('settings.assistant.labSection.couldNotStartTheRe')),
  })
  const out = run.data
  return (
    <Section
      title={t('settings.assistant.labSection.decisionsLab')}
      desc={<>{tx('settings.assistant.labSection.seeWhatEachModelDecides', undefined, { b: (c) => <b>{c}</b> })}</>}
      actions={
        <button type="button" className="btn tonal sm" disabled={rerun.isPending} onClick={() => rerun.mutate()}
          title={t('settings.assistant.labSection.decideCategoryAndSignalsAgain')}>
          <RefreshCw size={16} />{' '}{t('settings.assistant.labSection.reRunOnTheInbox')}
        </button>
      }
    >
      <div className="lab-form">
        <label>{t('common.emails')}{' '}<input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="in:inbox" aria-label={t('settings.assistant.labSection.searchForEmails')} /></label>
        <label>{t('settings.assistant.labSection.howMany')}{' '}<input className="input" type="number" min={1} max={15} value={limit} onChange={(e) => setLimit(Math.max(1, Math.min(15, Number(e.target.value) || 1)))} style={{ width: 72 }} /></label>
        <label className="lab-q">{t('settings.assistant.labSection.questionOptional')}{' '}<input className="input" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={t('settings.assistant.labSection.isThisABillOr')} /></label>
        <div className="lab-eps" role="group" aria-label={t('common.models')}>
          {(eps.data ?? []).map((e) => (
            <label key={e.name} className="choice">
              <input type="checkbox" className="checkbox" checked={chosen.includes(e.name)}
                onChange={(ev) => setPicked(ev.target.checked ? [...chosen, e.name] : chosen.filter((x) => x !== e.name))} />
              {e.locality === 'local' ? <HardDrive size={14} /> : <Cloud size={14} />} {e.name}
            </label>
          ))}
          {eps.data && eps.data.length === 0 && <span className="muted">{t('settings.assistant.labSection.noSortingModelIsSet')}</span>}
        </div>
        <button type="button" className="btn filled" disabled={run.isPending} onClick={() => run.mutate(false)}>
          {run.isPending ? <Spinner small /> : <FlaskConical size={16} />}{' '}{t('settings.assistant.labSection.run')}
        </button>
        <button type="button" className="btn tonal" disabled={run.isPending} onClick={() => run.mutate(true)}
          title={t('settings.assistant.labSection.twelveRealisticEmailsABill')}>
          {t('settings.assistant.labSection.runTheSampleEmails')}
        </button>
      </div>
      {out && out.items.length === 0 && <p className="muted">{t('settings.assistant.labSection.noEmailsMatchThatSearch')}</p>}
      {out && out.items.map((it) => (
        <div key={it.message.id} className="lab-item">
          <div className="lab-head">
            <b>{it.message.subject || t('common.noSubject')}</b>
            <span className="muted"> · {it.message.from}</span>
            {it.message.bulk && <span className="pill info">{t('settings.assistant.labSection.bulk')}</span>}
            {it.message.to_me && <span className="pill">{t('settings.assistant.labSection.toYou')}</span>}
            {it.message.local_only && <span className="pill warn">{t('settings.assistant.labSection.localOnly')}</span>}
            {it.sample && <span className="muted lab-tests">{t('settings.assistant.labSection.tests', { tests: it.sample.tests })}</span>}
          </div>
          <div className="lab-scroll">
            <table className="lab-table">
              <thead><tr><th />{it.sample ? <th>{t('settings.assistant.labSection.expected')}</th> : <th>{t('common.saved')}</th>}<th>{t('settings.assistant.labSection.rules')}</th>{out.endpoints.map((e) => <th key={e.name}>{e.name}<span className="muted"> {e.model}</span></th>)}</tr></thead>
              <tbody>
                <tr>
                  <th>{t('common.category')}</th>
                  {it.sample ? <Cell>{[it.sample.expected.category].flat().join(' or ')}</Cell> : <Cell>{it.stored.category ? `${it.stored.category.value} (${it.stored.category.source})` : undefined}</Cell>}
                  <Cell><Mark ok={okCat(it, it.rules.category.value)} />{it.rules.category.value}{pct(it.rules.category.confidence)}{it.rules.learned ? t('settings.assistant.labSection.learned', { learnedValue: it.rules.learned.value, pct: pct(it.rules.learned.confidence) }) : ''}</Cell>
                  {out.endpoints.map((e) => { const c = it.models[e.name]?.category; return <Cell key={e.name} err={c?.error}><Mark ok={okCat(it, c?.value)} />{c?.value ? `${c.value}${pct(c.confidence)}${ms(c.ms)}` : c ? t('settings.assistant.labSection.noAnswer', { ms: ms(c.ms) }) : undefined}</Cell> })}
                </tr>
                <tr>
                  <th>{t('settings.assistant.labSection.signals')}</th>
                  {it.sample
                    ? <Cell>{[it.sample.expected.needs_reply ? t('settings.assistant.labSection.needsReply') : t('settings.assistant.labSection.noReply'), it.sample.expected.deadline ? t('settings.assistant.labSection.deadline', { deadline: it.sample.expected.deadline }) : t('settings.assistant.labSection.noDeadline')].join(', ')}</Cell>
                    : <Cell>{it.stored.signals.length ? it.stored.signals.map((s) => `${NAMES[s.name] ?? s.name} (${s.source})`).join(', ') : 'none'}</Cell>}
                  <Cell><Mark ok={okSig(it, it.rules.signals.needs_reply, it.rules.signals.deadline)} />{[it.rules.signals.needs_reply && t('settings.assistant.labSection.needsReply'), it.rules.signals.waiting_on && t('settings.assistant.labSection.asksAQuestion'), it.rules.signals.deadline && t('settings.assistant.labSection.deadline', { deadline: it.rules.signals.deadline })].filter(Boolean).join(', ') || 'none'}</Cell>
                  {out.endpoints.map((e) => { const s = it.models[e.name]?.signals; return (
                    <Cell key={e.name} err={s?.error}><Mark ok={s ? okSig(it, !!s.needs_reply, s.deadline) : null} />{s ? `${[s.needs_reply && t('settings.assistant.labSection.needsReply'), s.asks_question && t('settings.assistant.labSection.asksAQuestion'), s.deadline && t('settings.assistant.labSection.deadline', { deadline: s.deadline })].filter(Boolean).join(', ') || 'none'}${s.request ? ` — “${s.request}”` : ''}${s.deadline_model ? t('settings.assistant.labSection.modelSaidDeadlineDropped', { deadlineModel: s.deadline_model, deadlineDropped: s.deadline_dropped }) : ''}${ms(s.ms)}` : undefined}</Cell>
                  ) })}
                </tr>
                {(it.rules.actions.length > 0 || it.sample?.expected.action) && (
                  <tr>
                    <th>{t('settings.assistant.labSection.mainAction')}</th>
                    <Cell>{it.sample?.expected.action}</Cell>
                    <Cell><Mark ok={it.sample?.expected.action ? it.rules.actions[0]?.kind === it.sample.expected.action : null} />{it.rules.actions.map((a) => a.label).join(' › ') || 'none'}</Cell>
                    {out.endpoints.map((e) => { const a = it.models[e.name]?.action; return <Cell key={e.name} err={a?.error}>{a ? (a.note ?? <><Mark ok={it.sample?.expected.action ? a.value === it.sample.expected.action : null} />{`${a.value ?? t('settings.assistant.labSection.noChoice')}${ms(a.ms)}`}</>) : undefined}</Cell> })}
                  </tr>
                )}
                {out.question && (
                  <tr>
                    <th>{t('settings.assistant.labSection.question')}</th>
                    <Cell />
                    <Cell />
                    {out.endpoints.map((e) => { const a = it.models[e.name]?.question; return <Cell key={e.name} err={a?.error}>{a && a.answer != null ? `${a.answer ? 'yes' : 'no'}${pct(a.confidence)}${a.reason ? ` — ${a.reason}` : ''}${ms(a.ms)}` : undefined}</Cell> })}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </Section>
  )
}

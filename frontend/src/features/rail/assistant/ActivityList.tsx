// What the assistant is doing, as a readable list: one row per step with an icon, a plain label
// ("Reading “PADS Invites…”"), and the outcome ("50 conversations"). Live while it works; folded
// into one line ("Looked at 6 conversations · 13 steps · 11.2 s") when it is done. The icon is the
// server's (agent/present.py step_icon), with a local fallback for older servers.

import {
  AlertCircle, AudioLines, Brain, CalendarDays, Check, ChevronRight, Clock, FilePen, Layers, MailOpen, Search, Send, Settings2,
  SquareCheck, StickyNote, Tag, UserRound, Wrench,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { activitySummary, foldRepeats, stepIcon, stepResult, type Step, type StepIcon, type StepRow } from './steps'
import { t } from '../../../i18n'

const ICON: Record<StepIcon, ReactNode> = {
  search: <Search size={14} />,
  read: <MailOpen size={14} />,
  person: <UserRound size={14} />,
  calendar: <CalendarDays size={14} />,
  task: <SquareCheck size={14} />,
  draft: <FilePen size={14} />,
  send: <Send size={14} />,
  label: <Tag size={14} />,
  context: <Layers size={14} />,
  recording: <AudioLines size={14} />,
  memory: <Brain size={14} />,
  settings: <Settings2 size={14} />,
  note: <StickyNote size={14} />,
  other: <Wrench size={14} />,
}

function StatusMark({ s }: { s: Step }) {
  if (s.status === 'running') return <span className="spinner xs" aria-label="working" />
  if (s.status === 'failed') return <AlertCircle size={14} className="act-fail" aria-label="failed" />
  if (s.status === 'waiting') return <Clock size={14} className="act-wait" aria-label={t('rail.assistant.activityList.waitingForApproval')} />
  return <Check size={14} className="act-ok" aria-label="done" />
}

/** A row's label, with "×3" when the same call was asked for again (foldRepeats). */
function rowLabel(s: StepRow): string {
  return s.times > 1 ? t('rail.assistant.activityList.repeated', { label: s.label, n: s.times }) : s.label
}

function Row({ s, all }: { s: StepRow; all: Step[] }) {
  const result = stepResult(s, all)
  return (
    <li className={`act-row ${s.status}`}>
      <span className="act-icon" aria-hidden>
        {ICON[(s.icon as StepIcon) in ICON ? (s.icon as StepIcon) : stepIcon(s.name)]}
      </span>
      <span className="act-label">{rowLabel(s)}</span>
      {result && <span className="act-result">{result}</span>}
      <StatusMark s={s} />
    </li>
  )
}

interface Props {
  steps: Step[]
  running: boolean
  seconds?: number
  conversations?: number
}

export function ActivityList({ steps, running, seconds, conversations }: Props) {
  const [open, setOpen] = useState(false)
  const [dev, setDev] = useState(false)
  if (!steps.length) return null
  const expanded = running || open
  const rows = foldRepeats(steps)
  return (
    <div className={`activity${running ? ' live' : ''}`}>
      {!running && (
        <button type="button" className="act-summary" aria-expanded={open} onClick={() => setOpen(!open)}>
          <ChevronRight size={14} className={`act-chevron${open ? ' open' : ''}`} aria-hidden />
          {rows.length === 1 && seconds == null
            ? `${rowLabel(rows[0])}${stepResult(rows[0], steps) ? ` · ${stepResult(rows[0], steps)}` : ''}`
            : activitySummary(rows, seconds, conversations)}
        </button>
      )}
      {expanded && (
        <>
          <ol className="act-list">
            {rows.map((s) => (
              <Row key={s.id} s={s} all={steps} />
            ))}
          </ol>
          {!running && (
            <details className="act-dev" open={dev} onToggle={(e) => setDev((e.target as HTMLDetailsElement).open)}>
              <summary>{t('rail.assistant.activityList.technicalDetails')}</summary>
              {dev &&
                steps.map((s) => (
                  <pre key={s.id}>
                    {s.name}
                    {s.ms != null ? t('rail.assistant.activityList.ms', { ms: s.ms }) : ''}
                    {s.error ? t('rail.assistant.activityList.error', { error: s.error }) : ''}
                    {'\n'}
                    {JSON.stringify(s.args ?? {}, null, 1)}
                  </pre>
                ))}
            </details>
          )}
        </>
      )}
    </div>
  )
}

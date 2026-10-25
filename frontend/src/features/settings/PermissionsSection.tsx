// Settings → Agents & API → Permissions.
//
// Per subject (every agent, the built-in assistant, or one API token): the resolved effect for each
// action class, editable in place (allow / ask / deny / default), plus narrower rules with
// constraints (recipient domains, replies only, calendars, task lists, rate limits, …).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api } from '../../api/client'
import { useApiList, useLabelList } from '../../api/hooks'
import type { ApiToken } from '../../api/types'
import { Skeleton } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { toastError } from '../../stores/ui'
import { Section } from './common'
import { t as tr, appLocale } from '../../i18n'
import { assistantNameNow } from '../rail/assistant/assistantName'

type Effect = 'allow' | 'ask' | 'deny'

interface PermissionRule {
  id: number
  subject: string
  action: string
  effect: Effect
  constraints: Record<string, unknown>
  source: string
  expires_at: string | null
  created_at: string
}

interface RulesResponse {
  classes: { action: string; default: Effect; scope: string }[]
  constraints: string[]
  rules: PermissionRule[]
}

interface EffectiveRow {
  action: string
  effect: Effect
  source: string
  default: Effect
}

export const ACTION_LABELS: Record<string, string> = {
  'mail.read': tr('settings.permissionsSection.readAndSearchMail'),
  'mail.draft': tr('settings.permissionsSection.writeDraftsAndRewriteBriefs'),
  'mail.label': tr('settings.permissionsSection.labelArchiveMarkReadSnooze'),
  'mail.send': tr('settings.permissionsSection.sendMail'),
  'mail.trash': tr('common.moveToTrash'),
  'mail.spam': tr('settings.permissionsSection.reportSpamBlockSenders'),
  'mail.delete': tr('common.deleteForever'),
  'mail.unsubscribe': tr('settings.permissionsSection.unsubscribeFromLists'),
  'mail.screener': tr('settings.permissionsSection.allowBlockOrLabelA'),
  'rules.write': tr('settings.permissionsSection.createAndChangeFilters'),
  'calendar.read': tr('settings.permissionsSection.readCalendars'),
  'calendar.create': tr('settings.permissionsSection.createEvents'),
  'calendar.update': tr('settings.permissionsSection.changeEvents'),
  'calendar.delete': tr('settings.permissionsSection.deleteEvents'),
  'calendar.rsvp': tr('settings.permissionsSection.answerInvitations'),
  'tasks.read': tr('settings.permissionsSection.readTasks'),
  'tasks.create': tr('settings.permissionsSection.createTasks'),
  'tasks.update': tr('settings.permissionsSection.changeTasks'),
  'tasks.complete': tr('settings.permissionsSection.completeTasks'),
  'tasks.delete': tr('settings.permissionsSection.deleteTasks'),
  'contacts.read': tr('settings.permissionsSection.readContacts'),
  'contacts.write': tr('settings.permissionsSection.createAndChangeContactGroups'),
  'labels.write': tr('settings.permissionsSection.createRenameAndRecolourLabels'),
  'labels.delete': tr('settings.permissionsSection.deleteLabels'),
  'prefs.write': tr('settings.permissionsSection.changeSettingsInboxLayoutsSignatures'),
  'memory.read': tr('settings.permissionsSection.readMemoriesPeopleAndSkills'),
  'memory.write': tr('settings.permissionsSection.saveAndForgetMemoriesPeople'),
  'skills.write': tr('settings.permissionsSection.createAndChangeSkills'),
  'settings.write': tr('settings.permissionsSection.changeSettings'),
  'speakr.link': tr('settings.permissionsSection.linkRecordingsToEventsEmails'),
  'speakr.upload': tr('settings.permissionsSection.sendAttachmentsToSpeakrFor'),
  'speakr.write': tr('settings.permissionsSection.editRecordingsInSpeakrTitle'),
  'speakr.share': tr('settings.permissionsSection.createPublicSpeakrShareLinks'),
  'web.search': tr('settings.permissionsSection.searchTheWebAndRead'),
  'notes.read': tr('settings.permissionsSection.readNotesAndChecklists'),
  'notes.create': tr('settings.permissionsSection.makeNotesAndChecklists'),
  'notes.edit': tr('settings.permissionsSection.changeNotesAddCheckAnd'),
  'notes.delete': tr('settings.permissionsSection.moveNotesToTrashClear'),
  'notes.labels': tr('settings.permissionsSection.createRenameRecolourAndDelete'),
  'agent.forwarded_instructions': tr('settings.permissionsSection.actOnNotesYouForward'),
}

const GROUPS: { title: string; prefix: string[] }[] = [
  { title: tr('common.mail2'), prefix: ['mail.'] },
  { title: tr('settings.permissionsSection.filtersLabelsAndSettings'), prefix: ['rules.', 'labels.', 'prefs.'] },
  { get title() { return tr('settings.permissionsSection.memoryAndSkills', { name: assistantNameNow() }) }, prefix: ['memory.', 'skills.'] },
  { title: tr('common.calendar'), prefix: ['calendar.'] },
  { title: tr('common.tasks'), prefix: ['tasks.'] },
  { title: tr('settings.permissionsSection.recordingsSpeakr'), prefix: ['speakr.'] },
  { title: tr('settings.permissionsSection.notesAndChecklists'), prefix: ['notes.'] },
  { title: tr('settings.permissionsSection.web'), prefix: ['web.'] },
  { title: tr('settings.permissionsSection.other'), prefix: ['contacts.', 'settings.', 'agent.'] },
]

const EFFECT_TEXT: Record<Effect, string> = { allow: tr('settings.permissionsSection.allow'), ask: tr('settings.permissionsSection.askMe'), deny: tr('settings.permissionsSection.deny') }

/** Reading is limited by a token's scopes and its account / local-only limits, not by rules:
 * read classes are not offered here (they would not be enforced). */
const isReadClass = (action: string) => action.endsWith('.read')

function describeSource(src: string): string {
  if (src === 'default') return 'default'
  if (src.startsWith('rule:')) return tr('settings.permissionsSection.yourRule')
  if (src.startsWith('missing scope')) return src.replace('missing scope', 'token lacks scope')
  return src
}

export function describeConstraints(c: Record<string, unknown>, labelName: (id: number) => string): string {
  const parts: string[] = []
  if (c.replies_only) parts.push(tr('settings.permissionsSection.repliesOnly'))
  if (c.known_only) parts.push(tr('settings.permissionsSection.knownRecipientsOnly'))
  if (Array.isArray(c.domains) && c.domains.length) parts.push(tr('settings.permissionsSection.domains', { domains: c.domains.join(', ') }))
  if (Array.isArray(c.recipients) && c.recipients.length) parts.push(tr('settings.permissionsSection.recipients', { recipients: c.recipients.join(', ') }))
  if (c.max_per_hour) parts.push(`≤ ${c.max_per_hour}/hour`)
  if (c.max_per_day) parts.push(`≤ ${c.max_per_day}/day`)
  if (Array.isArray(c.calendars) && c.calendars.length) parts.push(tr('settings.permissionsSection.calendarS', { count: c.calendars.length }))
  if (Array.isArray(c.lists) && c.lists.length) parts.push(tr('settings.permissionsSection.taskListS', { count: c.lists.length }))
  if (Array.isArray(c.labels) && c.labels.length) {
    // Mail labels are ids; note labels are names.
    parts.push(tr('settings.permissionsSection.labels', { map: (c.labels as (number | string)[]).map((l) => (typeof l === 'number' ? labelName(l) : l)).join(', ') }))
  }
  if (Array.isArray(c.notes) && c.notes.length) parts.push(tr('settings.permissionsSection.notes', { count: c.notes.length }))
  if (Array.isArray(c.connectors) && c.connectors.length) parts.push(tr('settings.permissionsSection.accountS', { count: c.connectors.length }))
  return parts.join(' · ') || tr('settings.permissionsSection.noConditions')
}

export function PermissionsSection() {
  const qc = useQueryClient()
  const tokens = useApiList<ApiToken>(['tokens'], '/tokens')
  const labels = useLabelList()
  const [subject, setSubject] = useState('assistant')
  const [editing, setEditing] = useState<Partial<PermissionRule> | null>(null)

  const rulesQ = useQuery<RulesResponse>({ queryKey: ['permissions'], queryFn: ({ signal }) => api.get('/permissions', undefined, signal) })
  const effQ = useQuery<{ items: EffectiveRow[] }>({
    queryKey: ['permissions', 'effective', subject],
    queryFn: ({ signal }) => api.get('/permissions/effective', { subject }, signal),
  })
  const done = () => void qc.invalidateQueries({ queryKey: ['permissions'] })

  const subjects = useMemo(
    () => [
      { id: 'assistant', label: tr('settings.permissionsSection.builtInAssistant') },
      { id: '*', label: tr('settings.permissionsSection.everyAgentDefaultForAll') },
      ...(tokens.data ?? []).filter((t) => !t.revoked).map((t) => ({ id: `token:${t.id}`, label: tr('settings.permissionsSection.token', { itemName: t.name }) })),
    ],
    [tokens.data],
  )
  const labelName = (id: number) => labels.find((l) => l.id === id)?.name ?? `#${id}`
  const rules = (rulesQ.data?.rules ?? []).filter((r) => r.subject === subject)
  const plain = new Map(rules.filter((r) => !Object.keys(r.constraints ?? {}).length && !r.expires_at).map((r) => [r.action, r]))
  const conditional = rules.filter((r) => Object.keys(r.constraints ?? {}).length || r.expires_at)

  const setEffect = useMutation({
    mutationFn: async ({ action, effect }: { action: string; effect: Effect | 'default' }) => {
      const r = plain.get(action)
      if (effect === 'default') {
        if (r) await api.del(`/permissions/${r.id}`)
        return
      }
      if (r) await api.patch(`/permissions/${r.id}`, { effect })
      else await api.post('/permissions', { subject, action, effect, constraints: {} })
    },
    onSuccess: done,
    onError: (e) => toastError(e, tr('settings.permissionsSection.couldNotChangePermission')),
  })
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/permissions/${id}`),
    onSuccess: done,
    onError: (e) => toastError(e, tr('settings.permissionsSection.couldNotDeleteRule')),
  })

  const rows = (effQ.data?.items ?? []).filter((r) => !isReadClass(r.action))
  return (
    <Section
      id="permissions"
      title={tr('settings.permissionsSection.permissions')}
      desc={tr('settings.permissionsSection.whatAgentsMayDoWithout')}
    >
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <label htmlFor="perm-subject" className="muted" style={{ fontSize: 13 }}>
          {tr('settings.permissionsSection.rulesFor')}
        </label>
        <select id="perm-subject" className="select" style={{ maxWidth: 320 }} value={subject} onChange={(e) => setSubject(e.target.value)}>
          {subjects.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
      {effQ.isLoading || rulesQ.isLoading ? (
        <Skeleton height={160} />
      ) : effQ.isError ? (
        <div className="banner error">{(effQ.error as Error).message}</div>
      ) : (
        <div className="perm-groups">
          {GROUPS.map((g) => {
            const items = rows.filter((r) => g.prefix.some((p) => r.action.startsWith(p)))
            if (!items.length) return null
            return (
              <fieldset key={g.title} className="perm-group">
                <legend>{g.title}</legend>
                {items.map((r) => {
                  const own = plain.get(r.action)
                  const value = own ? own.effect : 'default'
                  const lockedByScope = r.source.startsWith('missing scope')
                  return (
                    <div key={r.action} className="perm-row">
                      <span className="perm-name" id={`perm-${r.action}`}>
                        {ACTION_LABELS[r.action] ?? r.action}
                      </span>
                      <span className={`perm-effect ${r.effect}`} title={tr('settings.permissionsSection.resolved', { effectText: EFFECT_TEXT[r.effect], source: describeSource(r.source) })}>
                        {EFFECT_TEXT[r.effect]}
                        <span className="muted"> · {describeSource(r.source)}</span>
                      </span>
                      <select
                        className="select sm"
                        aria-labelledby={`perm-${r.action}`}
                        value={value}
                        disabled={lockedByScope || setEffect.isPending}
                        onChange={(e) => setEffect.mutate({ action: r.action, effect: e.target.value as Effect | 'default' })}
                      >
                        <option value="default">{tr('settings.permissionsSection.default', { effectText: EFFECT_TEXT[r.default] })}</option>
                        <option value="allow">{tr('settings.permissionsSection.allow')}</option>
                        <option value="ask">{tr('settings.permissionsSection.askMe')}</option>
                        <option value="deny">{tr('settings.permissionsSection.deny')}</option>
                      </select>
                    </div>
                  )
                })}
              </fieldset>
            )
          })}
        </div>
      )}

      <div className="row" style={{ alignItems: 'center', margin: '16px 0 6px', gap: 8 }}>
        <h3 className="sub-h" style={{ margin: 0, flex: 1 }}>
          {tr('settings.permissionsSection.conditionalRules')}
        </h3>
        <button type="button" className="btn tonal sm" onClick={() => setEditing({ subject, action: 'mail.send', effect: 'allow', constraints: { replies_only: true, known_only: true } })}>
          <Plus />{' '}{tr('settings.permissionsSection.addRule')}
        </button>
      </div>
      {conditional.length === 0 ? (
        <p className="muted" style={{ fontSize: 13 }}>
          {tr('settings.permissionsSection.noneAConditionalRuleApplies')}
        </p>
      ) : (
        <ul className="perm-rules">
          {conditional.map((r) => (
            <li key={r.id}>
              <span className={`perm-effect ${r.effect}`}>{EFFECT_TEXT[r.effect]}</span>
              <span className="grow">
                <b>{ACTION_LABELS[r.action] ?? r.action}</b>
                <span className="muted"> — {describeConstraints(r.constraints ?? {}, labelName)}</span>
                {r.expires_at && <span className="muted">{' '}{tr('settings.permissionsSection.until', { value: new Date(r.expires_at).toLocaleString(appLocale()) })}</span>}
              </span>
              <button type="button" className="icon-btn sm" aria-label={tr('settings.permissionsSection.editRule')} data-tip={tr('common.edit2')} onClick={() => setEditing(r)}>
                <Pencil />
              </button>
              <button type="button" className="icon-btn sm" aria-label={tr('settings.permissionsSection.deleteRule')} data-tip={tr('common.delete2')} onClick={() => remove.mutate(r.id)}>
                <Trash2 />
              </button>
            </li>
          ))}
        </ul>
      )}
      {editing && <RuleDialog rule={editing} classes={(rulesQ.data?.classes ?? []).filter((c) => !isReadClass(c.action))} onClose={() => setEditing(null)} onSaved={done} />}
    </Section>
  )
}

function csv(v: unknown): string {
  return Array.isArray(v) ? v.join(', ') : ''
}

function parseCsv(v: string): string[] {
  return v
    .split(/[,\s]+/)
    .map((x) => x.trim().toLowerCase().replace(/^@/, ''))
    .filter(Boolean)
}

function RuleDialog({ rule, classes, onClose, onSaved }: { rule: Partial<PermissionRule>; classes: { action: string }[]; onClose: () => void; onSaved: () => void }) {
  const labels = useLabelList()
  const calendars = useApiList<{ id: string; name: string; events?: boolean; tasks?: boolean; error?: string }>(['calendars'], '/calendar/calendars')
  const [action, setAction] = useState(rule.action ?? 'mail.send')
  const [effect, setEffect] = useState<Effect>(rule.effect ?? 'allow')
  const c = rule.constraints ?? {}
  const [repliesOnly, setRepliesOnly] = useState(!!c.replies_only)
  const [knownOnly, setKnownOnly] = useState(!!c.known_only)
  const [domains, setDomains] = useState(csv(c.domains))
  const [recipients, setRecipients] = useState(csv(c.recipients))
  const [perHour, setPerHour] = useState(c.max_per_hour ? String(c.max_per_hour) : '')
  const [perDay, setPerDay] = useState(c.max_per_day ? String(c.max_per_day) : '')
  const [cals, setCals] = useState<string[]>(Array.isArray(c.calendars) ? (c.calendars as string[]) : [])
  const [lists, setLists] = useState<string[]>(Array.isArray(c.lists) ? (c.lists as string[]) : [])
  const [labelIds, setLabelIds] = useState<number[]>(Array.isArray(c.labels) ? (c.labels as number[]) : [])
  const [expires, setExpires] = useState(rule.expires_at ? rule.expires_at.slice(0, 16) : '')

  const isSend = action === 'mail.send'
  const isCal = action.startsWith('calendar.')
  const isTask = action.startsWith('tasks.')
  const isLabel = action === 'mail.label'

  const constraints = () => {
    const out: Record<string, unknown> = {}
    if (isSend) {
      if (repliesOnly) out.replies_only = true
      if (knownOnly) out.known_only = true
      if (parseCsv(domains).length) out.domains = parseCsv(domains)
      if (parseCsv(recipients).length) out.recipients = parseCsv(recipients)
      if (Number(perHour) > 0) out.max_per_hour = Number(perHour)
      if (Number(perDay) > 0) out.max_per_day = Number(perDay)
    }
    if (isCal && cals.length) out.calendars = cals
    if (isTask && lists.length) out.lists = lists
    if (isLabel && labelIds.length) out.labels = labelIds
    return out
  }

  const save = useMutation({
    mutationFn: () => {
      const body = { effect, constraints: constraints(), expires_at: expires ? new Date(expires).toISOString() : null }
      return rule.id ? api.patch(`/permissions/${rule.id}`, body) : api.post('/permissions', { ...body, subject: rule.subject, action })
    },
    onSuccess: () => {
      onSaved()
      onClose()
    },
    onError: (e) => toastError(e, tr('settings.permissionsSection.couldNotSaveRule')),
  })

  const calOptions = (calendars.data ?? []).filter((x) => !x.error && x.events)
  const listOptions = (calendars.data ?? []).filter((x) => !x.error && x.tasks)
  const toggle = <T,>(arr: T[], v: T) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v])

  return (
    <Dialog
      open
      onClose={onClose}
      title={rule.id ? tr('settings.permissionsSection.editRule') : tr('settings.permissionsSection.addRule')}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={save.isPending} onClick={() => save.mutate()}>
            {tr('common.save')}
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="rule-action">{tr('settings.permissionsSection.action')}</label>
        <select id="rule-action" className="select" value={action} disabled={!!rule.id} onChange={(e) => setAction(e.target.value)}>
          {classes.map((k) => (
            <option key={k.action} value={k.action}>
              {ACTION_LABELS[k.action] ?? k.action}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="rule-effect">{tr('settings.permissionsSection.effectWhenTheConditionsMatch')}</label>
        <select id="rule-effect" className="select" value={effect} onChange={(e) => setEffect(e.target.value as Effect)}>
          <option value="allow">{tr('settings.permissionsSection.allow')}</option>
          <option value="ask">{tr('settings.permissionsSection.askMe')}</option>
          <option value="deny">{tr('settings.permissionsSection.deny')}</option>
        </select>
      </div>
      {isSend && (
        <>
          <label className="choice">
            <input type="checkbox" className="checkbox" checked={repliesOnly} onChange={(e) => setRepliesOnly(e.target.checked)} />
            <span>{tr('settings.permissionsSection.onlyRepliesInsideAnExisting')}</span>
          </label>
          <label className="choice">
            <input type="checkbox" className="checkbox" checked={knownOnly} onChange={(e) => setKnownOnly(e.target.checked)} />
            <span>{tr('settings.permissionsSection.onlyPeopleIHaveCorresponded')}</span>
          </label>
          <div className="field">
            <label htmlFor="rule-domains">{tr('settings.permissionsSection.recipientDomainsCommaSeparatedSubdomains')}</label>
            <input id="rule-domains" className="input" placeholder={tr('settings.permissionsSection.exampleEduExampleOrg')} value={domains} onChange={(e) => setDomains(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="rule-recipients">{tr('settings.permissionsSection.onlyTheseRecipientsOptional')}</label>
            <input id="rule-recipients" className="input" placeholder={tr('settings.permissionsSection.aliceExampleOrg')} value={recipients} onChange={(e) => setRecipients(e.target.value)} />
          </div>
          <div className="grid-2">
            <div className="field">
              <label htmlFor="rule-hour">{tr('settings.permissionsSection.atMostPerHour')}</label>
              <input id="rule-hour" className="input" type="number" min={0} value={perHour} onChange={(e) => setPerHour(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="rule-day">{tr('settings.permissionsSection.atMostPerDay')}</label>
              <input id="rule-day" className="input" type="number" min={0} value={perDay} onChange={(e) => setPerDay(e.target.value)} />
            </div>
          </div>
        </>
      )}
      {isCal && (
        <fieldset className="field">
          <legend>{tr('settings.permissionsSection.onlyInTheseCalendarsNone')}</legend>
          {calOptions.map((x) => (
            <label key={x.id} className="choice">
              <input type="checkbox" className="checkbox" checked={cals.includes(x.id)} onChange={() => setCals(toggle(cals, x.id))} />
              <span>{x.name}</span>
            </label>
          ))}
        </fieldset>
      )}
      {isTask && (
        <fieldset className="field">
          <legend>{tr('settings.permissionsSection.onlyInTheseTaskLists')}</legend>
          {listOptions.map((x) => (
            <label key={x.id} className="choice">
              <input type="checkbox" className="checkbox" checked={lists.includes(x.id)} onChange={() => setLists(toggle(lists, x.id))} />
              <span>{x.name}</span>
            </label>
          ))}
        </fieldset>
      )}
      {isLabel && (
        <fieldset className="field">
          <legend>{tr('settings.permissionsSection.onlyTheseLabelsNoneAny')}</legend>
          {labels
            .filter((l) => !l.role)
            .map((l) => (
              <label key={l.id} className="choice">
                <input type="checkbox" className="checkbox" checked={labelIds.includes(l.id)} onChange={() => setLabelIds(toggle(labelIds, l.id))} />
                <span>{l.name}</span>
              </label>
            ))}
        </fieldset>
      )}
      <div className="field">
        <label htmlFor="rule-expires">{tr('settings.permissionsSection.expiresOptional')}</label>
        <input id="rule-expires" className="input" type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} />
      </div>
    </Dialog>
  )
}

import { Copy, KeyRound, Lock, Plus, ShieldQuestion } from 'lucide-react'
import { useState } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, asList } from '../../api/client'
import { useApiList, useMe } from '../../api/hooks'
import { ALL_SCOPES, type ApiToken, type AuditEntry, type Connector, type Outbox, type Scope, type SendPolicy } from '../../api/types'
import { Skeleton } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatDateTime, formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { Check, Radio, Section } from './common'
import { ACTION_LABELS, PermissionsSection } from './PermissionsSection'
import { useVisibility } from './useVisibility'
import { localOnlyChoice } from './visibility'
import { ApprovalCard } from '../rail/assistant/ApprovalCard'
import { STATUS_TEXT, actorLabel, historyLine, sessionLabelFor, toRef, useApprovals } from '../approvals/approvals'
import { t as tr, tx, appLocale } from '../../i18n'
import { assistantNameNow } from '../rail/assistant/assistantName'

const SCOPE_DESC: Record<Scope, string> = {
  read: tr('settings.agentsTab.searchAndReadMailPeople'),
  label: tr('settings.agentsTab.addAndRemoveLabelsArchive'),
  draft: tr('settings.agentsTab.createAndEditDrafts'),
  send: tr('settings.agentsTab.sendMailThroughTheSend'),
  delete: tr('settings.agentsTab.deleteAndUnsubscribe'),
  rules: tr('settings.agentsTab.createAndChangeFiltersSettings'),
  calendar: tr('settings.agentsTab.readAndCreateEventsAnd'),
  get memory() { return tr('settings.agentsTab.saveMemoriesPeopleAndSkills', { name: assistantNameNow() }) },
  recordings: tr('settings.agentsTab.readSpeakrRecordingsLinkThem'),
  notes: tr('settings.agentsTab.readAndChangeNotesAnd'),
  profile: tr('settings.agentsTab.yourNameAddressAndPicture'),
  // An agent that reads web pages through mailr can also send what it read to any site.
  web: tr('settings.agentsTab.searchTheWebAndRead'),
  admin: tr('settings.agentsTab.instanceAdministration'),
}

// Settings → Agents & API (groups.ts): approvals and failed messages always show; permissions,
// tokens and the audit log need advanced mode and the API tokens switch of the access policy.

function useAgentTools(): boolean {
  return useVisibility().agentTools
}

export function PermissionsPart() {
  return useAgentTools() ? <PermissionsSection /> : null
}

export function TokensPart() {
  return useAgentTools() ? <TokensSection /> : null
}

export function AuditPart() {
  return useAgentTools() ? <AuditSection /> : null
}

export function ApprovalsSection() {
  const qc = useQueryClient()
  const pending = useApprovals('pending', { refetchInterval: 20_000 })
  const history = useApprovals('approved,executing,executed,denied,expired,failed', { limit: 30 })
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['approvals'] })
    void qc.invalidateQueries({ queryKey: ['outbox'] })
  }
  const list = pending.data ?? []
  return (
    <Section title={tr('common.approvals')} desc={tr('settings.agentsTab.actionsAgentsAskedToTake')}>
      {pending.isLoading ? (
        <Skeleton height={60} />
      ) : list.length === 0 ? (
        <div className="card row">
          <ShieldQuestion size={18} /> <span className="muted">{tr('settings.agentsTab.nothingIsWaitingForApproval')}</span>
        </div>
      ) : (
        <div className="approvals-grid">
          {list.map((p) => (
            <div key={p.id}>
              <div className="approvals-meta">
                {tx('settings.agentsTab.requestedBy', { actorLabel: actorLabel(p), value: p.created_at ? ` · ${formatRelative(p.created_at)}` : '', value2: p.expires_at ? tr('settings.agentsTab.expires', { relative: formatRelative(p.expires_at) }) : '' }, { b: (c) => <b>{c}</b> })}
              </div>
              <ApprovalCard a={toRef(p)} actorName={actorLabel(p)} sessionLabel={sessionLabelFor(p)} onStatus={refresh} onDecided={refresh} />
            </div>
          ))}
        </div>
      )}
      {(history.data ?? []).length > 0 && (
        <>
          <h3 className="sub-h">{tr('settings.agentsTab.recentDecisions')}</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{tr('settings.agentsTab.action')}</th>
                  <th>{tr('settings.agentsTab.by')}</th>
                  <th>{tr('settings.agentsTab.outcome')}</th>
                  <th>{tr('common.when')}</th>
                </tr>
              </thead>
              <tbody>
                {(history.data ?? []).map((p) => (
                  <tr key={p.id}>
                    <td>
                      <b>{p.display?.title || p.preview?.title || ACTION_LABELS[p.action] || p.action}</b>
                      {/* The card's wording, never the raw target (an event uid, a calendar URL). */}
                      {historyLine(p.display) && <div className="muted" style={{ fontSize: 12 }}>{historyLine(p.display)}</div>}
                    </td>
                    <td>{actorLabel(p)}</td>
                    <td>
                      <span className={`status-pill ${p.status === 'executed' ? 'idle' : p.status === 'failed' ? 'error' : ''}`}>{STATUS_TEXT[p.status] ?? p.status}</span>
                      {p.status === 'executed' && p.outcome?.text && <div className="muted" style={{ fontSize: 12 }}>{p.outcome.text.replace(/^Approved · /, '')}</div>}
                      {p.status === 'failed' && p.result && typeof p.result.error === 'string' && <div className="muted" style={{ fontSize: 12 }}>{p.result.error}</div>}
                    </td>
                    <td>{formatRelative(p.executed_at ?? p.decided_at ?? p.created_at ?? '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  )
}

function TokensSection() {
  const qc = useQueryClient()
  const localOnlyAllowed = useVisibility().localOnlyTokens
  const q = useApiList<ApiToken>(['tokens'], '/tokens')
  const [creating, setCreating] = useState(false)
  const [revoking, setRevoking] = useState<ApiToken | null>(null)
  const list = (q.data ?? []).filter((t) => !t.revoked)
  return (
    <Section
      title={tr('settings.agentsTab.apiTokens')}
      desc={
        <>
          {tx('settings.agentsTab.tokensLetAgentsClaudeCode', undefined, { code: (c) => <code>{c}</code> })}
        </>
      }
      actions={
        <button type="button" className="btn filled" onClick={() => setCreating(true)}>
          <Plus />{' '}{tr('settings.agentsTab.createToken')}
        </button>
      }
    >
      {q.isLoading ? (
        <Skeleton height={60} />
      ) : list.length === 0 ? (
        <p className="muted">{tr('settings.agentsTab.noTokensYet')}</p>
      ) : (
        <div className="table-wrap"><table className="table">
          <thead>
            <tr>
              <th>{tr('common.name')}</th>
              <th>{tr('settings.agentsTab.scopes')}</th>
              <th>{tr('settings.agentsTab.sendPolicy')}</th>
              <th>{tr('settings.agentsTab.lastUsed')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.map((t) => (
              <tr key={t.id}>
                <td>
                  <div style={{ fontWeight: 500 }}>
                    {t.name}{' '}
                    {t.local_only && (localOnlyAllowed
                      ? <span className="pill warn"><Lock size={11} />{' '}{tr('common.localOnly')}</span>
                      : <span className="pill" data-tip={tr('settings.agentsTab.yourAdministratorNoLongerAllows')}><Lock size={11} />{' '}{tr('settings.agentsTab.localOnlyOff')}</span>)}
                  </div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {tx('settings.agentsTab.created', { prefix: t.prefix, relative: formatRelative(t.created_at), value: t.expires_at ? tr('settings.agentsTab.expires2', { dateTime: formatDateTime(t.expires_at) }) : '' }, { code: (c) => <code>{c}</code> })}
                  </div>
                </td>
                <td>
                  <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>
                    {t.scopes.map((s) => (
                      <span key={s} className={`pill${s === 'send' || s === 'delete' || s === 'admin' ? ' warn' : ''}`}>
                        {s}
                      </span>
                    ))}
                  </div>
                </td>
                <td style={{ fontSize: 12.5 }}>{t.scopes.includes('send') ? policySummary(t.send_policy) : '—'}</td>
                <td>{t.last_used_at ? formatRelative(t.last_used_at) : 'never'}</td>
                <td>
                  <button type="button" className="btn text sm" onClick={() => setRevoking(t)}>
                    {tr('settings.agentsTab.revoke3')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {creating && <TokenDialog onClose={() => setCreating(false)} />}
      <ConfirmDialog
        open={!!revoking}
        title={tr('settings.agentsTab.revoke', { revokingName: revoking?.name })}
        body={tr('settings.agentsTab.agentsUsingThisTokenLose')}
        confirmLabel={tr('settings.agentsTab.revoke2')}
        danger
        onClose={() => setRevoking(null)}
        onConfirm={() => {
          const t = revoking!
          setRevoking(null)
          api
            .del(`/tokens/${t.id}`)
            .then(() => void qc.invalidateQueries({ queryKey: ['tokens'] }))
            .catch((e) => toastError(e))
        }}
      />
    </Section>
  )
}

function policySummary(p: SendPolicy): string {
  if (!p || p.mode === 'approve_all') return tr('settings.agentsTab.everySendNeedsApproval')
  const parts: string[] = []
  if (p.replies_only) parts.push(tr('settings.agentsTab.repliesOnly'))
  if (p.known_only) parts.push(tr('settings.agentsTab.knownCorrespondents'))
  if (p.domains?.length) parts.push(tr('settings.agentsTab.domains', { domains: p.domains.join(', ') }))
  if (p.max_per_hour) parts.push(`${p.max_per_hour}/h`)
  if (p.max_per_day) parts.push(`${p.max_per_day}/day`)
  return tr('settings.agentsTab.autoSend', { parts: parts.join(', ') || 'any' })
}

function TokenDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const me = useMe().data
  const localOnlyAllowed = useVisibility().localOnlyTokens
  const connectors = useApiList<Connector>(['connectors'], '/connectors')
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<Set<Scope>>(new Set(['read', 'draft']))
  const [allAccounts, setAllAccounts] = useState(true)
  const [accounts, setAccounts] = useState<Set<number>>(new Set())
  const [localOnly, setLocalOnly] = useState(false)
  const [policy, setPolicy] = useState<SendPolicy>({ mode: 'approve_all', replies_only: true, known_only: true, max_per_hour: 5, max_per_day: 20 })
  const [domains, setDomains] = useState('')
  const [expires, setExpires] = useState('')
  const [raw, setRaw] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () =>
      api.post<{ token: string; raw?: string; api_token?: ApiToken }>('/tokens', {
        name: name.trim(),
        scopes: [...scopes],
        connector_ids: allAccounts ? [] : [...accounts],
        local_only: localOnly,
        send_policy: { ...policy, domains: domains.split(/[\s,]+/).filter(Boolean) },
        expires_at: expires ? new Date(`${expires}T23:59:59`).toISOString() : null,
      } as unknown as Record<string, unknown>),
    onSuccess: (r) => {
      setRaw(r.token ?? r.raw ?? '')
      void qc.invalidateQueries({ queryKey: ['tokens'] })
    },
    onError: (e) => toastError(e, tr('settings.agentsTab.tokenNotCreated')),
  })
  const mailConnectors = (connectors.data ?? []).filter((c) => c.kind !== 'caldav')
  const restrictedAccounts = mailConnectors.filter((c) => c.data_class === 'local_only')
  const localOnlyPart = localOnlyChoice(restrictedAccounts.length, localOnlyAllowed)

  if (raw !== null)
    return (
      <Dialog
        open
        onClose={onClose}
        title={tr('settings.agentsTab.tokenCreated')}
        modal
        footer={
          <button type="button" className="btn filled" onClick={onClose}>
            {tr('common.done')}
          </button>
        }
      >
        <div className="stack">
          <p style={{ margin: 0 }}>{tr('settings.agentsTab.copyThisTokenNowIt')}</p>
          <div className="token-box">
            <span style={{ flex: 1 }}>{raw}</span>
            <button type="button" className="icon-btn sm" aria-label={tr('settings.agentsTab.copyToken')} data-tip={tr('common.copy')} onClick={() => void navigator.clipboard.writeText(raw).then(() => toast({ message: tr('settings.agentsTab.tokenCopied') }))}>
              <Copy size={16} />
            </button>
          </div>
          <div className="card" style={{ fontSize: 13 }}>
            <b>{tr('settings.agentsTab.useItFromClaudeCode')}</b>
            <pre style={{ whiteSpace: 'pre-wrap', margin: '8px 0 0', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
              {tr('settings.agentsTab.claudeMcpAddMailrTransport', { origin: window.location.origin, raw: raw.slice(0, 8) })}
            </pre>
          </div>
        </div>
      </Dialog>
    )

  return (
    <Dialog
      open
      onClose={onClose}
      title={tr('settings.agentsTab.createApiToken')}
      wide
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!name.trim() || !scopes.size || create.isPending} onClick={() => create.mutate()}>
            <KeyRound />{' '}{tr('settings.agentsTab.createToken')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <div className="field">
            <label htmlFor="tok-name">{tr('common.name')}</label>
            <input id="tok-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('settings.agentsTab.claudeCodeOnWorkstation')} autoFocus />
          </div>
          <div className="field">
            <label htmlFor="tok-exp">{tr('settings.agentsTab.expiresOptional')}</label>
            <input id="tok-exp" className="input" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
          </div>
        </div>
        <div className="field">
          <span className="field-label">{tr('settings.agentsTab.scopes')}</span>
          <div className="scope-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
            {ALL_SCOPES.filter((s) => s !== 'admin' || me?.user.role === 'admin').map((s) => (
              <Check
                key={s}
                checked={scopes.has(s)}
                onChange={(v) => {
                  const n = new Set(scopes)
                  if (v) n.add(s)
                  else n.delete(s)
                  setScopes(n)
                }}
                label={<b>{s}</b>}
                desc={SCOPE_DESC[s]}
              />
            ))}
          </div>
        </div>
        <div className="field">
          <span className="field-label">{tr('settings.agentsTab.accounts')}</span>
          <Check checked={allAccounts} onChange={setAllAccounts} label={tr('settings.agentsTab.allMyAccounts')} desc={restrictedAccounts.length ? tr('settings.agentsTab.localOnlyAccountsAreStill') : undefined} />
          {!allAccounts && (
            <div className="scope-grid">
              {mailConnectors.map((c) => (
                <Check
                  key={c.id}
                  checked={accounts.has(c.id)}
                  onChange={(v) => {
                    const n = new Set(accounts)
                    if (v) n.add(c.id)
                    else n.delete(c.id)
                    setAccounts(n)
                  }}
                  label={c.name}
                />
              ))}
            </div>
          )}
        </div>
        {localOnlyPart === 'checkbox' && (
          <div className="card" data-testid="token-local-only">
            <Check
              checked={localOnly}
              onChange={setLocalOnly}
              label={
                <b>
                  <Lock size={14} style={{ verticalAlign: -2 }} />{' '}{tr('settings.agentsTab.thisAgentUsesLocalModels')}
                </b>
              }
              desc={tr('settings.agentsTab.requiredToReadTickOnly', { map: restrictedAccounts.map((c) => c.name).join(', ') })}
            />
          </div>
        )}
        {localOnlyPart === 'note' && (
          <p className="muted" data-testid="token-local-only-note" style={{ margin: 0, fontSize: 13 }}>
            <Lock size={13} style={{ verticalAlign: -2 }} />{' '}{tr('settings.agentsTab.agentsCannotReadYourAdministrator', { map: restrictedAccounts.map((c) => c.name).join(', ') })}
          </p>
        )}
        {scopes.has('send') && (
          <div className="card stack">
            <b>{tr('settings.agentsTab.sendGate')}</b>
            <Radio name="pol" value="approve_all" current={policy.mode} onChange={(v) => setPolicy({ ...policy, mode: v })} label={tr('settings.agentsTab.everyMessageWaitsForMy')} desc={tr('settings.agentsTab.recommendedYouApproveEachSend')} />
            <Radio name="pol" value="policy" current={policy.mode} onChange={(v) => setPolicy({ ...policy, mode: v })} label={tr('settings.agentsTab.sendAutomaticallyWithinAPolicy')} desc={tr('settings.agentsTab.anythingOutsideThePolicyStill')} />
            {policy.mode === 'policy' && (
              <div className="stack" style={{ paddingInlineStart: 28 }}>
                <Check checked={!!policy.replies_only} onChange={(v) => setPolicy({ ...policy, replies_only: v })} label={tr('settings.agentsTab.onlyRepliesInsideAnExisting')} />
                <Check checked={!!policy.known_only} onChange={(v) => setPolicy({ ...policy, known_only: v })} label={tr('settings.agentsTab.onlyToPeopleIHave')} />
                <div className="field">
                  <label htmlFor="pol-dom">{tr('settings.agentsTab.onlyToTheseDomainsOptional')}</label>
                  <input id="pol-dom" className="input" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder={tr('settings.agentsTab.exampleComPartnerOrg')} />
                </div>
                <div className="grid-2">
                  <div className="field">
                    <label htmlFor="pol-h">{tr('settings.agentsTab.maxPerHour')}</label>
                    <input id="pol-h" className="input" inputMode="numeric" value={policy.max_per_hour ?? ''} onChange={(e) => setPolicy({ ...policy, max_per_hour: Number(e.target.value.replace(/\D/g, '')) || undefined })} />
                  </div>
                  <div className="field">
                    <label htmlFor="pol-d">{tr('settings.agentsTab.maxPerDay')}</label>
                    <input id="pol-d" className="input" inputMode="numeric" value={policy.max_per_day ?? ''} onChange={(e) => setPolicy({ ...policy, max_per_day: Number(e.target.value.replace(/\D/g, '')) || undefined })} />
                  </div>
                </div>
                <span className="muted" style={{ fontSize: 12.5 }}>
                  {tr('settings.agentsTab.everyAgentSendIsHeld')}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}

export function OutboxSection() {
  const qc = useQueryClient()
  const failed = useApiList<Outbox>(['outbox', 'failed'], '/outbox', { status: 'failed' })
  const list = failed.data ?? []
  if (!list.length) return null
  return (
    <Section title={tr('settings.agentsTab.failedMessages')} desc={tr('settings.agentsTab.theseMessagesCouldNotBe')}>
      <div className="table-wrap"><table className="table">
        <tbody>
          {list.map((o) => (
            <tr key={o.id}>
              <td>
                <b>{o.subject || tr('common.noSubject')}</b>
                <div className="muted" style={{ fontSize: 12 }}>
                  {tr('settings.agentsTab.to', { recipients: o.recipients.join(', ') })}
                </div>
              </td>
              <td style={{ color: 'var(--danger)', fontSize: 12.5 }}>{o.error}</td>
              <td>
                <button
                  type="button"
                  className="btn text sm"
                  onClick={() =>
                    api
                      .post(`/outbox/${o.id}/cancel`)
                      .then(() => void qc.invalidateQueries({ queryKey: ['outbox'] }))
                      .catch((e) => toastError(e))
                  }
                >
                  {tr('settings.agentsTab.dismiss')}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </Section>
  )
}

function AuditSection() {
  const tokens = useApiList<ApiToken>(['tokens'], '/tokens')
  const [tokenId, setTokenId] = useState<string>('')
  const q = useInfiniteQuery({
    queryKey: ['audit', tokenId],
    queryFn: async ({ pageParam }) => {
      const r = await api.get<{ items?: AuditEntry[]; next_cursor?: string | null } | AuditEntry[]>('/audit', { token_id: tokenId || undefined, cursor: pageParam || undefined })
      return Array.isArray(r) ? { items: r, next_cursor: null } : { items: asList<AuditEntry>(r), next_cursor: r.next_cursor ?? null }
    },
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const rows = q.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <Section
      title={tr('settings.agentsTab.auditLog')}
      desc={tr('settings.agentsTab.everyCallMadeWithAn')}
      actions={
        <select className="select" style={{ width: 240 }} value={tokenId} onChange={(e) => setTokenId(e.target.value)} aria-label={tr('settings.agentsTab.filterByToken')}>
          <option value="">{tr('settings.agentsTab.allTokens')}</option>
          {(tokens.data ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      }
    >
      {q.isLoading ? (
        <Skeleton height={80} />
      ) : rows.length === 0 ? (
        <p className="muted">{tr('settings.agentsTab.noAgentActivityYet')}</p>
      ) : (
        <>
          <div className="table-wrap"><table className="table">
            <thead>
              <tr>
                <th>{tr('common.when')}</th>
                <th>{tr('settings.agentsTab.actor')}</th>
                <th>{tr('settings.agentsTab.action')}</th>
                <th>{tr('settings.agentsTab.target')}</th>
                <th>{tr('settings.agentsTab.result')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td style={{ whiteSpace: 'nowrap' }} title={new Date(r.created_at).toLocaleString(appLocale())}>
                    {formatRelative(r.created_at)}
                  </td>
                  <td>{r.actor}</td>
                  <td>
                    <code>{r.action}</code>
                  </td>
                  <td className="ellipsis" style={{ maxWidth: 260 }} title={JSON.stringify(r.inputs)}>
                    {r.target}
                  </td>
                  <td>
                    <span className={`pill ${r.ok ? 'ok' : 'err'}`}>{r.ok ? 'ok' : 'error'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
          {q.hasNextPage && (
            <button type="button" className="btn text" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>
              {tr('settings.agentsTab.loadMore')}
            </button>
          )}
        </>
      )}
    </Section>
  )
}

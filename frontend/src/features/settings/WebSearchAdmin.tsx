import { assistantNameNow } from '../rail/assistant/useAssistantName'
// Settings → Admin → Web search (backend/mailr/websearch/routes.py, site/docs/admin/web-search.md).
// The search services the assistant's web search uses, in the order they are tried: an API service
// (Exa, Brave, Tavily) or a service on the admin's own network (degoog, SearXNG). The API key is
// write-only. "Test" runs one search with what the dialog holds, before or after saving.
// ACT settings.web_search @web.settings

import { Globe, Plus } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useApi } from '../../api/hooks'
import { Skeleton, Switch } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { Check, Radio, Section } from './common'
import { t as tr } from '../../i18n'

export interface WebProvider {
  id: number
  name: string
  purpose?: 'search' | 'reader'
  kind: string
  base_url: string
  has_api_key: boolean
  /** The page reader the server's environment sets up (MAILR_BROWSER_URL): only its route, order, limits and on/off change. */
  from_env?: boolean
  network: 'internet' | 'local'
  use_egress: boolean
  priority: number
  enabled: boolean
  max_results: number
  timeout_s: number
  per_minute: number
  per_day: number
  used_today: number
  last_ok_at: string | null
  last_error: string | null
}

export interface WebKind {
  kind: string
  label: string
  needs_key: boolean
  default_url: string
  default_network: 'internet' | 'local'
  freshness: boolean
}

export interface ReaderKind {
  kind: string
  label: string
  needs_key: boolean
  default_url: string
  default_network: 'internet' | 'local'
  renders: boolean
}

export interface Overview {
  providers: WebProvider[]
  readers?: WebProvider[]
  kinds: WebKind[]
  reader_kinds?: ReaderKind[]
  browser_proxy_set?: boolean
  egress: { configured: boolean; required: boolean }
  private_hosts: 'all' | 'admins' | 'none'
}

export interface TestResult {
  ok: boolean
  error?: string
  via?: 'egress' | 'direct' | 'lan'
  ms: number
  results?: { title: string; url: string; site: string; snippet: string }[]
}

const VIA: Record<string, string> = { egress: tr('settings.webSearchAdmin.throughTheEgressProxy'), direct: 'directly', lan: 'on your network' }
export const KEY = ['admin', 'websearch']

/** How a provider's queries leave the server, in words. */
export function routeText(p: Pick<WebProvider, 'network' | 'use_egress'>, egress: Overview['egress']): string {
  if (p.network === 'local') return tr('settings.webSearchAdmin.onYourNetworkReachedDirectly')
  if (egress.required) return egress.configured ? tr('settings.webSearchAdmin.throughTheEgressProxyRequired') : tr('settings.webSearchAdmin.pausedTheEgressProxyIs')
  if (p.use_egress && egress.configured) return tr('settings.webSearchAdmin.throughTheEgressProxy2')
  return tr('settings.webSearchAdmin.directlyFromTheServer')
}

export function WebSearchSection() {
  const qc = useQueryClient()
  const q = useApi<Overview>(KEY, '/admin/websearch')
  const [editing, setEditing] = useState<Partial<WebProvider> | null>(null)
  const [deleting, setDeleting] = useState<WebProvider | null>(null)
  const [tested, setTested] = useState<{ name: string; r: TestResult } | null>(null)
  const refresh = () => void qc.invalidateQueries({ queryKey: KEY })
  const test = useMutation({
    mutationFn: (p: WebProvider) => api.post<TestResult>(`/admin/websearch/providers/${p.id}/test`, {}).then((r) => ({ name: p.name, r })),
    onSuccess: (t) => {
      setTested(t)
      refresh()
    },
    onError: (e) => toastError(e),
  })
  const data = q.data
  const kinds = data?.kinds ?? []
  const label = (k: string) => kinds.find((x) => x.kind === k)?.label ?? k
  return (
    <Section
      title={tr('settings.webSearchAdmin.webSearch')}
      id="web-search"
      desc={<>{tr('settings.webSearchAdmin.theServicesSearchesTheWeb', { name: assistantNameNow() })}</>}
      actions={
        <button type="button" className="btn filled" onClick={() => setEditing({ kind: '', name: '', base_url: '', network: 'internet', use_egress: true, priority: ((data?.providers.length ?? 0) + 1) * 10, enabled: true, max_results: 10, timeout_s: 10, per_minute: 0, per_day: 0 })}>
          <Plus />{' '}{tr('settings.webSearchAdmin.addProvider')}
        </button>
      }
    >
      {q.isLoading || !data ? (
        <Skeleton height={80} />
      ) : data.providers.length === 0 ? (
        <p className="muted">{tr('settings.webSearchAdmin.noProvidersCannotSearchThe', { name: assistantNameNow() })}</p>
      ) : (
        <div className="table-wrap"><table className="table" data-testid="web-providers">
          <thead>
            <tr>
              <th>{tr('common.order')}</th>
              <th>{tr('settings.webSearchAdmin.provider')}</th>
              <th>{tr('settings.webSearchAdmin.queriesGo')}</th>
              <th>{tr('common.status')}</th>
              <th>{tr('settings.webSearchAdmin.enabled2')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.providers.map((p) => (
              <tr key={p.id}>
                <td>{p.priority}</td>
                <td>
                  <b>{p.name}</b>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {label(p.kind)} · {p.base_url}{p.has_api_key ? tr('settings.webSearchAdmin.keySaved') : ''}
                  </div>
                </td>
                <td style={{ fontSize: 12.5 }}>
                  <span className={`pill ${p.network === 'local' ? 'ok' : 'warn'}`}>{p.network === 'local' ? 'local' : 'internet'}</span>
                  <div className="muted" style={{ fontSize: 12 }}>{routeText(p, data.egress)}</div>
                </td>
                <td style={{ fontSize: 12.5 }}>
                  {p.last_error ? <span className="error">{p.last_error}</span> : p.last_ok_at ? tr('settings.webSearchAdmin.answered', { relative: formatRelative(p.last_ok_at) }) : <span className="muted">{tr('settings.webSearchAdmin.notUsedYet')}</span>}
                  <div className="muted" style={{ fontSize: 12 }}>
                    {p.per_day ? tr('settings.webSearchAdmin.ofToday', { usedToday: p.used_today, perDay: p.per_day }) : tr('settings.webSearchAdmin.today', { usedToday: p.used_today })}{p.per_minute ? tr('settings.webSearchAdmin.atMostAMinute', { perMinute: p.per_minute }) : ''}
                  </div>
                </td>
                <td>
                  <Switch
                    checked={p.enabled}
                    label={tr('settings.webSearchAdmin.enabled', { itemName: p.name })}
                    onChange={(v) =>
                      void api
                        .patch(`/admin/websearch/providers/${p.id}`, { ...bodyOf(p), enabled: v })
                        .then(refresh)
                        .catch((e) => toastError(e))
                    }
                  />
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button type="button" className="btn text sm" disabled={test.isPending} onClick={() => test.mutate(p)}>
                    {tr('settings.webSearchAdmin.test')}
                  </button>
                  <button type="button" className="btn text sm" onClick={() => setEditing(p)}>
                    {tr('common.edit')}
                  </button>
                  <button type="button" className="btn text sm" onClick={() => setDeleting(p)}>
                    {tr('common.delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {tested && <TestView title={tr('settings.webSearchAdmin.testOf', { testedName: tested.name })} r={tested.r} onClose={() => setTested(null)} />}
      {data && (
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          {data.egress.required
            ? tr('settings.webSearchAdmin.theServerRequiresTheEgress')
            : data.egress.configured
              ? tr('settings.webSearchAdmin.anEgressProxyIsSet')
              : tr('settings.webSearchAdmin.noEgressProxyIsSet')}
        </div>
      )}
      {editing && data && <ProviderDialog provider={editing} overview={data} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={tr('settings.webSearchAdmin.delete', { deletingName: deleting?.name })}
        body={tr('settings.webSearchAdmin.itsApiKeyIsDeleted', { name: assistantNameNow() })}
        confirmLabel={tr('common.delete2')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const p = deleting!
          setDeleting(null)
          api.del(`/admin/websearch/providers/${p.id}`).then(refresh).catch((e) => toastError(e))
        }}
      />
    </Section>
  )
}

export function bodyOf(p: Partial<WebProvider>) {
  return {
    name: (p.name ?? '').trim(),
    kind: p.kind,
    base_url: (p.base_url ?? '').trim(),
    network: p.network ?? 'internet',
    use_egress: p.use_egress ?? true,
    priority: Number(p.priority ?? 100),
    enabled: p.enabled ?? true,
    max_results: Number(p.max_results ?? 10),
    timeout_s: Number(p.timeout_s ?? 10),
    per_minute: Number(p.per_minute ?? 0),
    per_day: Number(p.per_day ?? 0),
  }
}

function TestView({ title, r, onClose }: { title: string; r: TestResult; onClose?: () => void }) {
  return (
    <div className={r.ok ? 'banner' : 'banner error'} style={{ marginTop: 12, fontSize: 13, display: 'block' }} data-testid="web-test" role="status">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <b>
          {title}:{' '}
          {r.ok ? tr('settings.webSearchAdmin.resultsInMs', { count: r.results?.length ?? 0, via: VIA[r.via ?? ''] ?? '', ms: r.ms }) : r.error}
        </b>
        {onClose && (
          <button type="button" className="btn text sm" onClick={onClose}>
            {tr('settings.webSearchAdmin.close')}
          </button>
        )}
      </div>
      {r.ok && (
        <ul style={{ margin: '6px 0 0', paddingInlineStart: 18 }}>
          {(r.results ?? []).map((x) => (
            <li key={x.url}>
              {x.title} <span className="muted">· {x.site}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ProviderDialog({ provider, overview, onClose }: { provider: Partial<WebProvider>; overview: Overview; onClose: () => void }) {
  const qc = useQueryClient()
  const [p, setP] = useState<Partial<WebProvider>>(provider)
  const [apiKey, setApiKey] = useState('')
  const [tested, setTested] = useState<TestResult | null>(null)
  const kind = overview.kinds.find((k) => k.kind === p.kind)
  const pick = (k: string) => {
    const meta = overview.kinds.find((x) => x.kind === k)
    setTested(null)
    setP({ ...p, kind: k, name: p.name || meta?.label || '', network: meta?.default_network ?? 'internet', base_url: meta?.default_url ?? '' })
  }
  const save = useMutation({
    mutationFn: () => {
      const body = { ...bodyOf(p), ...(apiKey ? { api_key: apiKey } : {}) }
      return provider.id ? api.patch(`/admin/websearch/providers/${provider.id}`, body) : api.post('/admin/websearch/providers', body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY })
      toast({ message: tr('settings.webSearchAdmin.providerSaved') })
      onClose()
    },
    onError: (e) => toastError(e, tr('common.notSaved')),
  })
  const test = useMutation({
    mutationFn: () =>
      api.post<TestResult>('/admin/websearch/test', {
        id: provider.id ?? null, kind: p.kind, base_url: (p.base_url ?? '').trim(), network: p.network,
        use_egress: p.use_egress ?? true, timeout_s: Number(p.timeout_s ?? 10), ...(apiKey ? { api_key: apiKey } : {}),
      }),
    onSuccess: (r) => setTested(r),
    onError: (e) => setTested({ ok: false, error: (e as Error).message, ms: 0 }),
  })
  const needsKey = !!kind?.needs_key && !apiKey && !(provider.id && provider.has_api_key && provider.kind === p.kind)
  const ready = !!p.kind && !!p.name?.trim() && (!!p.base_url?.trim() || !!kind?.default_url)
  return (
    <Dialog
      open
      onClose={onClose}
      title={provider.id ? tr('settings.webSearchAdmin.editWebSearchProvider') : tr('settings.webSearchAdmin.addWebSearchProvider')}
      wide
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn outlined" disabled={!ready || needsKey || test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? tr('common.testing') : tr('common.test')}
          </button>
          <button type="button" className="btn filled" disabled={!ready || save.isPending} onClick={() => save.mutate()}>
            {tr('common.save')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <div className="field">
            <label htmlFor="ws-kind">{tr('settings.webSearchAdmin.service')}</label>
            <select id="ws-kind" className="select" value={p.kind ?? ''} onChange={(e) => pick(e.target.value)}>
              <option value="" disabled>
                {tr('settings.webSearchAdmin.chooseAService')}
              </option>
              {overview.kinds.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                  {k.needs_key ? tr('settings.webSearchAdmin.apiKey') : k.default_network === 'local' ? ' (self-hosted)' : ''}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ws-name">{tr('common.name')}</label>
            <input id="ws-name" className="input" value={p.name ?? ''} onChange={(e) => setP({ ...p, name: e.target.value })} placeholder={tr('settings.webSearchAdmin.exa')} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="ws-url">{tr('settings.webSearchAdmin.baseUrl', { value: kind?.default_url ? tr('settings.webSearchAdmin.emptyTheServicesOwn') : '' })}</label>
          <input
            id="ws-url"
            className="input"
            value={p.base_url ?? ''}
            onChange={(e) => setP({ ...p, base_url: e.target.value })}
            placeholder={kind?.default_url || 'http://searxng.internal:8080'}
          />
        </div>
        <div className="field">
          <label htmlFor="ws-key">
            {tr('settings.webSearchAdmin.apiKey2', { value: provider.id && provider.has_api_key ? tr('settings.webSearchAdmin.leaveEmptyToKeepThe') : kind?.needs_key ? '' : '(optional)' })}
          </label>
          <input id="ws-key" className="input" type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          {provider.id && provider.has_api_key && <span className="muted" style={{ fontSize: 12 }}>{tr('settings.webSearchAdmin.changingTheUrlOrThe')}</span>}
        </div>
        <div role="radiogroup" aria-label={tr('settings.webSearchAdmin.whereTheServiceRuns')}>
          <Radio name="ws-net" value="internet" current={p.network ?? 'internet'} onChange={(v) => setP({ ...p, network: v })} label={tr('settings.webSearchAdmin.onTheInternet')} desc={tr('settings.webSearchAdmin.anApiServiceSuchAs')} />
          <Radio
            name="ws-net"
            value="local"
            current={p.network ?? 'internet'}
            onChange={(v) => setP({ ...p, network: v })}
            label={tr('settings.webSearchAdmin.onMyNetwork')}
            desc={overview.private_hosts === 'none'
              ? tr('settings.webSearchAdmin.notAvailableThisServerDoes')
              : tr('settings.webSearchAdmin.aSelfHostedServiceSuch')}
          />
        </div>
        {(p.network ?? 'internet') === 'internet' && (
          <Check
            checked={overview.egress.required || (p.use_egress ?? true)}
            disabled={overview.egress.required}
            onChange={(v) => setP({ ...p, use_egress: v })}
            label={tr('settings.webSearchAdmin.sendQueriesThroughTheEgress')}
            desc={overview.egress.required ? tr('settings.webSearchAdmin.requiredByTheServerMailr') : overview.egress.configured ? tr('settings.webSearchAdmin.theProxyHidesThisServers') : tr('settings.webSearchAdmin.countsOnceAnEgressProxy')}
          />
        )}
        <div className="grid-2">
          <NumberField id="ws-priority" label={tr('settings.webSearchAdmin.orderLowerIsTriedFirst')} value={p.priority} min={0} max={10000} onChange={(v) => setP({ ...p, priority: v })} />
          <NumberField id="ws-max" label={tr('settings.webSearchAdmin.resultsPerSearchAtMost')} value={p.max_results} min={1} max={20} onChange={(v) => setP({ ...p, max_results: v })} />
          <NumberField id="ws-timeout" label={tr('settings.webSearchAdmin.timeoutSeconds')} value={p.timeout_s} min={1} max={60} onChange={(v) => setP({ ...p, timeout_s: v })} />
          <NumberField id="ws-minute" label={tr('settings.webSearchAdmin.searchesAMinute0No')} value={p.per_minute} min={0} max={10000} onChange={(v) => setP({ ...p, per_minute: v })} />
          <NumberField id="ws-day" label={tr('settings.webSearchAdmin.searchesADay0No')} value={p.per_day} min={0} max={1000000} onChange={(v) => setP({ ...p, per_day: v })} />
        </div>
        <Check checked={p.enabled ?? true} onChange={(v) => setP({ ...p, enabled: v })} label={tr('settings.webSearchAdmin.enabled2')} />
        {tested && <TestView title={tr('common.test')} r={tested} />}
        {!tested && (
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>
            <Globe size={12} aria-hidden />{' '}{tr('settings.webSearchAdmin.testRunsOneSearchFor')}
          </p>
        )}
      </div>
    </Dialog>
  )
}

export function NumberField({ id, label, value, min, max, onChange }: { id: string; label: string; value: number | undefined; min: number; max: number; onChange: (v: number) => void }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" type="number" min={min} max={max} value={value ?? ''} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  )
}

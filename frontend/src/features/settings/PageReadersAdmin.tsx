import { assistantNameNow } from '../rail/assistant/useAssistantName'
// Settings → Admin → Web search → Page readers (backend/mailr/websearch/readers.py, site/docs/admin/web-search.md).
// How the assistant's read_page gets a page: the built-in reader (no JavaScript) is always first; when it
// fails or finds almost no text, these readers are tried in order. A reader on the internet is a third
// party: it receives the address of each page it reads. Keys are write-only, as for search providers.
// A browserless reader on the admin's network has a Route: through the egress proxy, or directly from this
// network (the admin's explicit choice, allowed even under MAILR_REQUIRE_EGRESS; "direct" badge, audit-logged).
// The reader the server's environment sets up ("Browser (server)", MAILR_BROWSER_URL) has its URL and token
// from there: only its route, order, limits and on/off change, and it cannot be deleted.
// ACT settings.page_readers @web.settings

import { BookOpen, Plus } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useApi } from '../../api/hooks'
import { Skeleton, Switch } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { Check, Radio, Section } from './common'
import { bodyOf, KEY, NumberField, routeText, type Overview, type WebProvider } from './WebSearchAdmin'
import { t as tr } from '../../i18n'

export interface ReaderTest {
  ok: boolean
  error?: string
  via?: 'egress' | 'direct' | 'lan'
  /** How the page itself was fetched. */
  route?: 'egress' | 'direct' | 'third-party'
  status?: number | null
  ms: number
  page?: { title: string; site: string; chars: number; text: string }
}

/** A browserless reader on the admin's network whose pages load directly (the admin's choice). */
export function isDirect(p: Pick<WebProvider, 'kind' | 'network' | 'use_egress'>): boolean {
  return p.kind === 'browserless' && p.network === 'local' && p.use_egress === false
}

/** Where the reader fetches pages from, in words. */
function fetchText(p: Pick<WebProvider, 'kind' | 'network' | 'use_egress'>, o: Overview): string {
  if (p.network === 'internet') return tr('settings.pageReadersAdmin.fromTheServicesAddress')
  if (p.kind !== 'browserless') return o.egress.required ? tr('settings.pageReadersAdmin.notUsedEgressRequired') : tr('settings.pageReadersAdmin.fromYourNetworksAddress')
  if (isDirect(p)) return tr('settings.pageReadersAdmin.browserDirect')
  if (o.egress.configured) return tr('settings.pageReadersAdmin.browserThroughTheEgress')
  return o.egress.required ? tr('settings.pageReadersAdmin.pausedNoEgress') : tr('settings.pageReadersAdmin.browserFromYourNetwork')
}

export function PageReadersSection() {
  const qc = useQueryClient()
  const q = useApi<Overview>(KEY, '/admin/websearch')
  const [editing, setEditing] = useState<Partial<WebProvider> | null>(null)
  const [deleting, setDeleting] = useState<WebProvider | null>(null)
  const [tested, setTested] = useState<{ name: string; r: ReaderTest } | null>(null)
  const refresh = () => void qc.invalidateQueries({ queryKey: KEY })
  const test = useMutation({
    mutationFn: (p: WebProvider) => api.post<ReaderTest>(`/admin/websearch/providers/${p.id}/test`, {}).then((r) => ({ name: p.name, r })),
    onSuccess: (t) => {
      setTested(t)
      refresh()
    },
    onError: (e) => toastError(e),
  })
  const data = q.data
  const rows = data?.readers ?? []
  const label = (k: string) => data?.reader_kinds?.find((x) => x.kind === k)?.label ?? k
  return (
    <Section
      title={tr('settings.pageReadersAdmin.pageReaders')}
      id="page-readers"
      desc={<>{tr('settings.pageReadersAdmin.desc', { name: assistantNameNow() })}</>}
      actions={
        <button type="button" className="btn filled" onClick={() => setEditing({ purpose: 'reader', kind: '', name: '', base_url: '', network: 'local', use_egress: true, priority: (rows.length + 1) * 10, enabled: true, max_results: 10, timeout_s: 30, per_minute: 0, per_day: 0 })}>
          <Plus />{' '}{tr('settings.pageReadersAdmin.addReader')}
        </button>
      }
    >
      {q.isLoading || !data ? (
        <Skeleton height={80} />
      ) : (
        <div className="table-wrap"><table className="table" data-testid="page-readers">
          <thead>
            <tr>
              <th>{tr('common.order')}</th>
              <th>{tr('settings.pageReadersAdmin.reader')}</th>
              <th>{tr('settings.pageReadersAdmin.pagesComeFrom')}</th>
              <th>{tr('common.status')}</th>
              <th>{tr('settings.webSearchAdmin.enabled2')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>—</td>
              <td>
                <b>{tr('settings.pageReadersAdmin.builtIn')}</b>
                <div className="muted" style={{ fontSize: 12 }}>{tr('settings.pageReadersAdmin.builtInDesc')}</div>
              </td>
              <td style={{ fontSize: 12.5 }}>
                <div className="muted" style={{ fontSize: 12 }}>{data.egress.configured ? tr('settings.webSearchAdmin.throughTheEgressProxy2') : data.egress.required ? tr('settings.webSearchAdmin.pausedTheEgressProxyIs') : tr('settings.webSearchAdmin.directlyFromTheServer')}</div>
              </td>
              <td style={{ fontSize: 12.5 }} className="muted">{tr('settings.pageReadersAdmin.alwaysFirst')}</td>
              <td />
              <td />
            </tr>
            {rows.map((p) => (
              <tr key={p.id}>
                <td>{p.priority}</td>
                <td>
                  <b>{p.name}</b>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {label(p.kind)} · {p.base_url}{p.from_env ? '' : p.has_api_key ? tr('settings.webSearchAdmin.keySaved') : ''}
                  </div>
                  {p.from_env && <div className="muted" style={{ fontSize: 12 }}>{tr('settings.pageReadersAdmin.setByTheServer')}</div>}
                </td>
                <td style={{ fontSize: 12.5 }}>
                  <span className={`pill ${p.network === 'local' ? 'ok' : 'warn'}`}>{p.network === 'local' ? tr('settings.pageReadersAdmin.selfHosted') : tr('settings.pageReadersAdmin.thirdParty')}</span>
                  {isDirect(p) && <>{' '}<span className="pill warn" data-testid="reader-direct">{tr('settings.pageReadersAdmin.directBadge')}</span></>}
                  <div className="muted" style={{ fontSize: 12 }}>{fetchText(p, data)}</div>
                  <div className="muted" style={{ fontSize: 12 }}>{tr('settings.pageReadersAdmin.requests', { route: routeText(p, data.egress) })}</div>
                </td>
                <td style={{ fontSize: 12.5 }}>
                  {p.last_error ? <span className="error">{p.last_error}</span> : p.last_ok_at ? tr('settings.pageReadersAdmin.readAPage', { relative: formatRelative(p.last_ok_at) }) : <span className="muted">{tr('settings.webSearchAdmin.notUsedYet')}</span>}
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
                  {!p.from_env && (
                    <button type="button" className="btn text sm" onClick={() => setDeleting(p)}>
                      {tr('common.delete')}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {tested && <ReaderTestView title={tr('settings.webSearchAdmin.testOf', { testedName: tested.name })} r={tested.r} onClose={() => setTested(null)} />}
      {editing && data && <ReaderDialog reader={editing} overview={data} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={tr('settings.webSearchAdmin.delete', { deletingName: deleting?.name })}
        body={tr('settings.pageReadersAdmin.deleteBody')}
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

const VIA: Record<string, string> = { egress: 'settings.webSearchAdmin.throughTheEgressProxy', direct: 'settings.pageReadersAdmin.directly', lan: 'settings.pageReadersAdmin.onYourNetwork' }
// How the page itself was fetched (a test's `route`), when the reader says.
const ROUTE: Record<string, string> = { egress: 'settings.webSearchAdmin.throughTheEgressProxy', direct: 'settings.pageReadersAdmin.directlyFromThisNetwork', 'third-party': 'settings.pageReadersAdmin.byTheReaderService' }

function ReaderTestView({ title, r, onClose }: { title: string; r: ReaderTest; onClose?: () => void }) {
  return (
    <div className={r.ok ? 'banner' : 'banner error'} style={{ marginTop: 12, fontSize: 13, display: 'block' }} data-testid="reader-test" role="status">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <b>
          {title}:{' '}
          {r.ok && r.page ? tr('settings.pageReadersAdmin.testRead', { title: r.page.title, chars: r.page.chars, via: tr(ROUTE[r.route ?? ''] ?? VIA[r.via ?? ''] ?? 'settings.pageReadersAdmin.directly'), ms: r.ms }) : r.error}
        </b>
        {onClose && (
          <button type="button" className="btn text sm" onClick={onClose}>
            {tr('settings.webSearchAdmin.close')}
          </button>
        )}
      </div>
      {r.ok && r.page?.text && <p className="muted" style={{ margin: '6px 0 0', whiteSpace: 'pre-line' }}>{r.page.text}</p>}
    </div>
  )
}

function ReaderDialog({ reader, overview, onClose }: { reader: Partial<WebProvider>; overview: Overview; onClose: () => void }) {
  const qc = useQueryClient()
  const [p, setP] = useState<Partial<WebProvider>>(reader)
  const [apiKey, setApiKey] = useState('')
  const [tested, setTested] = useState<ReaderTest | null>(null)
  const kinds = overview.reader_kinds ?? []
  const kind = kinds.find((k) => k.kind === p.kind)
  const pick = (k: string) => {
    const meta = kinds.find((x) => x.kind === k)
    setTested(null)
    setP({ ...p, kind: k, name: p.name || meta?.label || '', network: meta?.default_network ?? 'internet', base_url: meta?.default_url ?? '' })
  }
  const save = useMutation({
    mutationFn: () => {
      const body = { ...bodyOf(p), purpose: 'reader', ...(apiKey ? { api_key: apiKey } : {}) }
      return reader.id ? api.patch(`/admin/websearch/providers/${reader.id}`, body) : api.post('/admin/websearch/providers', body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY })
      toast({ message: tr('settings.pageReadersAdmin.readerSaved') })
      onClose()
    },
    onError: (e) => toastError(e, tr('common.notSaved')),
  })
  const test = useMutation({
    mutationFn: () =>
      api.post<ReaderTest>('/admin/websearch/test', {
        id: reader.id ?? null, purpose: 'reader', kind: p.kind, base_url: (p.base_url ?? '').trim(), network: p.network,
        use_egress: p.use_egress ?? true, timeout_s: Number(p.timeout_s ?? 30), ...(apiKey ? { api_key: apiKey } : {}),
      }),
    onSuccess: (r) => setTested(r),
    onError: (e) => setTested({ ok: false, error: (e as Error).message, ms: 0 }),
  })
  const fromEnv = !!reader.from_env
  const needsKey = !fromEnv && !!kind?.needs_key && !apiKey && !(reader.id && reader.has_api_key && reader.kind === p.kind)
  const ready = !!p.kind && !!p.name?.trim() && (!!p.base_url?.trim() || !!kind?.default_url)
  const net = p.network ?? 'internet'
  return (
    <Dialog
      open
      onClose={onClose}
      title={reader.id ? tr('settings.pageReadersAdmin.editReader') : tr('settings.pageReadersAdmin.addPageReader')}
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
        {fromEnv && (
          <p className="muted" style={{ fontSize: 13, margin: 0 }} data-testid="reader-from-env">
            {tr('settings.pageReadersAdmin.fromEnvDesc', { url: reader.base_url ?? '' })}
          </p>
        )}
        {!fromEnv && (<>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="pr-kind">{tr('settings.webSearchAdmin.service')}</label>
            <select id="pr-kind" className="select" value={p.kind ?? ''} onChange={(e) => pick(e.target.value)}>
              <option value="" disabled>
                {tr('settings.webSearchAdmin.chooseAService')}
              </option>
              {kinds.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="pr-name">{tr('common.name')}</label>
            <input id="pr-name" className="input" value={p.name ?? ''} onChange={(e) => setP({ ...p, name: e.target.value })} placeholder="browserless" />
          </div>
        </div>
        <div className="field">
          <label htmlFor="pr-url">{tr('settings.webSearchAdmin.baseUrl', { value: kind?.default_url ? tr('settings.webSearchAdmin.emptyTheServicesOwn') : '' })}</label>
          <input id="pr-url" className="input" value={p.base_url ?? ''} onChange={(e) => setP({ ...p, base_url: e.target.value })} placeholder={kind?.default_url || 'http://browserless.internal:3000'} />
        </div>
        <div className="field">
          <label htmlFor="pr-key">
            {tr('settings.webSearchAdmin.apiKey2', { value: reader.id && reader.has_api_key ? tr('settings.webSearchAdmin.leaveEmptyToKeepThe') : kind?.needs_key ? '' : '(optional)' })}
          </label>
          <input id="pr-key" className="input" type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          {p.kind === 'browserless' && <span className="muted" style={{ fontSize: 12 }}>{tr('settings.pageReadersAdmin.browserlessToken')}</span>}
        </div>
        <div role="radiogroup" aria-label={tr('settings.webSearchAdmin.whereTheServiceRuns')}>
          <Radio name="pr-net" value="local" current={net} onChange={(v) => setP({ ...p, network: v })} label={tr('settings.webSearchAdmin.onMyNetwork')}
            desc={overview.private_hosts === 'none' ? tr('settings.webSearchAdmin.notAvailableThisServerDoes') : tr('settings.pageReadersAdmin.localDesc')} />
          <Radio name="pr-net" value="internet" current={net} onChange={(v) => setP({ ...p, network: v })} label={tr('settings.pageReadersAdmin.onTheInternetThirdParty')} desc={tr('settings.pageReadersAdmin.internetDesc')} />
        </div>
        </>)}
        {net === 'internet' && (
          <Check
            checked={overview.egress.required || (p.use_egress ?? true)}
            disabled={overview.egress.required}
            onChange={(v) => setP({ ...p, use_egress: v })}
            label={tr('settings.pageReadersAdmin.sendRequestsThroughTheEgress')}
            desc={overview.egress.required ? tr('settings.webSearchAdmin.requiredByTheServerMailr') : overview.egress.configured ? tr('settings.pageReadersAdmin.hidesFromTheReader') : tr('settings.webSearchAdmin.countsOnceAnEgressProxy')}
          />
        )}
        {net === 'local' && p.kind === 'browserless' && (
          <div role="radiogroup" aria-label={tr('settings.pageReadersAdmin.route')} data-testid="reader-route">
            <b style={{ display: 'block', fontSize: 13, marginBottom: 4 }}>{tr('settings.pageReadersAdmin.route')}</b>
            <Radio name="pr-route" value="egress" current={p.use_egress === false ? 'direct' : 'egress'} onChange={() => setP({ ...p, use_egress: true })}
              label={tr('settings.pageReadersAdmin.routeEgress')}
              desc={overview.egress.configured ? tr('settings.pageReadersAdmin.routeEgressDesc') : overview.egress.required ? tr('settings.pageReadersAdmin.pausedNoEgress') : tr('settings.pageReadersAdmin.routeEgressNone')} />
            <Radio name="pr-route" value="direct" current={p.use_egress === false ? 'direct' : 'egress'} onChange={() => setP({ ...p, use_egress: false })}
              label={tr('settings.pageReadersAdmin.routeDirect')}
              desc={<>{tr('settings.pageReadersAdmin.routeDirectDesc')}{overview.egress.required ? <>{' '}{tr('settings.pageReadersAdmin.routeDirectRequired')}</> : null}</>} />
          </div>
        )}
        {net === 'local' && p.kind && p.kind !== 'browserless' && (
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>{fetchText({ kind: p.kind, network: 'local', use_egress: p.use_egress ?? true }, overview)}</p>
        )}
        <div className="grid-2">
          <NumberField id="pr-priority" label={tr('settings.webSearchAdmin.orderLowerIsTriedFirst')} value={p.priority} min={0} max={10000} onChange={(v) => setP({ ...p, priority: v })} />
          <NumberField id="pr-timeout" label={tr('settings.webSearchAdmin.timeoutSeconds')} value={p.timeout_s} min={1} max={60} onChange={(v) => setP({ ...p, timeout_s: v })} />
          <NumberField id="pr-minute" label={tr('settings.pageReadersAdmin.readsAMinute')} value={p.per_minute} min={0} max={10000} onChange={(v) => setP({ ...p, per_minute: v })} />
          <NumberField id="pr-day" label={tr('settings.pageReadersAdmin.readsADay')} value={p.per_day} min={0} max={1000000} onChange={(v) => setP({ ...p, per_day: v })} />
        </div>
        <Check checked={p.enabled ?? true} onChange={(v) => setP({ ...p, enabled: v })} label={tr('settings.webSearchAdmin.enabled2')} />
        {tested && <ReaderTestView title={tr('common.test')} r={tested} />}
        {!tested && (
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>
            <BookOpen size={12} aria-hidden />{' '}{tr('settings.pageReadersAdmin.testReadsOnePage')}
          </p>
        )}
      </div>
    </Dialog>
  )
}

import { ArrowDown, ArrowUp, FileUp, Play, Plus } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useLiveForm } from '../../lib/live/useLiveForm'
import { ConflictBar, EditingBanner } from '../live/LiveBars'
import { useSearchParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, asList } from '../../api/client'
import { useApiList, useLabelList, qk } from '../../api/hooks'
import type { ConversationSummary, Person, Rule, RuleActions } from '../../api/types'
import { Skeleton, Switch, LoadFailed, failedEmpty } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { formatParticipants } from '../../lib/format'
import { CATEGORIES } from '../../lib/views'
import { toast, toastError } from '../../stores/ui'
import { AdvancedSearchForm } from '../search/AdvancedSearchForm'
import { Check, Section } from './common'
import { useVisibility } from './useVisibility'
import { t, tx, appLocale } from '../../i18n'

function describeActions(a: RuleActions, labelName: (id: number) => string): string {
  const out: string[] = []
  if (a.archive) out.push(t('settings.filtersTab.skipInboxArchive'))
  if (a.mark_read) out.push(t('common.markAsRead'))
  if (a.star) out.push(t('settings.filtersTab.starIt'))
  for (const id of a.add_labels ?? []) out.push(t('settings.filtersTab.applyLabel', { labelName: labelName(id) }))
  for (const id of a.remove_labels ?? []) out.push(t('settings.filtersTab.removeLabel', { labelName: labelName(id) }))
  if (a.forward_to) out.push(t('settings.filtersTab.forwardTo', { forwardTo: a.forward_to }))
  if (a.trash) out.push(t('settings.filtersTab.deleteIt'))
  if (a.spam) out.push(t('settings.filtersTab.sendToSpam'))
  if (a.never_spam) out.push(t('settings.filtersTab.neverSendToSpam'))
  if (a.important === true) out.push(t('settings.filtersTab.alwaysMarkAsImportant'))
  if (a.important === false) out.push(t('settings.filtersTab.neverMarkAsImportant'))
  if (a.category) out.push(t('settings.filtersTab.categoriseAs', { category: a.category }))
  return out.join(', ') || t('settings.filtersTab.noActions')
}

export function FiltersTab() {
  // Simple mode shows only blocked addresses; a filter made from a search still opens its dialog.
  const showFilters = useVisibility().filters
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const rules = useApiList<Rule>(['rules'], '/rules')
  const labels = useLabelList()
  const labelName = (id: number) => labels.find((l) => l.id === id)?.name ?? `#${id}`
  const [editing, setEditing] = useState<Partial<Rule> | null>(() => {
    const q = params.get('new')
    return q !== null ? { query: q, actions: {} } : null
  })
  const [deleting, setDeleting] = useState<Rule | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const done = () => void qc.invalidateQueries({ queryKey: ['rules'] })

  useEffect(() => {
    if (params.get('new') !== null) {
      params.delete('new')
      setParams(params, { replace: true })
    }
  }, [params, setParams])

  const toggle = useMutation({
    mutationFn: (r: Rule) => api.patch(`/rules/${r.id}`, { enabled: !r.enabled }),
    onSuccess: done,
    onError: (e) => toastError(e),
  })
  const reorder = useMutation({
    mutationFn: (ids: number[]) => api.post('/rules/reorder', { ids }),
    onSuccess: done,
    onError: (e) => toastError(e),
  })
  const apply = (r: Rule) =>
    api
      .post<{ affected?: number }>(`/rules/${r.id}/apply`)
      .then((res) => toast({ message: t('settings.filtersTab.filterAppliedToConversations', { affected: res?.affected ?? 'matching' }) }))
      .catch((e) => toastError(e, t('settings.filtersTab.couldNotApplyFilter')))

  const list = (rules.data ?? []).slice().sort((a, b) => a.position - b.position)
  const move = (i: number, d: number) => {
    const ids = list.map((r) => r.id)
    const j = i + d
    if (j < 0 || j >= ids.length) return
    ;[ids[i], ids[j]] = [ids[j], ids[i]]
    reorder.mutate(ids)
  }

  const importGmail = async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    try {
      const r = await api.post<{ imported?: number; forwarding_needs_verification?: string[] }>('/rules/import-gmail', fd)
      done()
      // New labels may have been created for the imported filters.
      void qc.invalidateQueries({ queryKey: qk.labels })
      const fwd = r?.forwarding_needs_verification ?? []
      toast({
        message: fwd.length
          ? t('settings.filtersTab.importedFiltersForwardingToStarts', { count: fwd.length, imported: r?.imported ?? 'the', fwd: fwd.join(', ') })
          : t('settings.filtersTab.importedFiltersFromGmail', { imported: r?.imported ?? 'the' }),
        duration: fwd.length ? 12_000 : undefined,
      })
    } catch (e) {
      toastError(e, t('settings.filtersTab.importFailed'))
    }
  }

  return (
    <>
      {showFilters && <Section
        title={t('settings.filtersTab.filters')}
        desc={t('settings.filtersTab.filtersRunOnIncomingMail')}
        actions={
          <div className="row">
            <button type="button" className="btn outlined" onClick={() => fileRef.current?.click()}>
              <FileUp />{' '}{t('settings.filtersTab.importGmailFilters')}
            </button>
            <button type="button" className="btn filled" onClick={() => setEditing({ query: '', actions: {} })}>
              <Plus />{' '}{t('settings.filtersTab.createANewFilter')}
            </button>
          </div>
        }
      >
        <input ref={fileRef} type="file" accept=".xml,application/xml,text/xml" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void importGmail(f); e.target.value = '' }} />
        {rules.isLoading ? (
          <Skeleton height={80} />
        ) : failedEmpty(rules) ? (
          <LoadFailed what={t('settings.filtersTab.yourFilters')} q={rules} />
        ) : list.length === 0 ? (
          <p className="muted">{t('settings.filtersTab.youHaveNoFiltersCreate')}</p>
        ) : (
          <div className="table-wrap"><table className="table">
            <tbody>
              {list.map((r, i) => (
                <tr key={r.id} style={{ opacity: r.enabled ? 1 : 0.55 }}>
                  <td style={{ width: 56 }}>
                    <Switch checked={r.enabled} onChange={() => toggle.mutate(r)} label={r.enabled ? t('settings.filtersTab.disableFilter') : t('settings.filtersTab.enableFilter')} />
                  </td>
                  <td>
                    {r.name && <div style={{ fontWeight: 500 }}>{r.name}</div>}
                    <div>
                      <span className="muted">{t('settings.filtersTab.matches')}{' '}</span>
                      <b>{r.query}</b>
                    </div>
                    <div>
                      <span className="muted">{t('settings.filtersTab.doThis')}{' '}</span>
                      {describeActions(r.actions, labelName)}
                      {r.stop && <span className="pill" style={{ marginInlineStart: 8 }}>{t('settings.filtersTab.stop')}</span>}
                    </div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {t('settings.filtersTab.matches2', { count: r.hits, value: r.source !== 'user' ? t('settings.filtersTab.from', { source: r.source }) : '' })}
                    </div>
                  </td>
                  <td style={{ width: 280, whiteSpace: 'nowrap' }}>
                    <button type="button" className="icon-btn sm" aria-label={t('common.moveUp')} data-tip={t('common.moveUp')} disabled={i === 0} onClick={() => move(i, -1)}>
                      <ArrowUp size={18} />
                    </button>
                    <button type="button" className="icon-btn sm" aria-label={t('common.moveDown')} data-tip={t('common.moveDown')} disabled={i === list.length - 1} onClick={() => move(i, 1)}>
                      <ArrowDown size={18} />
                    </button>
                    <button type="button" className="icon-btn sm" aria-label={t('settings.filtersTab.applyToExistingMail')} data-tip={t('settings.filtersTab.applyToExistingMail')} onClick={() => void apply(r)}>
                      <Play size={18} />
                    </button>
                    <button type="button" className="btn text sm" onClick={() => setEditing(r)}>
                      {t('common.edit')}
                    </button>
                    <button type="button" className="btn text sm" onClick={() => setDeleting(r)}>
                      {t('common.delete')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Section>}
      <BlockedSection />
      {editing && <RuleDialog rule={editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={t('settings.filtersTab.deleteFilter')}
        body={t('settings.filtersTab.deleteTheFilterMatching', { query: deleting?.query })}
        confirmLabel={t('common.delete2')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const r = deleting!
          setDeleting(null)
          api
            .del(`/rules/${r.id}`)
            .then(done)
            .catch((e) => toastError(e))
        }}
      />
    </>
  )
}

interface RuleForm extends Record<string, unknown> {
  query: string
  name: string
  actions: RuleActions
  stop: boolean
}

function RuleDialog({ rule, onClose }: { rule: Partial<Rule>; onClose: () => void }) {
  const qc = useQueryClient()
  const labels = (useLabelList()).filter((l) => !l.role)
  const [step, setStep] = useState<0 | 1>(rule.id ? 1 : 0)
  // One form object, so a change saved elsewhere can be merged in (lib/live/useLiveForm.ts).
  const [f, setF] = useState<RuleForm>(() => ({ query: rule.query ?? '', name: rule.name ?? '', actions: rule.actions ?? {}, stop: rule.stop ?? false }))
  const { query, name, actions: a, stop } = f
  const setQuery = (v: string) => setF((x) => ({ ...x, query: v }))
  const setName = (v: string) => setF((x) => ({ ...x, name: v }))
  const setA = (v: RuleActions) => setF((x) => ({ ...x, actions: v }))
  const setStop = (v: boolean) => setF((x) => ({ ...x, stop: v }))
  const [applyExisting, setApplyExisting] = useState(false)
  // ACT settings.filter_live_update @web.settings: the filter follows changes the assistant saves meanwhile.
  const live = useLiveForm<RuleForm>({
    kind: 'rule', id: rule.id ?? null, form: f, setForm: setF,
    fromContent: (c) => ({ query: String(c.query ?? ''), name: String(c.name ?? ''), actions: (c.actions as RuleActions) ?? {}, stop: !!c.stop }),
    fields: (x) => ({ name: x.name, query: x.query }),
  })
  const preview = useQuery({
    queryKey: ['rules', 'preview', query],
    queryFn: () => api.post<{ count: number; sample: ConversationSummary[] }>('/rules/preview', { query }),
    enabled: step === 1 && !!query.trim(),
    retry: false,
  })
  const save = useMutation({
    mutationFn: async () => {
      const body = { name: name.trim(), query: query.trim(), actions: a, stop, enabled: rule.enabled ?? true,
        ...(rule.id && live.baseVersion() != null ? { base_version: live.baseVersion() } : {}) }
      const r = rule.id ? await api.patch<Rule>(`/rules/${rule.id}`, body) : await api.post<Rule>('/rules', body)
      if (applyExisting && r?.id) await api.post(`/rules/${r.id}/apply`)
      return r
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['rules'] })
      if (applyExisting) void qc.invalidateQueries({ queryKey: ['conversations'] })
      toast({ message: rule.id ? t('settings.filtersTab.filterUpdated') : t('settings.filtersTab.filterCreated') })
      onClose()
    },
    onError: (e) => {
      if (!live.rejected(e)) toastError(e, t('settings.filtersTab.filterNotSaved'))
    },
  })
  const set = <K extends keyof RuleActions>(k: K, v: RuleActions[K]) => setA({ ...a, [k]: v })
  const hasAction = Object.entries(a).some(([, v]) => (Array.isArray(v) ? v.length > 0 : v !== undefined && v !== null && v !== false && v !== ''))

  return (
    <Dialog
      open
      onClose={onClose}
      title={rule.id ? t('settings.filtersTab.editFilter') : t('settings.filtersTab.createAFilter')}
      wide
      modal
      footer={
        step === 0 ? (
          <>
            <button type="button" className="btn text" onClick={onClose}>
              {t('common.cancel')}
            </button>
            <button type="button" className="btn filled" disabled={!query.trim()} onClick={() => setStep(1)}>
              {t('common.createFilter')}
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn text" onClick={() => setStep(0)}>
              {t('settings.filtersTab.backToSearchOptions')}
            </button>
            <span className="spacer" />
            <button type="button" className="btn text" onClick={onClose}>
              {t('common.cancel')}
            </button>
            <button type="button" className="btn filled" disabled={!hasAction || save.isPending} onClick={() => save.mutate()}>
              {rule.id ? t('settings.filtersTab.updateFilter') : t('common.createFilter')}
            </button>
          </>
        )
      }
    >
      <EditingBanner kind="rule" editing={live.editing} />
      {live.conflict && <ConflictBar kind="rule" conflict={live.conflict} onMine={live.onMine} onTheirs={live.onTheirs} />}
      {step === 0 ? (
        <div className="stack">
          <AdvancedSearchForm initialQuery={query} filterMode onChange={setQuery} />
          <div className="field">
            <label htmlFor="rule-query">{t('settings.filtersTab.searchQuery')}</label>
            <input id="rule-query" className="input" value={query} onChange={(e) => setQuery(e.target.value)} />
            <span className="hint">{t('settings.filtersTab.anyGmailSearchOperatorWorks')}</span>
          </div>
        </div>
      ) : (
        <div className="stack">
          <div className="banner">
            <span>
              {tx('settings.filtersTab.matches3', { query, value: preview.data ? t('settings.filtersTab.existingConversations', { count: preview.data.count }) : preview.isLoading ? ' · counting…' : '' }, { b: (c) => <b>{c}</b> })}
            </span>
          </div>
          {preview.data && preview.data.sample.length > 0 && (
            <div className="card" style={{ padding: '8px 12px', fontSize: 13 }}>
              {preview.data.sample.slice(0, 5).map((c) => (
                <div key={c.id} className="ellipsis">
                  <b>{formatParticipants(c.participants, c.message_count)}</b> — {c.subject}
                </div>
              ))}
            </div>
          )}
          <div className="field">
            <label htmlFor="rule-name">{t('settings.filtersTab.nameOptional')}</label>
            <input id="rule-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <span className="field-label">{t('settings.filtersTab.whenAMessageIsAn')}</span>
          <div className="grid-2">
            <div>
              <Check checked={!!a.archive} onChange={(v) => set('archive', v)} label={t('settings.filtersTab.skipTheInboxArchiveIt')} />
              <Check checked={!!a.mark_read} onChange={(v) => set('mark_read', v)} label={t('common.markAsRead')} />
              <Check checked={!!a.star} onChange={(v) => set('star', v)} label={t('settings.filtersTab.starIt')} />
              <div className="row">
                <Check checked={(a.add_labels ?? []).length > 0} onChange={(v) => set('add_labels', v ? (labels[0] ? [labels[0].id] : []) : [])} label={t('settings.filtersTab.applyTheLabel')} />
                <select className="select" style={{ width: 200 }} value={a.add_labels?.[0] ?? ''} onChange={(e) => set('add_labels', e.target.value ? [Number(e.target.value)] : [])} aria-label={t('settings.filtersTab.labelToApply')}>
                  <option value="">{t('settings.filtersTab.chooseLabel')}</option>
                  {labels.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="row">
                <Check checked={!!a.forward_to} onChange={(v) => set('forward_to', v ? a.forward_to || ' ' : null)} label={t('settings.filtersTab.forwardItTo')} />
                <input className="input" style={{ width: 220 }} type="email" value={(a.forward_to ?? '').trim()} onChange={(e) => set('forward_to', e.target.value || null)} aria-label={t('settings.filtersTab.forwardTo2')} disabled={a.forward_to === null || a.forward_to === undefined} />
              </div>
            </div>
            <div>
              <Check checked={!!a.trash} onChange={(v) => set('trash', v)} label={t('settings.filtersTab.deleteIt')} />
              <Check checked={!!a.spam} onChange={(v) => set('spam', v)} label={t('settings.filtersTab.sendItToSpam')} />
              <Check checked={!!a.never_spam} onChange={(v) => set('never_spam', v)} label={t('settings.filtersTab.neverSendItToSpam')} />
              <Check checked={a.important === true} onChange={(v) => set('important', v ? true : null)} label={t('settings.filtersTab.alwaysMarkItAsImportant')} />
              <Check checked={a.important === false} onChange={(v) => set('important', v ? false : null)} label={t('settings.filtersTab.neverMarkItAsImportant')} />
              <div className="row">
                <Check checked={!!a.category} onChange={(v) => set('category', v ? 'updates' : null)} label={t('settings.filtersTab.categoriseAs2')} />
                <select className="select" style={{ width: 170 }} value={a.category ?? ''} onChange={(e) => set('category', e.target.value || null)} aria-label={t('common.category')}>
                  <option value="">{t('settings.filtersTab.chooseCategory')}</option>
                  {CATEGORIES.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
          <Check checked={stop} onChange={setStop} label={t('settings.filtersTab.stopProcessingLaterFilters')} />
          <Check checked={applyExisting} onChange={setApplyExisting} label={t('settings.filtersTab.alsoApplyFilterToMatching', { value: preview.data ? preview.data.count.toLocaleString(appLocale()) : '' })} />
        </div>
      )}
    </Dialog>
  )
}

function BlockedSection() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['people', 'blocked'], queryFn: async () => asList<Person>(await api.get<unknown>('/people', { blocked: true, limit: 200 })) })
  const [sel, setSel] = useState<Set<string>>(new Set())
  const unblock = async () => {
    for (const e of sel) await api.del(`/people/${encodeURIComponent(e)}/block`).catch((err) => toastError(err))
    setSel(new Set())
    void qc.invalidateQueries({ queryKey: ['people', 'blocked'] })
    toast({ message: t('settings.filtersTab.unblocked') })
  }
  const list = q.data ?? []
  return (
    <Section title={t('settings.filtersTab.blockedAddresses')} desc={t('settings.filtersTab.messagesFromTheseAddressesGo')}>
      {failedEmpty(q) ? (
        <LoadFailed what={t('settings.filtersTab.blockedAddresses2')} q={q} />
      ) : q.isLoading ? (
        <Skeleton height={40} />
      ) : list.length === 0 ? (
        <p className="muted">{t('settings.filtersTab.youHaventBlockedAnyone')}</p>
      ) : (
        <>
          <div className="table-wrap"><table className="table">
            <tbody>
              {list.map((p) => (
                <tr key={p.email}>
                  <td style={{ width: 40 }}>
                    <input type="checkbox" className="checkbox" checked={sel.has(p.email)} onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(p.email); else n.delete(p.email); setSel(n) }} aria-label={t('settings.filtersTab.select', { email: p.email })} />
                  </td>
                  <td>
                    {p.name ? `${p.name} <${p.email}>` : p.email}
                  </td>
                  <td style={{ width: 100 }}>
                    <button type="button" className="btn text sm" onClick={() => { setSel(new Set([p.email])); void api.del(`/people/${encodeURIComponent(p.email)}/block`).then(() => void qc.invalidateQueries({ queryKey: ['people', 'blocked'] })) }}>
                      {t('settings.filtersTab.unblock')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <button type="button" className="btn outlined" style={{ marginTop: 12 }} disabled={!sel.size} onClick={() => void unblock()}>
            {t('settings.filtersTab.unblockSelectedAddresses')}
          </button>
        </>
      )}
    </Section>
  )
}

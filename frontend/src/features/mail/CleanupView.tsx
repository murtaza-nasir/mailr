// Mail → Clean up (backend core/mailgroups.py): a backlog grouped by sender,
// domain, list or category, with archive, label, mark read, move to Trash, a category, a filter
// for future mail, and Keep. Starred, important and replied-to conversations are left alone
// unless included. Each action shows exactly what it changes first; large ones run in the
// background with progress and Undo. The user's sorting model may suggest categories for
// uncategorised groups overnight; a suggestion changes nothing until it is applied here.

import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Check, Eraser, Filter, MailOpen, Shapes, Sparkles, Tag, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api, newIdempotencyKey } from '../../api/client'
import { followBulkResult, invalidateMail, useLabelList, type BulkResult } from '../../api/hooks'
import { EmptyState, failedEmpty, LoadFailed, Spinner } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { formatDateTime } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { PreviewDialog, getPreview, useGridRows, type Includes } from './MailGroupBits'
import {
  actionVerb,
  AGES,
  CATEGORY_NAMES,
  dateSpan,
  DEFAULT_SCOPE,
  GROUP_BY,
  GROUP_SORTS,
  groupLabel,
  groupStats,
  plural,
  scopeQuery,
  WHERE,
  type CleanupAction,
  type CleanupGroup,
  type GroupBy,
  type GroupPage,
  type Scope,
  type Where,
} from './mailgroups'
import { t, tx, appLocale } from '../../i18n'

type Open =
  | { kind: 'action'; action: CleanupAction; group: CleanupGroup; category?: string }
  | { kind: 'rule'; group: CleanupGroup }
  | { kind: 'suggest' }
  | null

// ACT cleanup.groups @web.cleanup
export function CleanupView({ q }: { q: string }) {
  const qc = useQueryClient()
  const [by, setBy] = useState<GroupBy>('sender')
  const [scope, setScope] = useState<Scope>(DEFAULT_SCOPE)
  const [sort, setSort] = useState('count')
  const [search, setSearch] = useState(q)
  const [open, setOpen] = useState<Open>(null)
  const query = { by, sort, q: search, ...scopeQuery(scope) }
  const list = useInfiniteQuery({
    queryKey: ['mailgroups', 'cleanup', query],
    queryFn: ({ pageParam, signal }) => api.get<GroupPage>('/cleanup/groups', { ...query, offset: pageParam, limit: 50 }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
  })
  const items = list.data?.pages.flatMap((p) => p.items) ?? []
  const total = list.data?.pages[0]?.total ?? 0
  const grid = useGridRows(items.length)
  const set = (patch: Partial<Scope>) => setScope((s) => ({ ...s, ...patch }))

  // ACT cleanup.keep @web.cleanup
  const keep = async (g: CleanupGroup) => {
    try {
      await api.post('/cleanup/apply', { by, key: g.key, action: 'keep' })
      toast({ message: t('mail.cleanupView.kept', { groupLabel: groupLabel(by, g) }) })
    } catch (e) {
      toastError(e, t('common.actionFailed'))
    } finally {
      void qc.invalidateQueries({ queryKey: ['mailgroups'] })
    }
  }

  return (
    <div className="mg-view" data-testid="cleanup-view">
      <div className="mg-head">
        <h2>
          {t('common.cleanUp')}
          {total ? <span className="muted"> · {plural(total, 'group')}</span> : null}
        </h2>
        <p className="muted mg-intro">
          {t('mail.cleanupView.clearABacklogAGroup')}
        </p>
        <div className="mg-controls">
          <div className="files-filters" role="radiogroup" aria-label={t('common.groupBy')}>
            {GROUP_BY.map((g) => (
              <button key={g.id} type="button" role="radio" aria-checked={by === g.id} aria-pressed={by === g.id} className="chip" onClick={() => setBy(g.id)}>
                {g.label}
              </button>
            ))}
          </div>
          <div className="mg-row">
            <label className="mg-field">
              <span className="muted">{t('common.mail2')}</span>
              <select className="select" value={scope.where} aria-label={t('mail.cleanupView.whichMail')} onChange={(e) => set({ where: e.target.value as Where })}>
                {WHERE.map((w) => (
                  <option key={w.id} value={w.id}>{w.label}</option>
                ))}
              </select>
            </label>
            <label className="mg-field">
              <span className="muted">{t('mail.cleanupView.age')}</span>
              <select className="select" value={scope.older_than_days} aria-label={t('mail.cleanupView.age')} onChange={(e) => set({ older_than_days: Number(e.target.value) })}>
                {AGES.map((a) => (
                  <option key={a.days} value={a.days}>{a.label}</option>
                ))}
              </select>
            </label>
            <label className="mg-field">
              <span className="muted">{t('common.order')}</span>
              <select className="select" value={sort} aria-label={t('common.order')} onChange={(e) => setSort(e.target.value)}>
                {GROUP_SORTS.map((s) => (
                  <option key={s.id} value={s.id}>{s.label}</option>
                ))}
              </select>
            </label>
            <input className="input mg-search" type="search" placeholder={t('mail.cleanupView.findAGroup')} aria-label={t('mail.cleanupView.findAGroup')} value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="mg-row mg-toggles">
            <label><input type="checkbox" checked={scope.uncategorised} onChange={(e) => set({ uncategorised: e.target.checked })} />{' '}{t('mail.cleanupView.uncategorisedOnly')}</label>
            <label><input type="checkbox" checked={scope.bulk_only} onChange={(e) => set({ bulk_only: e.target.checked })} />{' '}{t('mail.cleanupView.newslettersAndAutomatedMailOnly')}</label>
            <span className="mg-sep" aria-hidden="true" />
            <label><input type="checkbox" checked={scope.include_starred} onChange={(e) => set({ include_starred: e.target.checked })} />{' '}{t('mail.cleanupView.includeStarred')}</label>
            <label><input type="checkbox" checked={scope.include_important} onChange={(e) => set({ include_important: e.target.checked })} />{' '}{t('mail.cleanupView.includeImportant')}</label>
            <label><input type="checkbox" checked={scope.include_replied} onChange={(e) => set({ include_replied: e.target.checked })} />{' '}{t('mail.cleanupView.includeConversationsWithMyReplies')}</label>
            {by !== 'category' && (
              <button type="button" className="btn tonal sm mg-suggest" onClick={() => setOpen({ kind: 'suggest' })}>
                <Sparkles size={16} />{' '}{t('mail.cleanupView.suggestCategories')}
              </button>
            )}
          </div>
        </div>
      </div>
      {failedEmpty(list) ? (
        <LoadFailed what={t('mail.cleanupView.theGroups')} q={list} />
      ) : list.isLoading ? (
        <div className="mg-center"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState icon={<Eraser />} title={t('mail.cleanupView.nothingToCleanUpHere')}>
          <p>{t('mail.cleanupView.noMailMatchesTheseChoices')}</p>
        </EmptyState>
      ) : (
        <div ref={grid.ref} className="mg-grid groups" role="grid" aria-label={t('common.groups')} aria-rowcount={total + 1} onKeyDown={grid.onKeyDown}>
          <div className="mg-gridhead" role="row" aria-rowindex={1}>
            <span role="columnheader">{GROUP_BY.find((g) => g.id === by)?.label}</span>
            <span role="columnheader" className="mg-actions-h">{t('common.actions')}</span>
          </div>
          {items.map((g, i) => (
            <div key={g.key || '(none)'} className={`mg-rowitem${g.reviewed ? ' done' : ''}`} {...grid.rowProps(i)} aria-label={groupLabel(by, g)} data-testid="cleanup-row">
              <div role="gridcell" className="mg-name">
                <span className="mg-title">{groupLabel(by, g)}</span>
                <span className="muted mg-addr">{groupStats(g)}{dateSpan(g.oldest, g.newest) ? ` · ${dateSpan(g.oldest, g.newest)}` : ''}</span>
                <span className="mg-badges">
                  {g.protected > 0 && <span className="mg-badge" title={t('mail.cleanupView.starredImportantOrRepliedTo')}>{t('mail.cleanupView.leftAlone', { protected: g.protected.toLocaleString(appLocale()) })}</span>}
                  {g.in_inbox > 0 && <span className="mg-badge">{t('mail.cleanupView.inInbox', { inInbox: g.in_inbox.toLocaleString(appLocale()) })}</span>}
                  {g.local_only && <span className="mg-badge" title={t('mail.cleanupView.includesMailFromALocal')}>{t('mail.cleanupView.localOnly')}</span>}
                  {g.reviewed && <span className="mg-badge ok">{t('common.kept')}</span>}
                  {g.suggestion && (
                    <button
                      type="button"
                      className="mg-badge suggestion"
                      data-testid="suggestion"
                      title={t('mail.cleanupView.suggestedByFromMessagesNothing', { model: g.suggestion.model ?? t('mail.cleanupView.yourSortingModel'), count: g.suggestion.samples ?? 0 })}
                      onClick={() => setOpen({ kind: 'action', action: 'category', group: g, category: g.suggestion!.category })}
                    >
                      <Sparkles size={14} /> {t('mail.cleanupView.apply', { categoryNames: CATEGORY_NAMES[g.suggestion.category] ?? g.suggestion.category })}
                    </button>
                  )}
                </span>
              </div>
              <div role="gridcell" className="mg-actions">
                {g.in_inbox > 0 && (
                  <button type="button" className="btn text sm" onClick={() => setOpen({ kind: 'action', action: 'archive', group: g })}><Archive size={16} />{' '}{t('common.archive')}</button>
                )}
                <button type="button" className="btn text sm" onClick={() => setOpen({ kind: 'action', action: 'label', group: g })}><Tag size={16} />{' '}{t('common.label')}</button>
                {g.unread > 0 && (
                  <button type="button" className="btn text sm" onClick={() => setOpen({ kind: 'action', action: 'read', group: g })}><MailOpen size={16} />{' '}{t('common.markRead')}</button>
                )}
                {by !== 'category' && (
                  <button type="button" className="btn text sm" onClick={() => setOpen({ kind: 'action', action: 'category', group: g, category: g.suggestion?.category })} title={t('mail.cleanupView.moveTheseConversationsToAn')}>
                    <Shapes size={16} />{' '}{t('common.category')}
                  </button>
                )}
                <button type="button" className="btn text sm" onClick={() => setOpen({ kind: 'action', action: 'trash', group: g })}><Trash2 size={16} />{' '}{t('common.trash')}</button>
                {g.rule_query && (
                  <button type="button" className="btn text sm" onClick={() => setOpen({ kind: 'rule', group: g })}><Filter size={16} />{' '}{t('common.filter')}</button>
                )}
                {!g.reviewed && (
                  <button type="button" className="btn text sm" onClick={() => void keep(g)} title={t('mail.cleanupView.markItReviewedAndKeep')}><Check size={16} />{' '}{t('common.keep')}</button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {list.hasNextPage && (
        <div className="files-more">
          <button type="button" className="btn tonal" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
            {list.isFetchingNextPage ? t('common.loading2') : t('common.showMore')}
          </button>
        </div>
      )}
      {open?.kind === 'action' && <GroupActionDialog by={by} scope={scope} group={open.group} action={open.action} category={open.category} onClose={() => setOpen(null)} />}
      {open?.kind === 'rule' && <RuleDialog by={by} group={open.group} onClose={() => setOpen(null)} />}
      {open?.kind === 'suggest' && <SuggestDialog by={by} scope={scope} onClose={() => setOpen(null)} />}
    </div>
  )
}

// ACT cleanup.archive @web.cleanup, ACT cleanup.label @web.cleanup, ACT cleanup.trash @web.cleanup, ACT cleanup.read @web.cleanup,
// ACT cleanup.category @web.cleanup
function GroupActionDialog({ by, scope, group, action, category, onClose }: {
  by: GroupBy
  scope: Scope
  group: CleanupGroup
  action: CleanupAction
  category?: string
  onClose: () => void
}) {
  const qc = useQueryClient()
  const labels = useLabelList().filter((l) => !l.role)
  const [labelId, setLabelId] = useState<number | null>(labels[0]?.id ?? null)
  const [cat, setCat] = useState(category ?? 'promotions')
  const [includes, setIncludes] = useState<Includes>({
    include_starred: scope.include_starred,
    include_important: scope.include_important,
    include_replied: scope.include_replied,
  })
  const full: Scope = { ...scope, ...includes }
  const name = groupLabel(by, group)
  const extra = action === 'label' ? labels.find((l) => l.id === labelId)?.name : action === 'category' ? cat : undefined
  const verb = actionVerb(action, extra)
  return (
    <PreviewDialog
      title={`${verb}: ${name}`}
      queryKey={['cleanup', by, group.key, full]}
      load={() => getPreview('/cleanup/preview', { by, key: group.key, ...scopeQuery(full) })}
      action={action}
      extra={extra}
      confirmLabel={verb}
      danger={action === 'trash'}
      includes={includes}
      onIncludes={setIncludes}
      onClose={onClose}
      onConfirm={(p) => {
        if (action === 'label' && !labelId) return
        onClose()
        void api
          .post<BulkResult>(
            '/cleanup/apply',
            { by, key: group.key, scope: full, action, expect: p.conversations, label_id: action === 'label' ? labelId : null, category: action === 'category' ? cat : null },
            { idempotencyKey: newIdempotencyKey() },
          )
          .then((res) => followBulkResult(qc, res, p.conversations, { done: (n) => t('mail.cleanupView.conversationsFrom', { verb, count: n, itemName: name }) }))
          .catch((e) => toastError(e, t('mail.cleanupView.cleanUpFailed')))
          .finally(() => invalidateMail(qc))
      }}
    >
      {action === 'label' &&
        (labels.length ? (
          <label className="mg-field">
            <span className="muted">{t('common.label')}</span>
            <select className="select" value={labelId ?? ''} aria-label={t('common.label')} onChange={(e) => setLabelId(Number(e.target.value))}>
              {labels.map((l) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </select>
          </label>
        ) : (
          <p className="muted">{t('mail.cleanupView.makeALabelFirstThe')}</p>
        ))}
      {action === 'category' && (
        <label className="mg-field">
          <span className="muted">{t('common.category')}</span>
          <select className="select" value={cat} aria-label={t('common.category')} onChange={(e) => setCat(e.target.value)}>
            {Object.entries(CATEGORY_NAMES).map(([id, title]) => (
              <option key={id} value={id}>{title}</option>
            ))}
          </select>
        </label>
      )}
    </PreviewDialog>
  )
}

// ACT cleanup.rule @web.cleanup
function RuleDialog({ by, group, onClose }: { by: GroupBy; group: CleanupGroup; onClose: () => void }) {
  const qc = useQueryClient()
  const labels = useLabelList().filter((l) => !l.role)
  const [archive, setArchive] = useState(true)
  const [read, setRead] = useState(false)
  const [labelId, setLabelId] = useState<number | 0>(0)
  const [cat, setCat] = useState('')
  const [busy, setBusy] = useState(false)
  const actions: Record<string, unknown> = {
    ...(archive ? { archive: true } : {}),
    ...(read ? { mark_read: true } : {}),
    ...(labelId ? { add_labels: [labelId] } : {}),
    ...(cat ? { category: cat } : {}),
  }
  const none = Object.keys(actions).length === 0
  const name = groupLabel(by, group)
  const save = async () => {
    setBusy(true)
    try {
      const res = await api.post<{ rule: { id: number } }>('/cleanup/rule', { by, key: group.key, actions })
      const rid = res.rule.id
      toast({
        message: t('mail.cleanupView.filterMadeNewMailFrom', { itemName: name }),
        actionLabel: t('common.undo'),
        onAction: () => void api.del(`/rules/${rid}`).then(() => toast({ message: t('mail.cleanupView.filterRemoved') })).catch((e) => toastError(e, t('common.undoFailed'))),
      })
      onClose()
    } catch (e) {
      toastError(e, t('mail.cleanupView.couldNotMakeTheFilter'))
      setBusy(false)
    } finally {
      void qc.invalidateQueries({ queryKey: ['rules'] })
    }
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('mail.cleanupView.filterForNewMailFrom', { itemName: name })}
      initialFocus="[data-confirm-cancel]"
      footer={
        <>
          <button type="button" className="btn text" data-confirm-cancel onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn filled" data-testid="mg-confirm" disabled={none || busy} onClick={() => void save()}>{t('mail.cleanupView.makeFilter')}</button>
        </>
      }
    >
      <div className="mg-preview">
        <p className="mg-lead">{tx('mail.cleanupView.newMailThatMatchesGets', { ruleQuery: group.rule_query }, { code: (c) => <code>{c}</code> })}</p>
        <fieldset className="mg-includes">
          <legend>{t('common.actions')}</legend>
          <label><input type="checkbox" checked={archive} onChange={(e) => setArchive(e.target.checked)} />{' '}{t('mail.cleanupView.skipTheInboxArchive')}</label>
          <label><input type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />{' '}{t('common.markRead')}</label>
          <label className="mg-field">
            <span className="muted">{t('common.label')}</span>
            <select className="select" value={labelId} aria-label={t('common.label')} onChange={(e) => setLabelId(Number(e.target.value))}>
              <option value={0}>{t('mail.cleanupView.noLabel')}</option>
              {labels.map((l) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </select>
          </label>
          <label className="mg-field">
            <span className="muted">{t('common.category')}</span>
            <select className="select" value={cat} aria-label={t('common.category')} onChange={(e) => setCat(e.target.value)}>
              <option value="">{t('mail.cleanupView.noChange')}</option>
              {Object.entries(CATEGORY_NAMES).map(([id, title]) => (
                <option key={id} value={id}>{title}</option>
              ))}
            </select>
          </label>
        </fieldset>
        <p className="muted">{t('mail.cleanupView.mailYouAlreadyHaveStays')}</p>
      </div>
    </Dialog>
  )
}

// ACT cleanup.suggest @web.cleanup
function SuggestDialog({ by, scope, onClose }: { by: GroupBy; scope: Scope; onClose: () => void }) {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: ['mailgroups', 'suggest-status'],
    queryFn: ({ signal }) => api.get<{ status: string; run_at?: string | null; done?: number | null; total?: number | null; error?: string }>('/cleanup/suggest', undefined, signal),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 4000 : false),
  })
  const [busy, setBusy] = useState(false)
  const st = status.data
  const run = async (now: boolean) => {
    setBusy(true)
    try {
      const res = await api.post<{ run_at: string }>('/cleanup/suggest', { by, scope: { ...scope, uncategorised: true }, now })
      toast({ message: now ? t('mail.cleanupView.suggestionsAreOnTheirWay') : t('mail.cleanupView.suggestionsStartAt', { dateTime: formatDateTime(res.run_at) }) })
      onClose()
    } catch (e) {
      toastError(e, t('mail.cleanupView.couldNotAskForSuggestions'))
      setBusy(false)
    } finally {
      void qc.invalidateQueries({ queryKey: ['mailgroups'] })
    }
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('mail.cleanupView.suggestCategories')}
      initialFocus="[data-confirm-cancel]"
      footer={
        <>
          <button type="button" className="btn text" data-confirm-cancel onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn tonal" disabled={busy} onClick={() => void run(true)}>{t('mail.cleanupView.startNow')}</button>
          <button type="button" className="btn filled" disabled={busy} onClick={() => void run(false)}>{t('common.tonight')}</button>
        </>
      }
    >
      <div className="mg-preview">
        <p className="mg-lead">{t('mail.cleanupView.yourSortingModelIsAsked')}</p>
        <p className="muted">
          {t('mail.cleanupView.onlyTheModelYourAccount')}
        </p>
        {st && st.status !== 'none' && (
          <p className="muted" data-testid="suggest-status">
            {t('mail.cleanupView.lastRequest', { value: st.status === 'queued' ? t('mail.cleanupView.waitingUntil', { dateTime: formatDateTime(st.run_at) }) : st.status === 'running' ? t('mail.cleanupView.runningGroupsRead', { count: st.done ?? 0 }) : st.status === 'failed' ? t('mail.cleanupView.stopped', { error: st.error }) : 'done' })}
          </p>
        )}
      </div>
    </Dialog>
  )
}

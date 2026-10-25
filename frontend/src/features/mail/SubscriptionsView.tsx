// Mail → Subscriptions (backend core/mailgroups.py): one row per mailing list or
// bulk sender, least-read and busiest first, with Unsubscribe, Archive all, Auto-archive, Label and
// Keep. Every action says what it does before it runs, and each has an Undo where the action
// system has one. The same page in every theme (it draws with the shared tokens).

import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, BellOff, Check, Newspaper, Tag } from 'lucide-react'
import { useState } from 'react'
import { api, newIdempotencyKey } from '../../api/client'
import { followBulkResult, invalidateMail, useLabelList, type BulkResult } from '../../api/hooks'
import { EmptyState, failedEmpty, LoadFailed, Spinner } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { PreviewDialog, getPreview, useGridRows, type Includes } from './MailGroupBits'
import {
  canUnsubscribe,
  percent,
  SUB_FILTERS,
  SUB_SORTS,
  subscriptionAddress,
  subscriptionName,
  unsubscribeLabel,
  unsubscribeText,
  type Subscription,
  type SubscriptionPage,
  type UnsubPlan,
} from './mailgroups'
import { t, appLocale } from '../../i18n'

const NO_INCLUDES: Includes = { include_starred: false, include_important: false, include_replied: false }

type Open = { kind: 'unsubscribe' | 'archive' | 'label'; sub: Subscription } | null

// ACT subscription.list @web.subscriptions
export function SubscriptionsView({ q }: { q: string }) {
  const qc = useQueryClient()
  const phone = useMediaQuery('(max-width: 700px)')
  const [filter, setFilter] = useState('all')
  const [sort, setSort] = useState('score')
  const [search, setSearch] = useState(q)
  const [open, setOpen] = useState<Open>(null)
  const list = useInfiniteQuery({
    queryKey: ['mailgroups', 'subscriptions', filter, sort, search],
    queryFn: ({ pageParam, signal }) =>
      api.get<SubscriptionPage>('/subscriptions', { filter, sort, q: search, offset: pageParam, limit: 50 }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    // Older mail's one-click support is being checked in the background: look again shortly, then
    // less often (8 s, 16 s, 32 s, then each minute), since every look refetches each loaded page.
    refetchInterval: (query) => ((query.state.data?.pages[0]?.unknown ?? 0) > 0
      ? Math.min(60_000, 8000 * 2 ** Math.max(0, Math.min(3, query.state.dataUpdateCount - 1))) : false),
  })
  const items = list.data?.pages.flatMap((p) => p.items) ?? []
  const total = list.data?.pages[0]?.total ?? 0
  const grid = useGridRows(items.length)

  const refresh = () => void qc.invalidateQueries({ queryKey: ['mailgroups'] })

  // ACT subscription.auto_archive @web.subscriptions, ACT subscription.keep @web.subscriptions
  const simple = async (sub: Subscription, action: 'auto_archive' | 'remove_auto_archive' | 'keep' | 'unkeep') => {
    try {
      const res = await api.post<{ undo?: { action: string } | null }>('/subscriptions/actions', { key: sub.key, action })
      const name = subscriptionName(sub)
      const message = {
        auto_archive: t('mail.subscriptionsView.newMailFromWillSkip', { itemName: name }),
        remove_auto_archive: t('mail.subscriptionsView.newMailFromWillReach', { itemName: name }),
        keep: t('mail.subscriptionsView.kept', { itemName: name }),
        unkeep: t('mail.subscriptionsView.isBackInTheReview', { itemName: name }),
      }[action]
      const undo = res?.undo
      toast({
        message,
        actionLabel: undo ? t('common.undo') : undefined,
        onAction: undo
          ? () => void api.post('/subscriptions/actions', { key: sub.key, action: undo.action }).then(refresh).catch((e) => toastError(e, t('common.undoFailed')))
          : undefined,
      })
    } catch (e) {
      toastError(e, t('common.actionFailed'))
    } finally {
      refresh()
    }
  }

  return (
    <div className="mg-view" data-testid="subscriptions-view">
      <div className="mg-head">
        <h2>
          {t('common.subscriptions')}
          {total ? <span className="muted"> · {total.toLocaleString(appLocale())}</span> : null}
        </h2>
        <p className="muted mg-intro">{t('mail.subscriptionsView.mailingListsAndSendersOf')}</p>
        <div className="mg-controls">
          <div className="files-filters" role="radiogroup" aria-label={t('common.show')}>
            {SUB_FILTERS.map((f) => (
              <button key={f.id} type="button" role="radio" aria-checked={filter === f.id} className="chip" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
          <div className="mg-row">
            <label className="mg-field">
              <span className="muted">{t('common.order')}</span>
              <select className="select" value={sort} onChange={(e) => setSort(e.target.value)} aria-label={t('common.order')}>
                {SUB_SORTS.map((s) => (
                  <option key={s.id} value={s.id}>{s.label}</option>
                ))}
              </select>
            </label>
            <input className="input mg-search" type="search" placeholder={t('mail.subscriptionsView.findAListOrSender')} aria-label={t('mail.subscriptionsView.findAListOrSender')} value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>
      </div>
      {failedEmpty(list) ? (
        <LoadFailed what={t('mail.subscriptionsView.yourSubscriptions')} q={list} />
      ) : list.isLoading ? (
        <div className="mg-center"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState icon={<Newspaper />} title={t('mail.subscriptionsView.noSubscriptionsHere')}>
          <p>{search || filter !== 'all' ? t('mail.subscriptionsView.nothingMatchesThisView') : t('mail.subscriptionsView.mailFromListsAndNewsletters')}</p>
        </EmptyState>
      ) : (
        <div ref={grid.ref} className="mg-grid subs" role="grid" aria-label={t('common.subscriptions')} aria-rowcount={total + 1} onKeyDown={grid.onKeyDown}>
          {!phone && (
            <div className="mg-gridhead" role="row" aria-rowindex={1}>
              <span role="columnheader">{t('mail.subscriptionsView.subscription')}</span>
              <span role="columnheader" className="num">{t('mail.subscriptionsView.n30Days')}</span>
              <span role="columnheader" className="num">{t('mail.subscriptionsView.n90Days')}</span>
              <span role="columnheader" className="num">{t('common.read')}</span>
              <span role="columnheader">{t('mail.subscriptionsView.lastRead')}</span>
              <span role="columnheader">{t('mail.subscriptionsView.unsubscribe')}</span>
              <span role="columnheader" className="mg-actions-h">{t('common.actions')}</span>
            </div>
          )}
          {items.map((s, i) => (
            <div key={s.key} className={`mg-rowitem${s.unsubscribed_at ? ' done' : ''}`} {...grid.rowProps(i)} aria-label={subscriptionName(s)} data-testid="subscription-row">
              <div role="gridcell" className="mg-name">
                <span className="mg-title">{subscriptionName(s)}</span>
                <span className="muted mg-addr" title={s.subject}>{subscriptionAddress(s)}</span>
                <span className="mg-badges">
                  {s.unsubscribed_at && <span className="mg-badge ok">{t('mail.subscriptionsView.unsubscribed')}</span>}
                  {s.auto_archive_rule_id && <span className="mg-badge">{t('mail.subscriptionsView.autoArchived')}</span>}
                  {s.kept && <span className="mg-badge">{t('common.kept')}</span>}
                  {s.replied && <span className="mg-badge" title={t('mail.subscriptionsView.youRepliedInOneOf')}>{t('mail.subscriptionsView.youReplied')}</span>}
                  {s.local_only && <span className="mg-badge" title={t('mail.subscriptionsView.fromALocalOnlyAccount')}>{t('mail.subscriptionsView.localOnly')}</span>}
                </span>
              </div>
              <div role="gridcell" className="num" data-label={t('mail.subscriptionsView.n30Days')}>{s.last_30.toLocaleString(appLocale())}</div>
              <div role="gridcell" className="num" data-label={t('mail.subscriptionsView.n90Days')} title={t('mail.subscriptionsView.inAll', { total: s.total.toLocaleString(appLocale()) })}>{s.last_90.toLocaleString(appLocale())}</div>
              <div role="gridcell" className="num" data-label={t('common.read')} title={t('mail.subscriptionsView.ofRead', { read: s.read.toLocaleString(appLocale()), total: s.total.toLocaleString(appLocale()) })}>{percent(s.open_rate)}</div>
              <div role="gridcell" data-label={t('mail.subscriptionsView.lastRead')}>{s.last_read ? formatRelative(s.last_read) : <span className="muted">{t('common.never')}</span>}</div>
              <div role="gridcell" data-label={t('mail.subscriptionsView.unsubscribe')}><span className={`mg-badge method ${s.unsubscribe ?? 'nolink'}`}>{unsubscribeLabel(s.unsubscribe)}</span></div>
              <div role="gridcell" className="mg-actions">
                {canUnsubscribe(s.unsubscribe) && !s.unsubscribed_at && (
                  <button type="button" className="btn tonal sm" onClick={() => setOpen({ kind: 'unsubscribe', sub: s })}>
                    <BellOff size={16} />{' '}{t('mail.subscriptionsView.unsubscribe')}
                  </button>
                )}
                {s.in_inbox > 0 && (
                  <button type="button" className="btn text sm" onClick={() => setOpen({ kind: 'archive', sub: s })} title={t('mail.subscriptionsView.archiveTheConversationsInYour', { count: s.in_inbox })}>
                    <Archive size={16} />{' '}{t('mail.subscriptionsView.archiveAll')}
                  </button>
                )}
                {s.auto_archive_rule_id ? (
                  <button type="button" className="btn text sm" onClick={() => void simple(s, 'remove_auto_archive')}>{t('mail.subscriptionsView.stopAutoArchive')}</button>
                ) : (
                  <button type="button" className="btn text sm" onClick={() => void simple(s, 'auto_archive')} title={t('mail.subscriptionsView.newMailSkipsTheInbox')}>{t('mail.subscriptionsView.autoArchive')}</button>
                )}
                <button type="button" className="btn text sm" onClick={() => setOpen({ kind: 'label', sub: s })}>
                  <Tag size={16} />{' '}{t('common.label')}
                </button>
                {s.kept ? (
                  <button type="button" className="btn text sm" onClick={() => void simple(s, 'unkeep')}>{t('mail.subscriptionsView.reviewAgain')}</button>
                ) : (
                  <button type="button" className="btn text sm" onClick={() => void simple(s, 'keep')} title={t('mail.subscriptionsView.markItReviewedAndKeep')}>
                    <Check size={16} />{' '}{t('common.keep')}
                  </button>
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
      {open?.kind === 'unsubscribe' && <UnsubscribeDialog sub={open.sub} onClose={() => setOpen(null)} onArchive={() => setOpen({ kind: 'archive', sub: open.sub })} onAutoArchive={() => { setOpen(null); void simple(open.sub, 'auto_archive') }} />}
      {open?.kind === 'archive' && <SubActionDialog sub={open.sub} action="archive_all" onClose={() => setOpen(null)} />}
      {open?.kind === 'label' && <SubActionDialog sub={open.sub} action="label" onClose={() => setOpen(null)} />}
    </div>
  )
}

// ACT subscription.unsubscribe @web.subscriptions
function UnsubscribeDialog({ sub, onClose, onArchive, onAutoArchive }: { sub: Subscription; onClose: () => void; onArchive: () => void; onAutoArchive: () => void }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  // How it would unsubscribe (read from the newest message), shown before anything is sent.
  const planQ = useQuery({
    queryKey: ['mailgroups', 'unsubscribe-plan', sub.key],
    queryFn: ({ signal }) => api.get<UnsubPlan>('/subscriptions/unsubscribe', { key: sub.key }, signal),
    staleTime: 0,
  })
  const plan = planQ.data ?? null
  const name = subscriptionName(sub)
  const run = async () => {
    setBusy(true)
    try {
      const res = await api.post<{ ok?: boolean; error?: string; method?: string }>('/subscriptions/unsubscribe', { key: sub.key }, { idempotencyKey: newIdempotencyKey() })
      if (res?.ok) {
        toast({ message: t('mail.subscriptionsView.unsubscribedFrom', { itemName: name }), actionLabel: sub.in_inbox ? t('mail.subscriptionsView.archiveItsMail') : undefined, onAction: sub.in_inbox ? onArchive : undefined })
        onClose()
      } else {
        toast({ message: t('mail.subscriptionsView.couldNotUnsubscribeFrom', { itemName: name, error: res?.error ?? t('mail.subscriptionsView.theListDidNotAccept') }), error: true })
        setBusy(false)
      }
    } catch (e) {
      toastError(e, t('mail.subscriptionsView.unsubscribeFailed'))
      setBusy(false)
    } finally {
      void qc.invalidateQueries({ queryKey: ['mailgroups'] })
    }
  }
  const acts = plan?.method === 'one_click' || plan?.method === 'mailto'
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('mail.subscriptionsView.unsubscribeFrom', { itemName: name })}
      initialFocus="[data-confirm-cancel]"
      footer={
        <>
          <button type="button" className="btn text" data-confirm-cancel onClick={onClose}>{t('common.cancel')}</button>
          {plan && !acts && (
            <button type="button" className="btn tonal" onClick={onAutoArchive}>{t('mail.subscriptionsView.autoArchiveInstead')}</button>
          )}
          {plan?.method === 'link' && plan.url && (
            <a className="btn filled" href={plan.url} target="_blank" rel="noopener noreferrer" onClick={onClose}>{t('mail.subscriptionsView.openThePage')}</a>
          )}
          {acts && (
            <button type="button" className="btn filled" data-testid="mg-confirm" disabled={busy} onClick={() => void run()}>{t('mail.subscriptionsView.unsubscribe')}</button>
          )}
        </>
      }
    >
      {planQ.isError ? (
        <p role="alert">{t('mail.subscriptionsView.couldntCheckTheUnsubscribeLink')}{' '}<button type="button" className="btn text sm" onClick={() => void planQ.refetch()}>{t('common.retry')}</button></p>
      ) : plan ? (
        <div className="mg-preview">
          <p className="mg-lead">{unsubscribeText(plan)}</p>
          <p className="muted">{t('mail.subscriptionsView.itsMailStaysWhereIt')}</p>
        </div>
      ) : (
        <div className="mg-center"><Spinner /></div>
      )}
    </Dialog>
  )
}

// ACT subscription.archive_all @web.subscriptions, ACT subscription.label @web.subscriptions
function SubActionDialog({ sub, action, onClose }: { sub: Subscription; action: 'archive_all' | 'label'; onClose: () => void }) {
  const qc = useQueryClient()
  const labels = useLabelList().filter((l) => !l.role)
  const [labelId, setLabelId] = useState<number | null>(labels[0]?.id ?? null)
  const [includes, setIncludes] = useState<Includes>(NO_INCLUDES)
  const name = subscriptionName(sub)
  const label = labels.find((l) => l.id === labelId)
  return (
    <PreviewDialog
      title={action === 'archive_all' ? t('mail.subscriptionsView.archiveMailFrom', { itemName: name }) : t('mail.subscriptionsView.labelMailFrom', { itemName: name })}
      queryKey={['subscription', sub.key, action]}
      load={() => getPreview('/subscriptions/preview', { key: sub.key, action, ...includes })}
      action={action === 'archive_all' ? 'archive_all' : 'label_sub'}
      extra={label?.name}
      confirmLabel={action === 'archive_all' ? t('common.archive') : t('common.label')}
      includes={includes}
      onIncludes={setIncludes}
      onClose={onClose}
      onConfirm={(p) => {
        if (action === 'label' && !labelId) return
        onClose()
        void api
          .post<BulkResult>('/subscriptions/actions', { key: sub.key, action, label_id: labelId, ...includes }, { idempotencyKey: newIdempotencyKey() })
          .then((res) => followBulkResult(qc, res, p.conversations, { done: (n) => (action === 'archive_all' ? t('mail.subscriptionsView.archivedConversationsFrom', { count: n, itemName: name }) : t('mail.subscriptionsView.labelledConversationsFrom', { count: n, itemName: name })) }))
          .catch((e) => toastError(e, t('common.actionFailed')))
          .finally(() => invalidateMail(qc))
      }}
    >
      {action === 'label' &&
        (labels.length ? (
          <label className="mg-field">
            <span className="muted">{t('common.label')}</span>
            <select className="select" value={labelId ?? ''} onChange={(e) => setLabelId(Number(e.target.value))} aria-label={t('common.label')}>
              {labels.map((l) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </select>
          </label>
        ) : (
          <p className="muted">{t('mail.subscriptionsView.makeALabelFirstThe')}</p>
        ))}
    </PreviewDialog>
  )
}

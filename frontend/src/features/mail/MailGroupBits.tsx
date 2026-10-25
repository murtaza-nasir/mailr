// Parts shared by the Subscriptions and Clean up pages (SubscriptionsView, CleanupView):
// the grid of group rows (one tab stop, arrow keys between rows, as the mail list's RowGrid) and
// the preview dialog that shows exactly what an action changes before it runs.

import { useQuery } from '@tanstack/react-query'
import { useCallback, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { Spinner } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { mailPath } from '../../lib/views'
import { previewLines, type CleanupAction, type Preview } from './mailgroups'
import { t, appLocale } from '../../i18n'

/** Roving focus for a grid of rows: one tab stop (the focused row), ↑/↓/Home/End move it. */
export function useGridRows(count: number) {
  const [active, setActive] = useState(0)
  const ref = useRef<HTMLDivElement>(null)
  const focusRow = useCallback((i: number) => {
    const rows = ref.current?.querySelectorAll<HTMLElement>('[role="row"][data-group-row]')
    if (!rows?.length) return
    const n = Math.max(0, Math.min(rows.length - 1, i))
    setActive(n)
    rows[n].focus({ preventScroll: false })
  }, [])
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const row = (e.target as HTMLElement).closest<HTMLElement>('[data-group-row]')
    if (!row || e.target !== row) return
    const i = Number(row.dataset.index ?? 0)
    const keys: Record<string, number> = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: count - 1 }
    if (e.key in keys) {
      e.preventDefault()
      e.stopPropagation()
      focusRow(keys[e.key])
    }
  }
  const rowProps = (i: number) => ({
    role: 'row' as const,
    'data-group-row': '',
    'data-index': i,
    'aria-rowindex': i + 2, // the header row is 1
    tabIndex: i === Math.min(active, Math.max(0, count - 1)) ? 0 : -1,
    onFocus: () => setActive(i),
  })
  return { ref, onKeyDown, rowProps }
}

export interface Includes {
  include_starred: boolean
  include_important: boolean
  include_replied: boolean
}

/** The preview of one action on one group: what changes, 20 samples, what is left alone (with
 *  switches to include it), and how it runs. Nothing changes until the confirm button. */
export function PreviewDialog({ title, queryKey, load, action, extra, confirmLabel, danger, includes, onIncludes, onConfirm, onClose, children }: {
  title: string
  queryKey: unknown[]
  load: () => Promise<Preview>
  action: CleanupAction | 'archive_all' | 'label_sub'
  extra?: string
  confirmLabel: string
  danger?: boolean
  includes: Includes
  onIncludes: (v: Includes) => void
  onConfirm: (p: Preview) => void
  onClose: () => void
  /** Extra controls above the preview (a label or category choice). */
  children?: ReactNode
}) {
  const nav = useNavigate()
  const q = useQuery({ queryKey: ['mailgroups', 'preview', ...queryKey, includes], queryFn: load, staleTime: 0 })
  const p = q.data
  const [busy, setBusy] = useState(false)
  const lines = p ? previewLines(p, action, extra) : []
  const any = p && (p.protected.starred || p.protected.important || p.protected.replied || includes.include_starred || includes.include_important || includes.include_replied)
  return (
    <Dialog
      open
      wide
      onClose={onClose}
      title={title}
      initialFocus="[data-confirm-cancel]"
      footer={
        <>
          <button type="button" className="btn text" data-confirm-cancel onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className={`btn ${danger ? 'danger' : 'filled'}`}
            data-testid="mg-confirm"
            disabled={!p || p.conversations === 0 || busy || q.isFetching}
            onClick={() => {
              if (!p) return
              setBusy(true)
              onConfirm(p)
            }}
          >
            {p && p.conversations ? `${confirmLabel} ${p.conversations.toLocaleString(appLocale())}` : confirmLabel}
          </button>
        </>
      }
    >
      <div className="mg-preview" data-testid="mg-preview">
        {children}
        {q.isLoading ? (
          <div className="mg-center"><Spinner /></div>
        ) : q.isError ? (
          <p role="alert">{t('mail.mailGroupBits.couldntLoadThePreview')}{' '}<button type="button" className="btn text sm" onClick={() => void q.refetch()}>{t('common.retry')}</button></p>
        ) : p ? (
          <>
            {lines.map((l, i) => (
              <p key={i} className={i === 0 ? 'mg-lead' : 'muted'}>{l}</p>
            ))}
            {any ? (
              <fieldset className="mg-includes">
                <legend>{t('mail.mailGroupBits.alsoInclude')}</legend>
                <label><input type="checkbox" checked={includes.include_starred} onChange={(e) => onIncludes({ ...includes, include_starred: e.target.checked })} />{' '}{t('common.starred')}</label>
                <label><input type="checkbox" checked={includes.include_important} onChange={(e) => onIncludes({ ...includes, include_important: e.target.checked })} />{' '}{t('common.important')}</label>
                <label><input type="checkbox" checked={includes.include_replied} onChange={(e) => onIncludes({ ...includes, include_replied: e.target.checked })} />{' '}{t('mail.mailGroupBits.withMyReplies')}</label>
              </fieldset>
            ) : null}
            {p.samples.length > 0 && (
              <>
                <h3 className="mg-sub">{p.conversations > p.samples.length ? t('mail.mailGroupBits.theNewestOfThem', { samplesLength: p.samples.length }) : t('mail.mailGroupBits.allOfThem')}</h3>
                <ul className="mg-samples" aria-label={t('mail.mailGroupBits.sampleConversations')}>
                  {p.samples.map((s) => (
                    <li key={s.conversation_id}>
                      <button type="button" className="mg-sample" onClick={() => nav(mailPath('all', s.conversation_id))} title={t('mail.mailGroupBits.openThisConversation')}>
                        <span className={`mg-sample-subj${s.unread ? ' unread' : ''}`} dir="auto">{s.subject || t('common.noSubject')}</span>
                        <span className="mg-sample-meta muted">{s.from_name || s.from_email} · {formatRelative(s.date)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        ) : null}
      </div>
    </Dialog>
  )
}

/** GET a preview: the shape of the backend's core/mailgroups.preview. */
export function getPreview(path: string, query: Record<string, string | number | boolean>): Promise<Preview> {
  return api.get<Preview>(path, query)
}
